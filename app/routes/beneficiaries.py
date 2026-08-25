from flask import Blueprint, jsonify, request, g

from app.extensions import db
from app.utils.decorators import require_admin
from app.utils.pagination import paginate_query
from app.models.beneficiary import Beneficiary, BeneficiaryStatus
from app.models.admin import record_audit

beneficiaries_bp = Blueprint("beneficiaries", __name__)


# --- Public ---------------------------------------------------------

@beneficiaries_bp.get("/api/beneficiaries")
def list_beneficiaries():
    query = Beneficiary.query.filter_by(status=BeneficiaryStatus.ACTIVE).order_by(
        Beneficiary.created_at.desc()
    )
    result = paginate_query(query)
    return jsonify(
        {
            "items": [b.to_public_dict() for b in result["items"]],
            "pagination": result["pagination"],
        }
    )


@beneficiaries_bp.get("/api/beneficiaries/<string:beneficiary_id>")
def get_beneficiary(beneficiary_id):
    beneficiary = Beneficiary.query.filter_by(
        id=beneficiary_id, status=BeneficiaryStatus.ACTIVE
    ).first()
    if beneficiary is None:
        return jsonify({"error": "Beneficiary not found"}), 404
    return jsonify(beneficiary.to_public_dict())


# --- Admin ------------------------------------------------------------

@beneficiaries_bp.get("/api/admin/beneficiaries")
@require_admin
def list_beneficiaries_admin():
    query = Beneficiary.query

    status = request.args.get("status")
    if status:
        if status not in BeneficiaryStatus.ALL:
            return jsonify({"error": f"Invalid status filter '{status}'"}), 400
        query = query.filter_by(status=status)

    query = query.order_by(Beneficiary.created_at.desc())
    result = paginate_query(query)
    return jsonify(
        {
            "items": [b.to_admin_dict() for b in result["items"]],
            "pagination": result["pagination"],
        }
    )


@beneficiaries_bp.post("/api/admin/beneficiaries")
@require_admin
def create_beneficiary():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    beneficiary = Beneficiary(
        name=name,
        description=payload.get("description"),
        photo_url=payload.get("photo_url"),
    )
    db.session.add(beneficiary)
    db.session.flush()

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_created_beneficiary",
        resource_type="beneficiary",
        resource_id=beneficiary.id,
        description=f"Created beneficiary '{beneficiary.name}'",
    )
    db.session.commit()
    return jsonify(beneficiary.to_admin_dict()), 201


@beneficiaries_bp.patch("/api/admin/beneficiaries/<string:beneficiary_id>")
@require_admin
def update_beneficiary(beneficiary_id):
    beneficiary = Beneficiary.query.get(beneficiary_id)
    if beneficiary is None:
        return jsonify({"error": "Beneficiary not found"}), 404

    payload = request.get_json(silent=True) or {}
    for field in ("name", "description", "photo_url"):
        if field in payload:
            setattr(beneficiary, field, payload[field])

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_updated_beneficiary",
        resource_type="beneficiary",
        resource_id=beneficiary.id,
    )
    db.session.commit()
    return jsonify(beneficiary.to_admin_dict())


@beneficiaries_bp.post("/api/admin/beneficiaries/<string:beneficiary_id>/archive")
@require_admin
def archive_beneficiary(beneficiary_id):
    """Soft-archive only — causes may still reference this beneficiary
    historically (same principle as Cause archiving, spec #82)."""
    beneficiary = Beneficiary.query.get(beneficiary_id)
    if beneficiary is None:
        return jsonify({"error": "Beneficiary not found"}), 404

    beneficiary.archive()

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_archived_beneficiary",
        resource_type="beneficiary",
        resource_id=beneficiary.id,
    )
    db.session.commit()
    return jsonify(beneficiary.to_admin_dict())
