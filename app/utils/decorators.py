from __future__ import annotations

from functools import wraps

from flask import g, request, jsonify, current_app

from app.services.firebase import verify_id_token, InvalidFirebaseToken
from app.models.admin import AdminUser


def _extract_bearer_token() -> str | None:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    return header.removeprefix("Bearer ").strip()


def require_firebase_auth(fn):
    """Verifies the Firebase ID token and attaches the decoded claims to
    `g.firebase_user`. Does not by itself grant any admin privileges."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _extract_bearer_token()
        if not token:
            return jsonify({"error": "Authentication required"}), 401

        try:
            g.firebase_user = verify_id_token(token)
        except InvalidFirebaseToken:
            # Spec #92 — no internal detail leaks into the response.
            current_app.logger.info("Rejected invalid Firebase token on %s", request.path)
            return jsonify({"error": "Invalid or expired authentication token"}), 401

        return fn(*args, **kwargs)

    return wrapper


def require_admin(fn):
    """Requires a valid Firebase session AND an active AdminUser record
    for that Firebase UID. Suspended/pending admins are rejected."""

    @wraps(fn)
    @require_firebase_auth
    def wrapper(*args, **kwargs):
        firebase_uid = g.firebase_user["uid"]
        admin = AdminUser.query.filter_by(firebase_uid=firebase_uid).first()

        if admin is None or not admin.is_active_admin():
            current_app.logger.info(
                "Rejected admin-only route for firebase_uid=%s (no active admin record)",
                firebase_uid,
            )
            return jsonify({"error": "Administrator access required"}), 403

        g.admin_user = admin
        return fn(*args, **kwargs)

    return wrapper


def require_superadmin(fn):
    @wraps(fn)
    @require_admin
    def wrapper(*args, **kwargs):
        if not g.admin_user.is_superadmin():
            current_app.logger.info(
                "Rejected superadmin-only route for admin_id=%s", g.admin_user.id
            )
            return jsonify({"error": "Superadmin access required"}), 403
        return fn(*args, **kwargs)

    return wrapper
