# ============================================================
# FILE BELONGS AT:  app/routes/donations.py
# ============================================================
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, g, Response

from app.extensions import db
from app.utils.decorators import require_admin, require_firebase_auth
from app.utils.pagination import paginate_query
from app.models.cause import Cause
from app.models.donation import Donor, Donation, DonationStatus
from app.models.admin import record_audit

donations_bp = Blueprint("donations", __name__)


@donations_bp.post("/api/donors/consent")
@require_firebase_auth
def record_donor_consent():
    """
    Fired right after Firebase sign-up/sign-in — this is now the ONLY
    reason a member of the public registers at all (recurring giving
    is handled entirely by SwipeSimple, outside this backend), so it
    doubles as the "you're now a registration a superadmin can see and
    promote" touchpoint. Body: { agreed: true, consent_version? }.
    """
    payload = request.get_json(silent=True) or {}
    email = g.firebase_user.get("email")

    if not email:
        return jsonify({"error": "Firebase account has no email on record"}), 400
    if not payload.get("agreed"):
        return jsonify({"error": "agreed must be true"}), 400

    donor = Donor.find_or_create_registered(firebase_uid=g.firebase_user["uid"], email=email)
    donor.consent_accepted_at = datetime.now(timezone.utc)
    donor.consent_version = payload.get("consent_version", "v1")
    db.session.commit()

    return jsonify({"status": "recorded", "consent_version": donor.consent_version}), 201


# --- Admin: manual donation bookkeeping ---------------------------------
# SwipeSimple (no API) is where the actual payment happens — these
# routes exist purely so admins have their own reporting/export,
# independent of whatever SwipeSimple's own dashboard shows. Nothing
# here talks to a payment provider; there isn't one to talk to.

def _apply_donation_filters(query):
    status = request.args.get("status")
    if status:
        if status not in DonationStatus.ALL:
            return None, ({"error": f"Invalid status filter '{status}'"}, 400)
        query = query.filter_by(status=status)

    cause_id = request.args.get("cause_id")
    if cause_id:
        query = query.filter_by(cause_id=cause_id)

    method = request.args.get("method")
    if method:
        query = query.filter_by(method=method)

    return query, None


@donations_bp.get("/api/admin/donations")
@require_admin
def list_donations_admin():
    query, error = _apply_donation_filters(Donation.query)
    if error:
        return jsonify(error[0]), error[1]

    query = query.order_by(Donation.created_at.desc())
    result = paginate_query(query)

    return jsonify(
        {
            "items": [d.to_admin_dict() for d in result["items"]],
            "pagination": result["pagination"],
        }
    )


@donations_bp.post("/api/admin/donations")
@require_admin
def create_donation_admin():
    """An admin logs a donation that already happened on SwipeSimple —
    this never creates a pending/in-progress record, because by the
    time anyone types it in here, the money has already moved."""
    payload = request.get_json(silent=True) or {}

    cause_id = payload.get("cause_id")
    amount = payload.get("amount")

    if not cause_id or amount is None:
        return jsonify({"error": "cause_id and amount are required"}), 400

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be a number"}), 400
    if amount <= 0:
        return jsonify({"error": "amount must be greater than 0"}), 400

    cause = Cause.query.get(cause_id)
    if cause is None:
        return jsonify({"error": "Cause not found"}), 404

    occurred_at = None
    if payload.get("occurred_at"):
        try:
            occurred_at = datetime.fromisoformat(payload["occurred_at"])
        except ValueError:
            return jsonify({"error": "occurred_at must be an ISO 8601 datetime"}), 400

    donation = Donation(
        cause_id=cause.id,
        donor_name=payload.get("donor_name"),
        donor_email=payload.get("donor_email"),
        amount=amount,
        currency=payload.get("currency", "USD"),
        method=payload.get("method", "SwipeSimple"),
        is_anonymous=bool(payload.get("is_anonymous", False)),
        note=payload.get("note"),
        recorded_by_admin_id=g.admin_user.id,
        occurred_at=occurred_at or datetime.now(timezone.utc),
    )
    db.session.add(donation)
    db.session.flush()

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_recorded_donation",
        resource_type="donation",
        resource_id=donation.id,
        description=f"Logged {donation.amount} {donation.currency} for cause '{cause.title}'",
    )
    db.session.commit()

    return jsonify(donation.to_admin_dict()), 201


@donations_bp.patch("/api/admin/donations/<string:donation_id>")
@require_admin
def update_donation_admin(donation_id):
    """Correcting a manually-entered record — a typo'd amount, a fixed
    donor name, etc. Not for changing status; use the refund endpoint
    for that, so it's always audited with a reason."""
    donation = Donation.query.get(donation_id)
    if donation is None:
        return jsonify({"error": "Donation not found"}), 404

    payload = request.get_json(silent=True) or {}

    if "status" in payload:
        return jsonify({"error": "Use the refund endpoint to change status"}), 400

    editable_fields = ("donor_name", "donor_email", "amount", "currency", "method", "is_anonymous", "note", "cause_id")
    for field in editable_fields:
        if field in payload:
            setattr(donation, field, payload[field])

    if "occurred_at" in payload and payload["occurred_at"]:
        try:
            donation.occurred_at = datetime.fromisoformat(payload["occurred_at"])
        except ValueError:
            return jsonify({"error": "occurred_at must be an ISO 8601 datetime"}), 400

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_updated_donation",
        resource_type="donation",
        resource_id=donation.id,
    )
    db.session.commit()

    return jsonify(donation.to_admin_dict())


@donations_bp.post("/api/admin/donations/<string:donation_id>/refund")
@require_admin
def refund_donation_admin(donation_id):
    donation = Donation.query.get(donation_id)
    if donation is None:
        return jsonify({"error": "Donation not found"}), 404
    if donation.status == DonationStatus.REFUNDED:
        return jsonify({"error": "Already marked as refunded"}), 400

    payload = request.get_json(silent=True) or {}
    donation.mark_refunded(reason=payload.get("reason"))

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_refunded_donation",
        resource_type="donation",
        resource_id=donation.id,
        description=payload.get("reason"),
    )
    db.session.commit()

    return jsonify(donation.to_admin_dict())


@donations_bp.get("/api/admin/donations/export")
@require_admin
def export_donations():
    query, error = _apply_donation_filters(Donation.query)
    if error:
        return jsonify(error[0]), error[1]

    donations = query.order_by(Donation.created_at.desc()).limit(10000).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id", "cause_id", "donor_name", "donor_email", "amount", "currency",
            "method", "status", "is_anonymous", "occurred_at", "created_at",
        ]
    )
    for d in donations:
        writer.writerow(
            [
                d.id, d.cause_id,
                "" if d.is_anonymous else (d.donor_name or ""),
                "" if d.is_anonymous else (d.donor_email or ""),
                d.amount, d.currency, d.method or "", d.status, d.is_anonymous,
                d.occurred_at.isoformat() if d.occurred_at else "",
                d.created_at.isoformat(),
            ]
        )

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_exported_donations",
        resource_type="donation",
        description=f"Exported {len(donations)} donation record(s)",
    )
    db.session.commit()

    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=donations.csv"},
    )