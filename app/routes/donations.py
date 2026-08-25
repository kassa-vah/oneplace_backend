import csv
import io
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, g, Response

from app.extensions import db
from app.utils.decorators import require_admin
from app.utils.pagination import paginate_query
from app.models.cause import Cause, CauseStatus
from app.models.donation import (
    Donor,
    Donation,
    DonationStatus,
    PaymentTransaction,
    PaymentWebhookEvent,
)
from app.models.admin import record_audit
from app.services.payments import get_payment_provider
from app.services.email import email_service

# --- Public: initiate a donation --------------------------------------

donations_bp = Blueprint("donations", __name__)


@donations_bp.post("/api/donations")
def create_donation():
    """
    Creates a donation and immediately attempts payment via the
    configured provider (only 'test' exists today — spec #102). This is
    milestone #101 item 15: donation records in a controlled/test state,
    built *before* any real payment provider.

    Duplicate-submission protection (spec #61/#96): if the client sends
    the same idempotency_key twice, the second call returns the
    existing donation instead of creating a new one.
    """
    payload = request.get_json(silent=True) or {}

    cause_id = payload.get("cause_id")
    email = payload.get("email")
    amount = payload.get("amount")
    idempotency_key = payload.get("idempotency_key")

    if not cause_id or not email or amount is None:
        return jsonify({"error": "cause_id, email, and amount are required"}), 400

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be a number"}), 400
    if amount <= 0:
        return jsonify({"error": "amount must be greater than 0"}), 400

    cause = Cause.query.filter_by(id=cause_id, status=CauseStatus.PUBLISHED).first()
    if cause is None:
        return jsonify({"error": "Cause not found"}), 404

    if idempotency_key:
        existing = Donation.query.filter_by(idempotency_key=idempotency_key).first()
        if existing is not None:
            return jsonify(existing.to_public_receipt_dict()), 200

    donor = Donor.find_or_create(
        email=email,
        first_name=payload.get("first_name"),
        last_name=payload.get("last_name"),
        phone=payload.get("phone"),
        country=payload.get("country"),
    )

    donation = Donation(
        donor_id=donor.id,
        cause_id=cause.id,
        amount=amount,
        currency=payload.get("currency", cause.currency),
        is_anonymous=bool(payload.get("is_anonymous", False)),
        donor_message=payload.get("donor_message"),
        idempotency_key=idempotency_key,
        payment_provider=payload.get("provider", "test"),
    )
    db.session.add(donation)
    db.session.flush()

    provider = get_payment_provider(donation.payment_provider)
    result = provider.initiate_payment(
        amount=donation.amount, currency=donation.currency, donation_id=donation.id
    )

    transaction = PaymentTransaction(
        donation_id=donation.id,
        provider=donation.payment_provider,
        provider_transaction_id=result.provider_transaction_id,
        provider_reference=result.provider_reference,
        amount=donation.amount,
        currency=donation.currency,
        status=result.status,
    )
    db.session.add(transaction)

    if result.status == "successful":
        donation.transition_to(DonationStatus.SUCCESSFUL)
    elif result.status == "failed":
        donation.transition_to(DonationStatus.FAILED)
    # otherwise it stays pending until a webhook confirms it

    db.session.commit()

    if donation.status == DonationStatus.SUCCESSFUL:
        email_service.send_donation_receipt(donor, donation)

    return jsonify(donation.to_public_receipt_dict()), 201


@donations_bp.post("/api/donations/webhook/<string:provider>")
def donation_webhook(provider):
    """
    Stub webhook receiver. No real provider is integrated yet, but the
    idempotency pattern is built now (spec #61/#95) so it doesn't need
    retrofitting later: the provider+event_id pair is unique, so a
    duplicate delivery is detected and short-circuited before touching
    any donation.
    """
    payload = request.get_json(silent=True) or {}
    event_id = payload.get("event_id")
    event_type = payload.get("event_type")

    if not event_id:
        return jsonify({"error": "event_id is required"}), 400

    existing_event = PaymentWebhookEvent.query.filter_by(
        provider=provider, event_id=event_id
    ).first()
    if existing_event is not None:
        # Already processed (or in flight) — return success without
        # duplicating any data, per spec #61's explicit flow.
        return jsonify({"status": "already_processed"}), 200

    event = PaymentWebhookEvent(
        provider=provider, event_id=event_id, event_type=event_type
    )
    db.session.add(event)
    db.session.flush()

    # Real provider-specific handling (verify signature, look up the
    # PaymentTransaction by provider_transaction_id, apply the donation
    # status transition) goes here once a real provider is integrated.
    event.processed = True
    event.processed_at = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({"status": "recorded"}), 200


# --- Admin -------------------------------------------------------------




@donations_bp.get("/api/admin/donations")
@require_admin
def list_donations_admin():
    query = Donation.query

    status = request.args.get("status")
    if status:
        if status not in DonationStatus.ALL:
            return jsonify({"error": f"Invalid status filter '{status}'"}), 400
        query = query.filter_by(status=status)

    cause_id = request.args.get("cause_id")
    if cause_id:
        query = query.filter_by(cause_id=cause_id)

    query = query.order_by(Donation.created_at.desc())
    result = paginate_query(query)

    return jsonify(
        {
            "items": [d.to_admin_dict() for d in result["items"]],
            "pagination": result["pagination"],
        }
    )


@donations_bp.get("/api/admin/donations/export")
@require_admin
def export_donations():
    """CSV export (spec #75). Filtered the same way as the list endpoint;
    only fields an authorized admin should see."""
    query = Donation.query

    status = request.args.get("status")
    if status:
        if status not in DonationStatus.ALL:
            return jsonify({"error": f"Invalid status filter '{status}'"}), 400
        query = query.filter_by(status=status)

    cause_id = request.args.get("cause_id")
    if cause_id:
        query = query.filter_by(cause_id=cause_id)

    donations = query.order_by(Donation.created_at.desc()).limit(10000).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["id", "donor_id", "cause_id", "amount", "currency", "status", "created_at"]
    )
    for d in donations:
        writer.writerow(
            [d.id, d.donor_id, d.cause_id, d.amount, d.currency, d.status, d.created_at.isoformat()]
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


@donations_bp.post("/api/admin/donations/<string:donation_id>/refund")
@require_admin
def refund_donation(donation_id):
    donation = Donation.query.get(donation_id)
    if donation is None:
        return jsonify({"error": "Donation not found"}), 404

    payload = request.get_json(silent=True) or {}

    if not donation.transition_to(DonationStatus.REFUNDED):
        return jsonify(
            {"error": f"Cannot transition donation from '{donation.status}' to 'refunded'"}
        ), 400

    donation.refund_reason = payload.get("reason")
    donation.refund_reference = payload.get("reference")
    donation.refunded_at = datetime.now(timezone.utc)

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_refunded_donation",
        resource_type="donation",
        resource_id=donation.id,
        description=payload.get("reason"),
    )
    db.session.commit()

    email_service.send_refund_confirmation(donation.donor, donation)

    return jsonify(donation.to_admin_dict())
