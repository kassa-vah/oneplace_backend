import re
import uuid

from app.extensions import db
from app.models.base import TimestampMixin, uuid_pk_column

ALLOWED_CATEGORIES = {"updates", "news", "blogs", "stories"}
MAX_PHOTOS = 5


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or uuid.uuid4().hex[:8]


class Blog(TimestampMixin, db.Model):
    """
    Admin-managed posts (updates / news / blogs / stories). `photos` is a
    JSON list of up to MAX_PHOTOS Cloudinary secure_urls, uploaded directly
    from the client via an unsigned upload preset (see ImageUploader.jsx) —
    the backend never talks to Cloudinary itself.
    """

    __tablename__ = "blogs"

    id = uuid_pk_column()

    title = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(280), nullable=False, unique=True, index=True)
    category = db.Column(db.String(20), nullable=False)
    excerpt = db.Column(db.String(500), nullable=False)
    content = db.Column(db.Text, nullable=True)
    photos = db.Column(db.JSON, nullable=False, default=list)

    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "slug": self.slug,
            "category": self.category,
            "excerpt": self.excerpt,
            "content": self.content,
            "photos": self.photos,
            "created_at": self.created_at.isoformat(),
        }

    def to_admin_dict(self) -> dict:
        return self.to_public_dict()