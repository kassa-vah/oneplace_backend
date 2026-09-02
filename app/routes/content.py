from __future__ import annotations

from flask import Blueprint, jsonify, request, g

from app.extensions import db
from app.utils.decorators import require_admin
from app.utils.pagination import paginate_query
from app.models.content import Blog, ALLOWED_CATEGORIES, MAX_PHOTOS, slugify
from app.models.admin import record_audit

content_bp = Blueprint("content", __name__)


def _unique_slug(title: str, exclude_id: str | None = None) -> str:
    base = slugify(title)
    slug = base
    suffix = 1
    while True:
        query = Blog.query.filter_by(slug=slug)
        if exclude_id:
            query = query.filter(Blog.id != exclude_id)
        if query.first() is None:
            return slug
        suffix += 1
        slug = f"{base}-{suffix}"


def _validate_category(category: str):
    if category not in ALLOWED_CATEGORIES:
        return f"category must be one of {sorted(ALLOWED_CATEGORIES)}"
    return None


def _validate_photos(photos):
    if photos is None:
        return None, []
    if not isinstance(photos, list) or not all(isinstance(p, str) for p in photos):
        return "photos must be a list of URL strings", None
    if len(photos) > MAX_PHOTOS:
        return f"a blog can have at most {MAX_PHOTOS} photos", None
    return None, [p.strip() for p in photos if p.strip()]


# ---------------- Public ----------------

@content_bp.get("/api/blogs")
def list_blogs():
    query = Blog.query

    category = request.args.get("category")
    if category:
        if category not in ALLOWED_CATEGORIES:
            return jsonify({"error": f"category must be one of {sorted(ALLOWED_CATEGORIES)}"}), 400
        query = query.filter_by(category=category)

    query = query.order_by(Blog.created_at.desc())
    result = paginate_query(query)

    return jsonify(
        {
            "items": [b.to_public_dict() for b in result["items"]],
            "pagination": result["pagination"],
        }
    )


@content_bp.get("/api/blogs/<string:blog_id>")
def get_blog(blog_id):
    blog = Blog.query.get(blog_id)
    if blog is None:
        return jsonify({"error": "Blog not found"}), 404
    return jsonify(blog.to_public_dict())


# ---------------- Admin ----------------

@content_bp.post("/api/admin/blogs")
@require_admin
def create_blog():
    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    category = (payload.get("category") or "").strip()
    excerpt = (payload.get("excerpt") or "").strip()
    content = (payload.get("content") or "").strip() or None

    if not title or not category or not excerpt:
        return jsonify({"error": "title, category, and excerpt are required"}), 400

    category_error = _validate_category(category)
    if category_error:
        return jsonify({"error": category_error}), 400

    photos_error, photos = _validate_photos(payload.get("photos"))
    if photos_error:
        return jsonify({"error": photos_error}), 400

    blog = Blog(
        title=title,
        slug=_unique_slug(title),
        category=category,
        excerpt=excerpt,
        content=content,
        photos=photos,
    )
    db.session.add(blog)

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_created_blog",
        resource_type="blog",
        resource_id=blog.id,
    )
    db.session.commit()

    return jsonify(blog.to_admin_dict()), 201


@content_bp.get("/api/admin/blogs")
@require_admin
def list_admin_blogs():
    query = Blog.query

    category = request.args.get("category")
    if category:
        query = query.filter_by(category=category)

    query = query.order_by(Blog.created_at.desc())
    result = paginate_query(query)

    return jsonify(
        {
            "items": [b.to_admin_dict() for b in result["items"]],
            "pagination": result["pagination"],
        }
    )


@content_bp.get("/api/admin/blogs/<string:blog_id>")
@require_admin
def get_admin_blog(blog_id):
    blog = Blog.query.get(blog_id)
    if blog is None:
        return jsonify({"error": "Blog not found"}), 404
    return jsonify(blog.to_admin_dict())


@content_bp.patch("/api/admin/blogs/<string:blog_id>")
@require_admin
def update_blog(blog_id):
    blog = Blog.query.get(blog_id)
    if blog is None:
        return jsonify({"error": "Blog not found"}), 404

    payload = request.get_json(silent=True) or {}

    if "title" in payload:
        title = (payload["title"] or "").strip()
        if not title:
            return jsonify({"error": "title cannot be empty"}), 400
        if title != blog.title:
            blog.slug = _unique_slug(title, exclude_id=blog.id)
        blog.title = title

    if "category" in payload:
        category = (payload["category"] or "").strip()
        category_error = _validate_category(category)
        if category_error:
            return jsonify({"error": category_error}), 400
        blog.category = category

    if "excerpt" in payload:
        excerpt = (payload["excerpt"] or "").strip()
        if not excerpt:
            return jsonify({"error": "excerpt cannot be empty"}), 400
        blog.excerpt = excerpt

    if "content" in payload:
        blog.content = (payload["content"] or "").strip() or None

    if "photos" in payload:
        photos_error, photos = _validate_photos(payload["photos"])
        if photos_error:
            return jsonify({"error": photos_error}), 400
        blog.photos = photos

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_updated_blog",
        resource_type="blog",
        resource_id=blog.id,
    )
    db.session.commit()

    return jsonify(blog.to_admin_dict())


@content_bp.delete("/api/admin/blogs/<string:blog_id>")
@require_admin
def delete_blog(blog_id):
    blog = Blog.query.get(blog_id)
    if blog is None:
        return jsonify({"error": "Blog not found"}), 404

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_deleted_blog",
        resource_type="blog",
        resource_id=blog.id,
    )
    db.session.delete(blog)
    db.session.commit()

    return jsonify({"status": "deleted"})