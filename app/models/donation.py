from app.extensions import db
from app.models.base import TimestampMixin, uuid_pk_column


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


class Donor(TimestampMixin, db.Model):
    """
    A donor may have one or many donations and one or many subscriptions
    (spec #56). Looked up/created by normalized email at donation time —
    never exposed on public endpoints (spec #57).
    """

    __tablename__ = "donors"

    id = uuid_pk_column()

    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    # Set only when a donor registers via Firebase — required to start a
    # subscription, never required for a one-time donation.
    firebase_uid = db.Column(db.String(128), nullable=True, unique=True, index=True)
    first_name = db.Column(db.String(120), nullable=True)
    last_name = db.Column(db.String(120), nullable=True)
    phone = db.Column(db.String(30), nullable=True)
    country = db.Column(db.String(2), nullable=True)

    # Communication preferences — separate from whether a donation is
    # displayed anonymously (spec #78). A receipt is transactional and
    # doesn't imply marketing consent.
    email_receipts = db.Column(db.Boolean, nullable=False, default=True)
    marketing_emails = db.Column(db.Boolean, nullable=False, default=False)
    impact_updates = db.Column(db.Boolean, nullable=False, default=False)

    donations = db.relationship("Donation", back_populates="donor")
    subscriptions = db.relationship("Subscription", back_populates="donor")

    @staticmethod
    def find_or_create(email: str, **fields) -> "Donor":
        """For one-time, no-registration-required donations (spec: donating
        never requires being signed in)."""
        normalized = normalize_email(email)
        donor = Donor.query.filter_by(email=normalized).first()
        if donor is None:
            donor = Donor(email=normalized, **fields)
            db.session.add(donor)
            db.session.flush()
        return donor

    @staticmethod
    def find_or_create_registered(firebase_uid: str, email: str, **fields) -> "Donor":
        """For subscriptions, which require registration. Links the donor
        record to a Firebase account so they can later view/cancel their
        own recurring giving without needing admin access."""
        donor = Donor.query.filter_by(firebase_uid=firebase_uid).first()
        if donor is not None:
            return donor

        normalized = normalize_email(email)
        donor = Donor.query.filter_by(email=normalized).first()
        if donor is None:
            donor = Donor(email=normalized, firebase_uid=firebase_uid, **fields)
            db.session.add(donor)
        else:
            # They donated once as a guest before registering — link the
            # existing donor record rather than creating a duplicate.
            donor.firebase_uid = firebase_uid
        db.session.flush()
        return donor

    def to_admin_dict(self) -> dict:
        """Admin-only view — full PII. Never returned from a public route."""
        return {
            "id": self.id,
            "email": self.email,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "phone": self.phone,
            "country": self.country,
            "email_receipts": self.email_receipts,
            "marketing_emails": self.marketing_emails,
            "impact_updates": self.impact_updates,
            "created_at": self.created_at.isoformat(),
        }


class DonationStatus:
    PENDING = "pending"
    SUCCESSFUL = "successful"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"

    ALL = (PENDING, SUCCESSFUL, FAILED, CANCELLED, REFUNDED)

    # Explicit transition table (spec #67) — an ordinary update endpoint
    # can never move a donation outside these edges, e.g. refunded ->
    # successful is simply not in this map.
    VALID_TRANSITIONS = {
        PENDING: {SUCCESSFUL, FAILED, CANCELLED},
        SUCCESSFUL: {REFUNDED},
        FAILED: set(),
        CANCELLED: set(),
        REFUNDED: set(),
    }

    @classmethod
    def can_transition(cls, current: str, target: str) -> bool:
        return target in cls.VALID_TRANSITIONS.get(current, set())


class Donation(TimestampMixin, db.Model):
    """
    The financial source of truth for a single payment (spec #66).
    Successful donations are treated as immutable history (spec #59) —
    amount/provider/transaction fields are never rewritten by an
    ordinary update; only status transitions defined in DonationStatus
    are allowed, and refunds are tracked via dedicated fields rather
    than by silently flipping status back to something else.
    """

    __tablename__ = "donations"

    id = uuid_pk_column()

    donor_id = db.Column(db.String(36), db.ForeignKey("donors.id"), nullable=False, index=True)
    cause_id = db.Column(db.String(36), db.ForeignKey("causes.id"), nullable=False, index=True)
    subscription_id = db.Column(db.String(36), db.ForeignKey("subscriptions.id"), nullable=True, index=True)

    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="KES")

    status = db.Column(db.String(20), nullable=False, default=DonationStatus.PENDING, index=True)
    is_anonymous = db.Column(db.Boolean, nullable=False, default=False)
    donor_message = db.Column(db.String(1000), nullable=True)

    payment_provider = db.Column(db.String(30), nullable=True)

    # Idempotency key supplied by the frontend at initiation time (spec
    # #61/#96) — protects against double-click / retry duplicate charges
    # independently of whatever transaction ID the provider assigns later.
    idempotency_key = db.Column(db.String(128), nullable=True, unique=True)

    refund_reason = db.Column(db.String(500), nullable=True)
    refund_reference = db.Column(db.String(255), nullable=True)
    refunded_at = db.Column(db.DateTime(timezone=True), nullable=True)

    donor = db.relationship("Donor", back_populates="donations")
    cause = db.relationship("Cause")
    subscription = db.relationship("Subscription", back_populates="donations")
    transactions = db.relationship("PaymentTransaction", back_populates="donation")

    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_donation_amount_positive"),
        db.CheckConstraint("currency IS NOT NULL", name="ck_donation_currency_not_null"),
    )

    def transition_to(self, target_status: str) -> bool:
        """Applies a status transition only if it's valid. Returns False
        (and does nothing) on an invalid transition rather than raising,
        so callers can decide how to respond."""
        if not DonationStatus.can_transition(self.status, target_status):
            return False
        self.status = target_status
        return True

    def to_public_receipt_dict(self) -> dict:
        """Minimal shape a donor-facing confirmation screen needs — no
        internal IDs, no other donors' data, no donor PII beyond what
        the donor themselves just submitted."""
        return {
            "id": self.id,
            "amount": str(self.amount),
            "currency": self.currency,
            "status": self.status,
            "is_anonymous": self.is_anonymous,
            "cause_id": self.cause_id,
        }

    def to_admin_dict(self) -> dict:
        return {
            "id": self.id,
            "donor_id": self.donor_id,
            "cause_id": self.cause_id,
            "subscription_id": self.subscription_id,
            "amount": str(self.amount),
            "currency": self.currency,
            "status": self.status,
            "is_anonymous": self.is_anonymous,
            "donor_message": self.donor_message,
            "payment_provider": self.payment_provider,
            "refund_reason": self.refund_reason,
            "refund_reference": self.refund_reference,
            "refunded_at": self.refunded_at.isoformat() if self.refunded_at else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class PaymentTransaction(TimestampMixin, db.Model):
    """
    Provider-side transaction metadata, kept separate from Donation
    (spec #63) so the donation business logic isn't tightly coupled to
    any one provider's fields. Never stores card data — metadata only.
    """

    __tablename__ = "payment_transactions"

    id = uuid_pk_column()

    donation_id = db.Column(db.String(36), db.ForeignKey("donations.id"), nullable=False, index=True)
    provider = db.Column(db.String(30), nullable=False)
    provider_transaction_id = db.Column(db.String(255), nullable=True)
    provider_reference = db.Column(db.String(255), nullable=True)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    status = db.Column(db.String(20), nullable=False, default=DonationStatus.PENDING)
    raw_event_reference = db.Column(db.String(255), nullable=True)

    donation = db.relationship("Donation", back_populates="transactions")

    __table_args__ = (
        db.UniqueConstraint("provider", "provider_transaction_id", name="uq_provider_transaction_id"),
    )

    def to_admin_dict(self) -> dict:
        return {
            "id": self.id,
            "donation_id": self.donation_id,
            "provider": self.provider,
            "provider_transaction_id": self.provider_transaction_id,
            "provider_reference": self.provider_reference,
            "amount": str(self.amount),
            "currency": self.currency,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


class PaymentWebhookEvent(TimestampMixin, db.Model):
    """
    A reliable record of every payment webhook received (spec #62).
    The provider+event_id uniqueness constraint is what makes webhook
    processing idempotent (spec #61/#95) — a duplicate delivery hits the
    constraint and is treated as already-processed rather than creating
    a second donation.
    """

    __tablename__ = "payment_webhook_events"

    id = uuid_pk_column()

    provider = db.Column(db.String(30), nullable=False)
    event_id = db.Column(db.String(255), nullable=False)
    event_type = db.Column(db.String(100), nullable=True)
    processed = db.Column(db.Boolean, nullable=False, default=False)
    processing_error = db.Column(db.Text, nullable=True)
    processed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    __table_args__ = (
        db.UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),
    )
