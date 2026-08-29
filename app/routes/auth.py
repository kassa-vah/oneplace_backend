from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request, g

from app.extensions import db
from app.utils.decorators import require_firebase_auth, OTP_SESSION_MINUTES
from app.utils.security import hash_token, verify_token, generate_otp_code
from app.models.admin import AdminUser
from app.models.donation import Donor
from app.services.email import email_service


auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN_SECONDS = 30


def _aware(dt):
    """
    Every datetime this module produces is timezone-aware UTC
    (datetime.now(timezone.utc)). But values read back from the DB can
    come back naive depending on the column type / driver, which blows
    up any subtraction against an aware `now`. Since this module never
    writes anything but UTC, a naive value read back is safe to treat
    as UTC — so just attach the tzinfo rather than convert.
    """
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@auth_bp.post("/register")
@require_firebase_auth
def register():
    """
    Fired the moment a Firebase signup succeeds — this IS registration,
    full stop. Not a consent step, not something gated behind onboarding
    screens. Without this call, a brand-new Firebase account never gets
    a row here at all, so it never shows up in
    GET /api/admins/registrations and a superadmin has nothing to
    promote. AuthPage calls this right after signUp() resolves, before
    the WELCOME/CONSENT steps even render.

    Backed by the Donor table because that's what list_registrations
    already queries (WHERE firebase_uid IS NOT NULL) — this route just
    exposes it as what it actually is: registering an admin candidate,
    not tracking a donor.

    Idempotent: find_or_create_registered looks up by firebase_uid
    first, then by email, before creating anything — a retried request
    (flaky network, double-click) reuses the same row instead of
    erroring or duplicating it. Always 201 on success, whether the row
    was just created or already existed, since "you are registered" is
    true either way.
    """
    firebase_user = g.firebase_user
    email = firebase_user.get("email")
    if not email:
        return jsonify({"error": "Firebase account has no email on record"}), 400

    name = firebase_user.get("name") or ""
    first_name, _, last_name = name.partition(" ")

    donor = Donor.find_or_create_registered(
        firebase_uid=firebase_user["uid"],
        email=email,
        first_name=first_name or None,
        last_name=last_name or None,
    )
    db.session.commit()

    return jsonify({
        "id": donor.id,
        "email": donor.email,
        "registered_at": donor.created_at.isoformat(),
    }), 201


@auth_bp.get("/me")
@require_firebase_auth
def get_current_identity():
    """Lets the frontend check 'am I an admin, and what state is my
    request in' without a separate round trip. `otp_required` tells the
    frontend whether to show the OTP screen before granting an admin
    session — an active admin who hasn't completed OTP (or whose OTP
    session has expired) still gets is_admin: true, since they ARE an
    admin, just not yet fully signed in.

    There is no path here for a non-admin to become one — becoming an
    admin only happens via a superadmin promoting an existing
    registration (see admins.py). This endpoint just reports status.

    Note: unlike admin-only routes, /me does NOT go through
    require_admin, so it does not renew the OTP session window. That's
    intentional — the frontend polls this defensively (see AuthPage's
    redirectAfterAuth) and polling shouldn't itself keep an otherwise-
    idle session alive."""
    admin = AdminUser.query.filter_by(firebase_uid=g.firebase_user["uid"]).first()
    if admin is None:
        return jsonify({"is_admin": False, "status": None})

    is_active = admin.is_active_admin()
    return jsonify({
        "is_admin": is_active,
        "otp_required": is_active and not admin.is_otp_verified(),
        **admin.to_dict(),
    })


@auth_bp.post("/otp/request")
@require_firebase_auth
def request_otp():
    """
    Mails a fresh 6-digit code to an active admin's email, once they've
    already passed Firebase sign-in. The code is only ever stored as a
    hash and expires after OTP_TTL_MINUTES.
    """
    admin = AdminUser.query.filter_by(firebase_uid=g.firebase_user["uid"]).first()
    if admin is None or not admin.is_active_admin():
        return jsonify({"error": "Administrator access required"}), 403

    now = datetime.now(timezone.utc)
    last_sent_at = _aware(admin.otp_last_sent_at)
    if last_sent_at and (now - last_sent_at).total_seconds() < OTP_RESEND_COOLDOWN_SECONDS:
        return jsonify({"error": "Please wait a moment before requesting another code"}), 429

    otp = generate_otp_code()
    admin.otp_code_hash = hash_token(otp)
    admin.otp_expires_at = now + timedelta(minutes=OTP_TTL_MINUTES)
    admin.otp_attempts = 0
    admin.otp_last_sent_at = now
    db.session.commit()

    sent = email_service.send_otp_email(to_email=admin.email, to_name=admin.name or admin.email, otp=otp)
    if not sent:
        return jsonify({"error": "Failed to send verification code — please try again"}), 502

    return jsonify({"message": "Verification code sent", "expires_in_minutes": OTP_TTL_MINUTES})


@auth_bp.post("/otp/verify")
@require_firebase_auth
def verify_otp():
    """
    Checks the submitted code against the stored hash. On success, opens
    an OTP-verified session window (OTP_SESSION_MINUTES) that require_admin
    checks — and slides forward on activity — on every subsequent admin
    request.
    """
    payload = request.get_json(silent=True) or {}
    code = (payload.get("otp") or "").strip()
    if not code:
        return jsonify({"error": "otp is required"}), 400

    admin = AdminUser.query.filter_by(firebase_uid=g.firebase_user["uid"]).first()
    if admin is None or not admin.is_active_admin():
        return jsonify({"error": "Administrator access required"}), 403

    if not admin.otp_code_hash or not admin.otp_expires_at:
        return jsonify({"error": "No verification code pending — request a new one"}), 400

    now = datetime.now(timezone.utc)
    expires_at = _aware(admin.otp_expires_at)
    if expires_at < now:
        admin.clear_otp_challenge()
        db.session.commit()
        return jsonify({"error": "Verification code expired — request a new one"}), 400

    if admin.otp_attempts >= OTP_MAX_ATTEMPTS:
        admin.clear_otp_challenge()
        db.session.commit()
        return jsonify({"error": "Too many incorrect attempts — request a new code"}), 429

    if not verify_token(code, admin.otp_code_hash):
        admin.otp_attempts += 1
        db.session.commit()
        return jsonify({
            "error": "Incorrect code",
            "attempts_remaining": OTP_MAX_ATTEMPTS - admin.otp_attempts,
        }), 401

    admin.clear_otp_challenge()
    admin.otp_verified_until = now + timedelta(minutes=OTP_SESSION_MINUTES)
    db.session.commit()

    return jsonify({"otp_required": False, **admin.to_dict()})


@auth_bp.post("/logout")
@require_firebase_auth
def logout():
    """
    Ends the OTP-verified admin session server-side. This is what makes
    logout actually mean something: without it, a Bearer token obtained
    before logout (e.g. cached, or a token that hasn't expired yet on
    Firebase's side) could still pass require_admin's OTP check, because
    that check only looks at otp_verified_until in the DB — it has no
    idea the frontend called firebase signOut(). Clearing the window here
    closes that gap immediately, independent of whatever the client does.

    Safe to call even if the caller isn't an admin (e.g. an in-between
    state) — it's a no-op in that case rather than an error, since the
    goal is just "make sure nothing admin-scoped is left open."
    """
    admin = AdminUser.query.filter_by(firebase_uid=g.firebase_user["uid"]).first()
    if admin is not None:
        admin.end_otp_session()
        db.session.commit()
    return jsonify({"message": "Logged out"})