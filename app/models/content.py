from app.extensions import db
from app.models.base import TimestampMixin, uuid_pk_column


class ContactMessage(TimestampMixin, db.Model):
    """
    Public "contact us" submissions. Spec #77 explicitly calls out
    contact-message retention as something the org needs to be able to
    configure eventually — this model is what that retention policy
    would apply to.
    """

    __tablename__ = "contact_messages"

    id = uuid_pk_column()

    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(255), nullable=True)
    message = db.Column(db.Text, nullable=False)

    resolved = db.Column(db.Boolean, nullable=False, default=False)

    def to_admin_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "subject": self.subject,
            "message": self.message,
            "resolved": self.resolved,
            "created_at": self.created_at.isoformat(),
        }
