from __future__ import annotations

from functools import wraps

from flask import g, request, jsonify, current_app

from app.services.firebase import verify_id_token, InvalidFirebaseToken
from app.models.admin import AdminUser


def _extract_bearer_token() -> str | None:
    """
    Extract the Firebase ID token from the Authorization header.

    Compatible with Python 3.8+.
    Expected format:
        Authorization: Bearer <firebase_id_token>
    """
    header = request.headers.get("Authorization", "")

    if not header.startswith("Bearer "):
        return None

    return header[7:].strip()


def require_firebase_auth(fn):
    """
    Verifies the Firebase ID token and attaches the decoded claims to
    `g.firebase_user`.

    This decorator authenticates the Firebase user but does not grant
    any admin privileges by itself.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _extract_bearer_token()

        if not token:
            return jsonify({"error": "Authentication required"}), 401

        try:
            g.firebase_user = verify_id_token(token)

        except InvalidFirebaseToken:
            # Do not expose internal Firebase/token validation details.
            current_app.logger.info(
                "Rejected invalid Firebase token on %s",
                request.path,
            )
            return jsonify(
                {"error": "Invalid or expired authentication token"}
            ), 401

        return fn(*args, **kwargs)

    return wrapper


def require_admin(fn):
    """
    Requires:
      1. A valid Firebase session
      2. An active AdminUser record associated with that Firebase UID
      3. A completed OTP (second-factor) verification, still within its
         session window (see AdminUser.otp_verified_until)

    Pending and suspended admins are rejected. An active admin who
    hasn't completed the OTP step (or whose OTP session has expired)
    gets a distinct `otp_required` flag in the 401 body so the frontend
    knows to show the OTP screen rather than treating this as a full
    logout.
    """

    @wraps(fn)
    @require_firebase_auth
    def wrapper(*args, **kwargs):
        firebase_uid = g.firebase_user["uid"]

        admin = AdminUser.query.filter_by(
            firebase_uid=firebase_uid
        ).first()

        if admin is None or not admin.is_active_admin():
            current_app.logger.info(
                "Rejected admin-only route for firebase_uid=%s "
                "(no active admin record)",
                firebase_uid,
            )

            return jsonify(
                {"error": "Administrator access required"}
            ), 403

        if not admin.is_otp_verified():
            current_app.logger.info(
                "Rejected admin-only route for admin_id=%s "
                "(OTP verification required or expired)",
                admin.id,
            )

            return jsonify(
                {"error": "OTP verification required", "otp_required": True}
            ), 401

        g.admin_user = admin

        return fn(*args, **kwargs)

    return wrapper


def require_superadmin(fn):
    """
    Requires the authenticated user to be an active superadmin.
    """

    @wraps(fn)
    @require_admin
    def wrapper(*args, **kwargs):
        if not g.admin_user.is_superadmin():
            current_app.logger.info(
                "Rejected superadmin-only route for admin_id=%s",
                g.admin_user.id,
            )

            return jsonify(
                {"error": "Superadmin access required"}
            ), 403

        return fn(*args, **kwargs)

    return wrapper