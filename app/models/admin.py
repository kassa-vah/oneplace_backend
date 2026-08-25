from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin, uuid_pk_column


class AdminRole:
    ADMIN = "admin"
    SUPERADMIN = "superadmin"

    ALL = (ADMIN, SUPERADMIN)


class AdminStatus:
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"

    ALL = (PENDING, ACTIVE, SUSPENDED)


class AdminUser(TimestampMixin, db.Model):
    """
    An administrator account. There is no password on this model —
    Firebase Authentication is the authentication authority (spec #89);
    this table only holds authorization state tied to a Firebase UID.
    """

    __tablename__ = "admin_users"

    id = uuid_pk_column()

    firebase_uid = db.Column(db.String(128), nullable=False, unique=True, index=True)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    name = db.Column(db.String(255), nullable=True)

    role = db.Column(db.String(20), nullable=False, default=AdminRole.ADMIN)
    status = db.Column(db.String(20), nullable=False, default=AdminStatus.PENDING)

    def is_active_admin(self) -> bool:
        return self.status == AdminStatus.ACTIVE

    def is_superadmin(self) -> bool:
        return self.role == AdminRole.SUPERADMIN and self.is_active_admin()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "role": self.role,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


class InvitationStatus:
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    REVOKED = "revoked"

    ALL = (PENDING, ACCEPTED, EXPIRED, REVOKED)


class AdminInvitation(TimestampMixin, db.Model):
    """
    A superadmin-issued invitation (spec #72-73). Only the token's hash
    is stored — the plaintext token is returned once, at creation time,
    to be delivered to the invitee (e.g. via the email service), and is
    never persisted or logged in the clear.
    """

    __tablename__ = "admin_invitations"

    id = uuid_pk_column()

    email = db.Column(db.String(255), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False, default=AdminRole.ADMIN)
    token_hash = db.Column(db.String(128), nullable=False, unique=True)
    status = db.Column(db.String(20), nullable=False, default=InvitationStatus.PENDING)

    invited_by = db.Column(db.String(36), db.ForeignKey("admin_users.id"), nullable=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    accepted_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "role": self.role,
            "status": self.status,
            "invited_by": self.invited_by,
            "expires_at": self.expires_at.isoformat(),
            "accepted_at": self.accepted_at.isoformat() if self.accepted_at else None,
            "created_at": self.created_at.isoformat(),
        }


class AuditLog(TimestampMixin, db.Model):
    """
    Records administrative actions (spec #74). Deliberately minimal —
    description is a short human-readable string, not a secrets dump.
    Never write tokens, keys, or payment credentials into this table.
    """

    __tablename__ = "audit_logs"

    id = uuid_pk_column()

    admin_id = db.Column(db.String(36), db.ForeignKey("admin_users.id"), nullable=True, index=True)
    action = db.Column(db.String(100), nullable=False)
    resource_type = db.Column(db.String(50), nullable=False)
    resource_id = db.Column(db.String(36), nullable=True)
    description = db.Column(db.String(500), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "admin_id": self.admin_id,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
        }


def record_audit(admin_id, action: str, resource_type: str, resource_id: str | None = None, description: str | None = None):
    entry = AuditLog(
        admin_id=admin_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        description=description,
    )
    db.session.add(entry)
    return entry
