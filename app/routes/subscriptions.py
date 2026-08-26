from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, g, current_app

from app.extensions import db
from app.utils.decorators import require_firebase_auth, require_admin
from app.utils.pagination import paginate_query
from app.models.cause import Cause, CauseStatus
from app.models.donation import Donor, Donation, DonationStatus, PaymentTransaction
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.admin import record_audit
from app.services.email import email_service
from app.services.recaptcha import verify_recaptcha


subscriptions_bp = Blueprint("subscriptions", __name__)


@subscriptions_bp.post("/api/subscriptions")
@require_firebase_auth
def create_subscription():
    """
    Starts a recurring giving commitment. Unlike a one-time donation,
    this REQUIRES the donor to be signed in via Firebase — registering
    links the Donor record to their Firebase account so they can later
    view/cancel their own subscription (see /api/subscriptions/me
    below). Registering here does not grant any admin access — that's
    an entirely separate flow, and there is no self-service way to
    request it (see admins.py — only a superadmin can promote someone).

    Also requires reCAPTCHA + privacy-policy consent, verified/recorded
    here rather than in a separate round trip, since the frontend flow
    is: sign in -> "choose your subscription" -> consent form with
    reCAPTCHA -> this call.
    """
    payload = request.get_json(silent=True) or {}

    cause_id = payload.get("cause_id")
    amount = payload.get("amount")
    email = g.firebase_user.get("email")
    recaptcha_token = payload.get("recaptcha_token")
    consent_version = payload.get("consent_version")

    if not email:
        return jsonify({"error": "Firebase account has no email on record"}), 400
    if not cause_id or amount is None:
        return jsonify({"error": "cause_id and amount are required"}), 400
    if not consent_version:
        return jsonify({"error": "consent_version is required (accept the privacy policy first)"}), 400

    if not current_app.config.get("SKIP_RECAPTCHA"):
        if not verify_recaptcha(recaptcha_token, current_app.config.get("RECAPTCHA_SECRET_KEY"), request.remote_addr):
            return jsonify({"error": "reCAPTCHA verification failed"}), 400

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be a number"}), 400
    if amount <= 0:
        return jsonify({"error": "amount must be greater than 0"}), 400

    cause = Cause.query.filter_by(id=cause_id, status=CauseStatus.PUBLISHED).first()
    if cause is None:
        return jsonify({"error": "Cause not found"}), 404

    donor = Donor.find_or_create_registered(
        firebase_uid=g.firebase_user["uid"],
        email=email,
        first_name=payload.get("first_name"),
        last_name=payload.get("last_name"),
        phone=payload.get("phone"),
        country=payload.get("country"),
    )
    donor.consent_accepted_at = datetime.now(timezone.utc)
    donor.consent_version = consent_version

    subscription = Subscription(
        donor_id=donor.id,
        cause_id=cause.id,
        amount=amount,
        currency=payload.get("currency", cause.currency),
        interval=payload.get("interval", "monthly"),
        provider=payload.get("provider", "test"),
        status=SubscriptionStatus.ACTIVE,
        started_at=datetime.now(timezone.utc),
    )
    db.session.add(subscription)
    db.session.commit()

    email_service.send_subscription_confirmation(donor, subscription)

    return jsonify(subscription.to_admin_dict()), 201


@subscriptions_bp.get("/api/subscriptions/me")
@require_firebase_auth
def list_my_subscriptions():
    """A registered donor's own subscriptions — no admin access needed,
    just proof of being the donor who owns them."""
    donor = Donor.query.filter_by(firebase_uid=g.firebase_user["uid"]).first()
    if donor is None:
        return jsonify({"items": []})

    subs = (
        Subscription.query.filter_by(donor_id=donor.id)
        .order_by(Subscription.created_at.desc())
        .all()
    )
    return jsonify({"items": [s.to_admin_dict() for s in subs]})


@subscriptions_bp.post("/api/subscriptions/me/<string:subscription_id>/cancel")
@require_firebase_auth
def cancel_my_subscription(subscription_id):
    """Lets a donor cancel their own subscription directly — this is
    NOT an admin action, so it deliberately doesn't go through
    require_admin or write an AuditLog admin entry."""
    donor = Donor.query.filter_by(firebase_uid=g.firebase_user["uid"]).first()
    subscription = Subscription.query.get(subscription_id)

    if donor is None or subscription is None or subscription.donor_id != donor.id:
        return jsonify({"error": "Subscription not found"}), 404

    subscription.cancel()
    db.session.commit()

    email_service.send_subscription_cancellation(donor, subscription)
    return jsonify(subscription.to_admin_dict())


@subscriptions_bp.post("/api/subscriptions/webhook/<string:provider>")
def subscription_webhook(provider):
    """
    Stub for recurring-billing webhooks. When a real provider bills a
    subscription, this is where each cycle gets its own Donation row
    (spec #69) rather than the Subscription accumulating a total
    itself — donations stay the one financial source of truth (spec #66).
    """
    payload = request.get_json(silent=True) or {}
    subscription_id = payload.get("subscription_id")
    event_type = payload.get("event_type")

    subscription = Subscription.query.get(subscription_id) if subscription_id else None
    if subscription is None:
        return jsonify({"error": "Unknown subscription_id"}), 404

    if event_type == "payment_succeeded":
        donation = Donation(
            donor_id=subscription.donor_id,
            cause_id=subscription.cause_id,
            subscription_id=subscription.id,
            amount=subscription.amount,
            currency=subscription.currency,
            status=DonationStatus.SUCCESSFUL,
            payment_provider=provider,
        )
        db.session.add(donation)
        db.session.flush()
        db.session.add(
            PaymentTransaction(
                donation_id=donation.id,
                provider=provider,
                provider_transaction_id=payload.get("provider_transaction_id"),
                amount=subscription.amount,
                currency=subscription.currency,
                status=DonationStatus.SUCCESSFUL,
            )
        )
        subscription.status = SubscriptionStatus.ACTIVE
    elif event_type == "payment_failed":
        subscription.status = SubscriptionStatus.PAST_DUE

    db.session.commit()
    return jsonify({"status": "recorded"}), 200


# --- Admin --------------------------------------------------------------

@subscriptions_bp.get("/api/admin/subscriptions")
@require_admin
def list_subscriptions_admin():
    query = Subscription.query

    status = request.args.get("status")
    if status:
        if status not in SubscriptionStatus.ALL:
            return jsonify({"error": f"Invalid status filter '{status}'"}), 400
        query = query.filter_by(status=status)

    query = query.order_by(Subscription.created_at.desc())
    result = paginate_query(query)

    return jsonify(
        {
            "items": [s.to_admin_dict() for s in result["items"]],
            "pagination": result["pagination"],
        }
    )


@subscriptions_bp.post("/api/admin/subscriptions/<string:subscription_id>/cancel")
@require_admin
def cancel_subscription_admin(subscription_id):
    """Admin-initiated cancellation (e.g. by request, or abuse) — this
    one DOES write an AuditLog entry, unlike the donor's own cancel."""
    subscription = Subscription.query.get(subscription_id)
    if subscription is None:
        return jsonify({"error": "Subscription not found"}), 404

    subscription.cancel()

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_cancelled_subscription",
        resource_type="subscription",
        resource_id=subscription.id,
    )
    db.session.commit()

    email_service.send_subscription_cancellation(subscription.donor, subscription)

    return jsonify(subscription.to_admin_dict())
