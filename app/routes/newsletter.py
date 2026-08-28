# ============================================================
# FILE BELONGS AT:  app/routes/newsletter.py
# ============================================================
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, current_app

from app.extensions import db
from app.utils.decorators import require_admin
from app.models.admin import record_audit
from app.models.newsletter import (
    NewsletterSubscriber,
    NewsletterCampaign,
    NewsletterCampaignStatus,
    normalize_email,
    is_valid_email,
    generate_unsubscribe_token,
)
from app.services.email import email_service

newsletter_bp = Blueprint("newsletter", __name__)


# ── Public: subscribe ──────────────────────────────────────────────────────

@newsletter_bp.route("/api/newsletter/subscribe", methods=["POST"])
def subscribe():
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get("email", ""))

    if not is_valid_email(email):
        return jsonify({"error": "Please enter a valid email address."}), 400

    existing = NewsletterSubscriber.query.filter_by(email=email).first()

    if existing:
        if existing.is_active:
            return jsonify({"message": "You're already subscribed.", "email": email}), 200
        existing.resubscribe()
        db.session.commit()
        return jsonify({"message": "Welcome back — you're subscribed again.", "email": email}), 200

    subscriber = NewsletterSubscriber(
        email=email,
        is_active=True,
        subscribed_at=datetime.now(timezone.utc),
        unsubscribe_token=generate_unsubscribe_token(),
        emails_sent_count=0,
    )
    db.session.add(subscriber)
    db.session.commit()
    return jsonify({"message": "You're subscribed.", "email": email}), 201


# ── Public: unsubscribe ─────────────────────────────────────────────────────

@newsletter_bp.route("/api/newsletter/unsubscribe/<token>", methods=["GET"])
def unsubscribe(token):
    subscriber = NewsletterSubscriber.query.filter_by(unsubscribe_token=token).first()

    if subscriber is None:
        return jsonify({"error": "Invalid or expired unsubscribe link."}), 404

    if not subscriber.is_active:
        return jsonify({"message": "You're already unsubscribed.", "email": subscriber.email}), 200

    subscriber.unsubscribe()
    db.session.commit()
    return jsonify({"message": "You have been unsubscribed from One Place, Inc. emails.", "email": subscriber.email}), 200


# ── Admin: list / search / filter subscribers ───────────────────────────────

@newsletter_bp.route("/api/admin/newsletter/subscribers", methods=["GET"])
@require_admin
def list_subscribers():
    query = NewsletterSubscriber.query

    status = (request.args.get("status") or "").strip().lower()
    if status == "active":
        query = query.filter_by(is_active=True)
    elif status == "unsubscribed":
        query = query.filter_by(is_active=False)

    search = (request.args.get("search") or "").strip().lower()
    if search:
        query = query.filter(NewsletterSubscriber.email.ilike(f"%{search}%"))

    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 50, type=int), 200)

    pagination = query.order_by(NewsletterSubscriber.subscribed_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return jsonify({
        "items": [s.to_dict() for s in pagination.items],
        "total": pagination.total,
        "page": page,
        "per_page": per_page,
    })


# ── Admin: dashboard stats ───────────────────────────────────────────────────

@newsletter_bp.route("/api/admin/newsletter/stats", methods=["GET"])
@require_admin
def newsletter_stats():
    total = NewsletterSubscriber.query.count()
    active = NewsletterSubscriber.query.filter_by(is_active=True).count()
    unsubscribed = total - active
    campaigns_sent = NewsletterCampaign.query.count()
    last_campaign = NewsletterCampaign.query.order_by(NewsletterCampaign.sent_at.desc()).first()

    return jsonify({
        "total_subscribers": total,
        "active_subscribers": active,
        "unsubscribed": unsubscribed,
        "newsletters_sent": campaigns_sent,
        "last_newsletter": last_campaign.to_dict() if last_campaign else None,
    })


# ── Admin: activate / deactivate a subscriber ────────────────────────────────

@newsletter_bp.route("/api/admin/newsletter/subscribers/<subscriber_id>", methods=["PATCH"])
@require_admin
def update_subscriber(subscriber_id):
    subscriber = NewsletterSubscriber.query.get(subscriber_id)
    if subscriber is None:
        return jsonify({"error": "Subscriber not found."}), 404

    data = request.get_json(silent=True) or {}
    if "is_active" not in data:
        return jsonify({"error": "is_active is required."}), 400

    if bool(data["is_active"]):
        subscriber.resubscribe()
        action = "newsletter.subscriber.reactivate"
    else:
        subscriber.unsubscribe()
        action = "newsletter.subscriber.deactivate"

    db.session.commit()
    record_audit(
        g_admin_id(),
        action=action,
        resource_type="newsletter_subscriber",
        resource_id=subscriber.id,
        description=subscriber.email,
    )
    db.session.commit()
    return jsonify(subscriber.to_dict())


# ── Admin: delete a subscriber ───────────────────────────────────────────────

@newsletter_bp.route("/api/admin/newsletter/subscribers/<subscriber_id>", methods=["DELETE"])
@require_admin
def delete_subscriber(subscriber_id):
    subscriber = NewsletterSubscriber.query.get(subscriber_id)
    if subscriber is None:
        return jsonify({"error": "Subscriber not found."}), 404

    email = subscriber.email
    db.session.delete(subscriber)
    record_audit(
        g_admin_id(),
        action="newsletter.subscriber.delete",
        resource_type="newsletter_subscriber",
        resource_id=subscriber_id,
        description=email,
    )
    db.session.commit()
    return jsonify({"message": "Subscriber deleted."})


# ── Admin: send newsletter ───────────────────────────────────────────────────

@newsletter_bp.route("/api/admin/newsletter/send", methods=["POST"])
@require_admin
def send_newsletter():
    data = request.get_json(silent=True) or {}
    subject = (data.get("subject") or "").strip()
    message = (data.get("message") or "").strip()

    if not subject:
        return jsonify({"error": "Subject is required."}), 400
    if not message:
        return jsonify({"error": "Message is required."}), 400

    subscribers = NewsletterSubscriber.query.filter_by(is_active=True).all()
    if not subscribers:
        return jsonify({"error": "No active subscribers to send to."}), 400

    frontend_url = current_app.config.get("FRONTEND_URL", "").rstrip("/")
    sent_count = 0
    failed_count = 0
    now = datetime.now(timezone.utc)

    for subscriber in subscribers:
        unsubscribe_url = f"{frontend_url}/unsubscribe/{subscriber.unsubscribe_token}"
        ok = email_service.send_newsletter_email(
            to_email=subscriber.email,
            subject=subject,
            message=message,
            unsubscribe_url=unsubscribe_url,
        )
        if ok:
            subscriber.last_email_sent_at = now
            subscriber.emails_sent_count = (subscriber.emails_sent_count or 0) + 1
            sent_count += 1
        else:
            failed_count += 1

    if sent_count == 0:
        status = NewsletterCampaignStatus.FAILED
    elif failed_count == 0:
        status = NewsletterCampaignStatus.SENT
    else:
        status = NewsletterCampaignStatus.PARTIAL

    campaign = NewsletterCampaign(
        subject=subject,
        message=message,
        recipient_count=sent_count,
        failed_count=failed_count,
        status=status,
        sent_by_admin_id=g_admin_id(),
        sent_at=now,
    )
    db.session.add(campaign)
    record_audit(
        g_admin_id(),
        action="newsletter.send",
        resource_type="newsletter_campaign",
        resource_id=campaign.id,
        description=f"{subject} — {sent_count} sent, {failed_count} failed",
    )
    db.session.commit()

    if status == NewsletterCampaignStatus.FAILED:
        return jsonify({
            "error": "Failed to send the newsletter — no emails went out. Check your Brevo configuration.",
            "sent": sent_count,
            "failed": failed_count,
        }), 502

    message_out = f"Newsletter sent successfully to {sent_count} subscriber{'s' if sent_count != 1 else ''}."
    if failed_count:
        message_out += f" {failed_count} failed to send."

    return jsonify({"message": message_out, "sent": sent_count, "failed": failed_count})


# ── Admin: send test email (does not touch subscriber/campaign data) ────────

@newsletter_bp.route("/api/admin/newsletter/test", methods=["POST"])
@require_admin
def send_test_newsletter():
    data = request.get_json(silent=True) or {}
    subject = (data.get("subject") or "").strip()
    message = (data.get("message") or "").strip()
    test_email = normalize_email(data.get("test_email", ""))

    if not subject:
        return jsonify({"error": "Subject is required."}), 400
    if not message:
        return jsonify({"error": "Message is required."}), 400
    if not is_valid_email(test_email):
        return jsonify({"error": "Please enter a valid test email address."}), 400

    frontend_url = current_app.config.get("FRONTEND_URL", "").rstrip("/")
    ok = email_service.send_newsletter_email(
        to_email=test_email,
        subject=f"[TEST] {subject}",
        message=message,
        unsubscribe_url=f"{frontend_url}/unsubscribe/preview",
    )

    if not ok:
        return jsonify({"error": "Failed to send test email. Check your Brevo configuration."}), 502

    return jsonify({"message": f"Test email sent to {test_email}."})


# ── helper ────────────────────────────────────────────────────────────────

def g_admin_id():
    from flask import g
    return getattr(g, "admin_user", None) and g.admin_user.id