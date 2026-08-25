from datetime import datetime, timezone

from app.extensions import db
from app.models.base import TimestampMixin, uuid_pk_column


class BeneficiaryStatus:
    ACTIVE = "active"
    ARCHIVED = "archived"

    ALL = (ACTIVE, ARCHIVED)


class Beneficiary(TimestampMixin, db.Model):
    """
    A person/family/group a Cause raises funds for. Kept as its own
    table (rather than a free-text field on Cause) so beneficiaries can
    be searched, reused across multiple causes, and soft-archived
    without breaking historical donation associations (spec #82's
    'same principle should apply to beneficiaries').
    """

    __tablename__ = "beneficiaries"

    id = uuid_pk_column()

    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    photo_url = db.Column(db.String(1000), nullable=True)

    status = db.Column(db.String(20), nullable=False, default=BeneficiaryStatus.ACTIVE, index=True)
    archived_at = db.Column(db.DateTime(timezone=True), nullable=True)

    causes = db.relationship("Cause", back_populates="beneficiary")

    def archive(self):
        self.status = BeneficiaryStatus.ARCHIVED
        self.archived_at = datetime.now(timezone.utc)

    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "photo_url": self.photo_url,
        }

    def to_admin_dict(self) -> dict:
        data = self.to_public_dict()
        data.update(
            {
                "status": self.status,
                "archived_at": self.archived_at.isoformat() if self.archived_at else None,
                "created_at": self.created_at.isoformat(),
                "updated_at": self.updated_at.isoformat(),
            }
        )
        return data
