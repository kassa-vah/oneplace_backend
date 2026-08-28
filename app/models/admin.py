from __future__ import annotations

from datetime import datetime, timezone

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


def _aware(dt):
    """
    Every datetime this app writes for these columns is UTC
    (datetime.now(timezone.utc)). But despite otp_expires_at /
    otp_last_sent_at / otp_verified_until being declared
    DateTime(timezone=True), SQLite has no real timezone-aware storage
    type — the sqlite3 driver commonly hands back naive datetimes on
    read regardless of how they were written, which breaks any
    comparison against an aware `now`. Since this app never writes
    anything but UTC into these columns, a naive value read back is
    safe to treat as UTC — so just attach the tzinfo rather than
    convert. (On a backend with real tz-aware storage, e.g. Postgres,
    this is a no-op.)
    """
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class AdminUser(TimestampMixin, db.Model):
    """
    An administrator account. There is no password on this model —
    Firebase Authentication is the authentication authority (spec #89);
    this table only holds authorization state tied to a Firebase UID.

    OTP fields below implement a second factor on top of Firebase auth:
    after a Firebase sign-in succeeds, the admin must also enter a
    mailed one-time code before `require_admin` will grant access to
    any admin route. Only the code's hash is ever stored.
    """

    __tablename__ = "admin_users"

    id = uuid_pk_column()

    firebase_uid = db.Column(db.String(128), nullable=False, unique=True, index=True)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    name = db.Column(db.String(255), nullable=True)

    role = db.Column(db.String(20), nullable=False, default=AdminRole.ADMIN)
    status = db.Column(db.String(20), nullable=False, default=AdminStatus.PENDING)

    # ── OTP (second factor) ────────────────────────────────────────────
    otp_code_hash = db.Column(db.String(128), nullable=True)
    otp_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    otp_attempts = db.Column(db.Integer, nullable=False, default=0)
    otp_last_sent_at = db.Column(db.DateTime(timezone=True), nullable=True)
    # Once verified, the admin session stays OTP-verified until this
    # timestamp — checked by require_admin on every admin-only request.
    otp_verified_until = db.Column(db.DateTime(timezone=True), nullable=True)

    def is_active_admin(self) -> bool:
        return self.status == AdminStatus.ACTIVE

    def is_superadmin(self) -> bool:
        return self.role == AdminRole.SUPERADMIN and self.is_active_admin()

    def is_otp_verified(self) -> bool:
        verified_until = _aware(self.otp_verified_until)
        return bool(verified_until) and verified_until > datetime.now(timezone.utc)

    def clear_otp_challenge(self) -> None:
        """Clear a pending (unverified) OTP code — used on success, expiry, or lockout."""
        self.otp_code_hash = None
        self.otp_expires_at = None
        self.otp_attempts = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "role": self.role,
            "status": self.status,
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