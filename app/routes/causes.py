from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, g

from app.extensions import db
from app.utils.decorators import require_admin
from app.utils.pagination import paginate_query
from app.models.cause import Cause, CauseStatus, slugify
from app.models.beneficiary import Beneficiary
from app.models.admin import record_audit

# One blueprint for the whole Cause resource. Public and admin routes
# live side by side, told apart by path (/api/causes vs
# /api/admin/causes) and by the @require_admin decorator — not by
# separate Blueprint objects, which was unnecessary indirection for
# what is, underneath, one resource.
causes_bp = Blueprint("causes", __name__)


# --- Public ---------------------------------------------------------

@causes_bp.get("/api/causes")
def list_causes():
    """Public listing — only published causes are ever visible here
    (spec #84). Draft/archived causes never leak through this endpoint."""
    query = Cause.query.filter_by(status=CauseStatus.PUBLISHED)

    featured_param = request.args.get("featured")
    if featured_param is not None:
        query = query.filter_by(featured=featured_param.lower() == "true")

    query = query.order_by(Cause.display_order.asc(), Cause.created_at.desc())

    result = paginate_query(query)
    return jsonify(
        {
            "items": [c.to_public_dict() for c in result["items"]],
            "pagination": result["pagination"],
        }
    )


@causes_bp.get("/api/causes/<string:slug>")
def get_cause(slug):
    cause = Cause.query.filter_by(slug=slug, status=CauseStatus.PUBLISHED).first()
    if cause is None:
        return jsonify({"error": "Cause not found"}), 404
    return jsonify(cause.to_public_dict())


# --- Admin — same resource, privileged view + mutations ---------------

@causes_bp.get("/api/admin/causes")
@require_admin
def list_causes_admin():
    """Admins see every cause regardless of status — draft/archived included."""
    query = Cause.query

    status = request.args.get("status")
    if status:
        if status not in CauseStatus.ALL:
            return jsonify({"error": f"Invalid status filter '{status}'"}), 400
        query = query.filter_by(status=status)

    query = query.order_by(Cause.display_order.asc(), Cause.created_at.desc())
    result = paginate_query(query)

    return jsonify(
        {
            "items": [c.to_admin_dict() for c in result["items"]],
            "pagination": result["pagination"],
        }
    )


@causes_bp.post("/api/admin/causes")
@require_admin
def create_cause():
    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()

    if not title:
        return jsonify({"error": "title is required"}), 400

    beneficiary_id = payload.get("beneficiary_id")
    if beneficiary_id and Beneficiary.query.get(beneficiary_id) is None:
        return jsonify({"error": "beneficiary_id does not exist"}), 400

    base_slug = slugify(title)
    slug = base_slug
    suffix = 2
    # Slugs must be unique (spec #83) — append a numeric suffix on collision
    # rather than failing the request outright.
    while Cause.query.filter_by(slug=slug).first() is not None:
        slug = f"{base_slug}-{suffix}"
        suffix += 1

    cause = Cause(
        title=title,
        slug=slug,
        description=payload.get("description"),
        beneficiary_id=beneficiary_id,
        cover_image_url=payload.get("cover_image_url"),
        goal_amount=payload.get("goal_amount"),
        currency=payload.get("currency", "KES"),
        status=CauseStatus.DRAFT,
        featured=bool(payload.get("featured", False)),
        display_order=int(payload.get("display_order", 0)),
    )
    db.session.add(cause)
    db.session.flush()  # get cause.id before the audit row references it

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_created_cause",
        resource_type="cause",
        resource_id=cause.id,
        description=f"Created cause '{cause.title}'",
    )
    db.session.commit()

    return jsonify(cause.to_admin_dict()), 201


@causes_bp.patch("/api/admin/causes/<string:cause_id>")
@require_admin
def update_cause(cause_id):
    cause = Cause.query.get(cause_id)
    if cause is None:
        return jsonify({"error": "Cause not found"}), 404

    payload = request.get_json(silent=True) or {}

    if "beneficiary_id" in payload:
        beneficiary_id = payload["beneficiary_id"]
        if beneficiary_id and Beneficiary.query.get(beneficiary_id) is None:
            return jsonify({"error": "beneficiary_id does not exist"}), 400

    editable_fields = (
        "title",
        "description",
        "beneficiary_id",
        "cover_image_url",
        "goal_amount",
        "currency",
        "featured",
        "display_order",
    )
    for field in editable_fields:
        if field in payload:
            setattr(cause, field, payload[field])

    # Status changes go through dedicated endpoints (publish/archive) rather
    # than this generic patch, so a status transition always gets an
    # explicit audit entry with context.
    if "status" in payload:
        return jsonify(
            {"error": "Use the publish/archive endpoints to change cause status"}
        ), 400

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_updated_cause",
        resource_type="cause",
        resource_id=cause.id,
        description=f"Updated cause '{cause.title}'",
    )
    db.session.commit()

    return jsonify(cause.to_admin_dict())


@causes_bp.post("/api/admin/causes/<string:cause_id>/publish")
@require_admin
def publish_cause(cause_id):
    cause = Cause.query.get(cause_id)
    if cause is None:
        return jsonify({"error": "Cause not found"}), 404

    cause.status = CauseStatus.PUBLISHED
    cause.archived_at = None

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_published_cause",
        resource_type="cause",
        resource_id=cause.id,
    )
    db.session.commit()
    return jsonify(cause.to_admin_dict())


@causes_bp.post("/api/admin/causes/<string:cause_id>/archive")
@require_admin
def archive_cause(cause_id):
    """Archiving never deletes the row — historical donations may still
    reference this cause (spec #82)."""
    cause = Cause.query.get(cause_id)
    if cause is None:
        return jsonify({"error": "Cause not found"}), 404

    cause.status = CauseStatus.ARCHIVED
    cause.archived_at = datetime.now(timezone.utc)

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_archived_cause",
        resource_type="cause",
        resource_id=cause.id,
    )
    db.session.commit()
    return jsonify(cause.to_admin_dict())
