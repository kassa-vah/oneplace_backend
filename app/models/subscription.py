from datetime import datetime, timezone

from app.extensions import db
from app.models.base import TimestampMixin, uuid_pk_column


class SubscriptionStatus:
    """Recurring giving needs more states than active/cancelled (spec #68)."""

    PENDING = "pending"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"

    ALL = (PENDING, ACTIVE, PAST_DUE, PAUSED, CANCELLED, EXPIRED, FAILED)


class Subscription(TimestampMixin, db.Model):
    """
    A recurring giving commitment. The payment provider remains the
    source of truth for billing state — this record is updated by
    webhooks, not by the admin dashboard changing it directly (spec
    #68). Each successful billing cycle produces its own Donation row
    linked via Donation.subscription_id (spec #69) rather than this
    model accumulating a running total itself.
    """

    __tablename__ = "subscriptions"

    id = uuid_pk_column()

    donor_id = db.Column(db.String(36), db.ForeignKey("donors.id"), nullable=False, index=True)
    cause_id = db.Column(db.String(36), db.ForeignKey("causes.id"), nullable=False, index=True)

    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="KES")
    interval = db.Column(db.String(20), nullable=False, default="monthly")

    status = db.Column(db.String(20), nullable=False, default=SubscriptionStatus.PENDING, index=True)

    provider = db.Column(db.String(30), nullable=True)
    provider_subscription_id = db.Column(db.String(255), nullable=True, unique=True)

    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    cancelled_at = db.Column(db.DateTime(timezone=True), nullable=True)

    donor = db.relationship("Donor", back_populates="subscriptions")
    cause = db.relationship("Cause")
    donations = db.relationship("Donation", back_populates="subscription")

    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_subscription_amount_positive"),
    )

    def cancel(self):
        self.status = SubscriptionStatus.CANCELLED
        self.cancelled_at = datetime.now(timezone.utc)

    def to_admin_dict(self) -> dict:
        return {
            "id": self.id,
            "donor_id": self.donor_id,
            "cause_id": self.cause_id,
            "amount": str(self.amount),
            "currency": self.currency,
            "interval": self.interval,
            "status": self.status,
            "provider": self.provider,
            "provider_subscription_id": self.provider_subscription_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "cancelled_at": self.cancelled_at.isoformat() if self.cancelled_at else None,
            "created_at": self.created_at.isoformat(),
        }
