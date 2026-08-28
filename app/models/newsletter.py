# ============================================================
# FILE BELONGS AT:  app/models/newsletter.py
# ============================================================
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from app.extensions import db
from app.models.base import TimestampMixin, uuid_pk_column

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(raw_email: str) -> str:
    """" USER@Example.COM " -> "user@example.com" """
    return (raw_email or "").strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(email) and len(email) <= 255 and bool(_EMAIL_RE.match(email))


def generate_unsubscribe_token() -> str:
    return secrets.token_urlsafe(32)


class NewsletterSubscriber(TimestampMixin, db.Model):
    """
    A public newsletter subscriber. Email-only — no name, no account.
    `created_at` (from TimestampMixin) is set once, on first insert.
    `subscribed_at` is set on every (re)subscription, so a resubscribe
    after unsubscribing updates it while `created_at` stays put.
    """

    __tablename__ = "newsletter_subscribers"

    id = uuid_pk_column()

    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)

    subscribed_at = db.Column(db.DateTime(timezone=True), nullable=False)
    unsubscribed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    unsubscribe_token = db.Column(db.String(64), nullable=False, unique=True, index=True)

    last_email_sent_at = db.Column(db.DateTime(timezone=True), nullable=True)
    emails_sent_count = db.Column(db.Integer, nullable=False, default=0)

    def resubscribe(self) -> None:
        self.is_active = True
        self.unsubscribed_at = None
        self.subscribed_at = datetime.now(timezone.utc)
        # Rotate the token on resubscribe — an old unsubscribe link
        # (e.g. leaked or bookmarked) shouldn't stay valid forever.
        self.unsubscribe_token = generate_unsubscribe_token()

    def unsubscribe(self) -> None:
        self.is_active = False
        self.unsubscribed_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "is_active": self.is_active,
            "subscribed_at": self.subscribed_at.isoformat() if self.subscribed_at else None,
            "unsubscribed_at": self.unsubscribed_at.isoformat() if self.unsubscribed_at else None,
            "last_email_sent_at": self.last_email_sent_at.isoformat() if self.last_email_sent_at else None,
            "emails_sent_count": self.emails_sent_count,
            "created_at": self.created_at.isoformat(),
        }


class NewsletterCampaignStatus:
    SENT = "sent"
    PARTIAL = "partial"
    FAILED = "failed"

    ALL = (SENT, PARTIAL, FAILED)


class NewsletterCampaign(TimestampMixin, db.Model):
    """
    A lightweight send-history record — one row per admin-triggered
    newsletter send. Deliberately not a full campaign/automation model:
    no scheduling, no templates, no segments. Test sends do NOT create
    a row here (see routes/newsletter.py: POST /api/admin/newsletter/test).
    """

    __tablename__ = "newsletter_campaigns"

    id = uuid_pk_column()

    subject = db.Column(db.String(255), nullable=False)
    message = db.Column(db.Text, nullable=False)

    recipient_count = db.Column(db.Integer, nullable=False, default=0)
    failed_count = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default=NewsletterCampaignStatus.SENT)

    sent_by_admin_id = db.Column(db.String(36), db.ForeignKey("admin_users.id"), nullable=True)
    sent_at = db.Column(db.DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "subject": self.subject,
            "recipient_count": self.recipient_count,
            "failed_count": self.failed_count,
            "status": self.status,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
        }