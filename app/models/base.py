import uuid
from datetime import datetime, timezone

from app.extensions import db


def utcnow():
    """Timezone-aware UTC now — spec #76 requires no naive datetimes."""
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


def uuid_pk_column():
    return db.Column(
        db.String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
