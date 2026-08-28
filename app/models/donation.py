# ============================================================
# FILE BELONGS AT:  app/models/donation.py
# ============================================================
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin, uuid_pk_column


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


class Donor(TimestampMixin, db.Model):
    """
    A registered account. Since SwipeSimple (external, no API) handles
    all actual payment processing, this model's real job now is
    tracking who's signed in via Firebase — anyone who does is a
    candidate a superadmin can promote to admin (see
    GET/POST /api/admins/registrations in routes/admins.py). It also
    doubles as a lightweight consent record (POST /api/donors/consent
    is the natural "you just registered" touchpoint).

    Never exposed on a public endpoint — email/phone/etc are only ever
    returned from admin-authenticated routes.
    """

    __tablename__ = "donors"

    id = uuid_pk_column()

    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    firebase_uid = db.Column(db.String(128), nullable=True, unique=True, index=True)
    first_name = db.Column(db.String(120), nullable=True)
    last_name = db.Column(db.String(120), nullable=True)
    phone = db.Column(db.String(30), nullable=True)
    country = db.Column(db.String(2), nullable=True)

    email_receipts = db.Column(db.Boolean, nullable=False, default=True)
    marketing_emails = db.Column(db.Boolean, nullable=False, default=False)
    impact_updates = db.Column(db.Boolean, nullable=False, default=False)

    # Privacy policy consent — captured the moment someone registers
    # (POST /api/donors/consent), which is now the only reason a
    # member of the public would sign in at all.
    consent_accepted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    consent_version = db.Column(db.String(20), nullable=True)

    @staticmethod
    def find_or_create_registered(firebase_uid: str, email: str, **fields) -> "Donor":
        donor = Donor.query.filter_by(firebase_uid=firebase_uid).first()
        if donor is not None:
            return donor

        normalized = normalize_email(email)
        donor = Donor.query.filter_by(email=normalized).first()
        if donor is None:
            donor = Donor(email=normalized, firebase_uid=firebase_uid, **fields)
            db.session.add(donor)
        else:
            donor.firebase_uid = firebase_uid
        db.session.flush()
        return donor

    def to_admin_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "firebase_uid": self.firebase_uid,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "phone": self.phone,
            "country": self.country,
            "email_receipts": self.email_receipts,
            "marketing_emails": self.marketing_emails,
            "impact_updates": self.impact_updates,
            "consent_accepted_at": self.consent_accepted_at.isoformat() if self.consent_accepted_at else None,
            "consent_version": self.consent_version,
            "created_at": self.created_at.isoformat(),
        }


class DonationStatus:
    """
    No payment-provider-driven state machine anymore — an admin only
    ever logs a donation after it's already happened on SwipeSimple, so
    there's no "pending"/"failed" to track. The only transition that
    matters is marking one refunded after the fact.
    """

    RECORDED = "recorded"
    REFUNDED = "refunded"

    ALL = (RECORDED, REFUNDED)


class Donation(TimestampMixin, db.Model):
    """
    A lightweight, admin-entered bookkeeping record — "we received
    $50 via SwipeSimple for Cause X on this date." Nothing here talks
    to a payment provider; there isn't one to talk to. This exists
    purely so admins have their own reporting/CSV export, independent
    of whatever SwipeSimple's own dashboard shows them.
    """

    __tablename__ = "donations"

    id = uuid_pk_column()

    cause_id = db.Column(db.String(36), db.ForeignKey("causes.id"), nullable=False, index=True)

    # Free-text donor info rather than a Donor FK — most manually-logged
    # donations won't correspond to a registered account (Donor is for
    # admin-candidate tracking, a different concern). If it happens to
    # be the same person, that's just a coincidence of matching email.
    donor_name = db.Column(db.String(255), nullable=True)
    donor_email = db.Column(db.String(255), nullable=True)

    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="USD")

    # Free text, not an enum — "SwipeSimple", "Cash", "Check", "Bank
    # Transfer", whatever the admin actually received. No provider
    # abstraction to validate against anymore.
    method = db.Column(db.String(50), nullable=True)

    status = db.Column(db.String(20), nullable=False, default=DonationStatus.RECORDED, index=True)
    is_anonymous = db.Column(db.Boolean, nullable=False, default=False)
    note = db.Column(db.String(1000), nullable=True)

    refund_reason = db.Column(db.String(500), nullable=True)
    refunded_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # Which admin logged this entry — accountability now that these
    # are hand-entered rather than provider-confirmed.
    recorded_by_admin_id = db.Column(db.String(36), db.ForeignKey("admin_users.id"), nullable=True)

    # When the donation actually happened, per SwipeSimple/the admin's
    # records — may differ from created_at if logged after the fact.
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=True)

    cause = db.relationship("Cause")

    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_donation_amount_positive"),
        db.CheckConstraint("currency IS NOT NULL", name="ck_donation_currency_not_null"),
    )

    def mark_refunded(self, reason: str | None = None):
        from datetime import datetime, timezone
        self.status = DonationStatus.REFUNDED
        self.refund_reason = reason
        self.refunded_at = datetime.now(timezone.utc)

    def to_admin_dict(self) -> dict:
        return {
            "id": self.id,
            "cause_id": self.cause_id,
            "donor_name": None if self.is_anonymous else self.donor_name,
            "donor_email": None if self.is_anonymous else self.donor_email,
            "amount": str(self.amount),
            "currency": self.currency,
            "method": self.method,
            "status": self.status,
            "is_anonymous": self.is_anonymous,
            "note": self.note,
            "refund_reason": self.refund_reason,
            "refunded_at": self.refunded_at.isoformat() if self.refunded_at else None,
            "recorded_by_admin_id": self.recorded_by_admin_id,
            "occurred_at": self.occurred_at.isoformat() if self.occurred_at else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }