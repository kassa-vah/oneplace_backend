import re

from app.extensions import db
from app.models.base import TimestampMixin, uuid_pk_column


def slugify(title: str) -> str:
    slug = title.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


class CauseStatus:
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"

    ALL = (DRAFT, PUBLISHED, ARCHIVED)


class Cause(TimestampMixin, db.Model):
    __tablename__ = "causes"

    id = uuid_pk_column()

    title = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), nullable=False, unique=True, index=True)
    description = db.Column(db.Text, nullable=True)

    beneficiary_id = db.Column(db.String(36), db.ForeignKey("beneficiaries.id"), nullable=True, index=True)
    beneficiary = db.relationship("Beneficiary", back_populates="causes")

    # Media: store a URL/reference only — storage provider stays swappable
    # per spec #87 (Cloudinary today, could be S3/Firebase later).
    cover_image_url = db.Column(db.String(1000), nullable=True)

    status = db.Column(
        db.String(20), nullable=False, default=CauseStatus.DRAFT, index=True
    )
    featured = db.Column(db.Boolean, nullable=False, default=False)
    display_order = db.Column(db.Integer, nullable=False, default=0)

    # goal_amount is informational only — actual raised totals are computed
    # from Donation records (spec #66), never stored as a trusted counter
    # on this model. A cached column can be added later once that
    # aggregation pipeline exists.
    goal_amount = db.Column(db.Numeric(12, 2), nullable=True)
    currency = db.Column(db.String(3), nullable=False, default="KES")

    archived_at = db.Column(db.DateTime(timezone=True), nullable=True)

    __table_args__ = (
        db.CheckConstraint("goal_amount IS NULL OR goal_amount > 0", name="ck_cause_goal_amount_positive"),
        db.CheckConstraint("currency IS NOT NULL", name="ck_cause_currency_not_null"),
    )

    def to_public_dict(self) -> dict:
        """Only fields safe for the public API — no internal admin fields."""
        return {
            "id": self.id,
            "title": self.title,
            "slug": self.slug,
            "description": self.description,
            "beneficiary": self.beneficiary.to_public_dict() if self.beneficiary else None,
            "cover_image_url": self.cover_image_url,
            "featured": self.featured,
            "goal_amount": str(self.goal_amount) if self.goal_amount is not None else None,
            "currency": self.currency,
        }

    def to_admin_dict(self) -> dict:
        data = self.to_public_dict()
        data.update(
            {
                "status": self.status,
                "beneficiary_id": self.beneficiary_id,
                "display_order": self.display_order,
                "archived_at": self.archived_at.isoformat() if self.archived_at else None,
                "created_at": self.created_at.isoformat(),
                "updated_at": self.updated_at.isoformat(),
            }
        )
        return data
