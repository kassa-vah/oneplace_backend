import base64
import io
from datetime import datetime, timedelta, timezone

import pyotp
import qrcode
from flask import Blueprint, jsonify, request, g

from app.extensions import db, limiter
from app.utils.decorators import require_admin, require_firebase_auth, OTP_SESSION_MINUTES
from app.utils.security import hash_token, verify_token, generate_otp_code
from app.models.admin import AdminUser, record_audit
from app.models.donation import Donor
from app.services.email import email_service


auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN_SECONDS = 30

# Issuer name shown inside the authenticator app next to the account —
# purely cosmetic, doesn't affect verification.
TOTP_ISSUER_NAME = "One Place, Inc."


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


def _build_totp_qr_base64(secret: str, email: str) -> str:
    """Generate a TOTP provisioning URI and return it as a base64-encoded PNG QR code."""
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name=TOTP_ISSUER_NAME)
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _verify_totp_code(admin: AdminUser, code: str) -> bool:
    """Verify a 6-digit TOTP code against an admin's ALREADY-CONFIRMED
    secret. Deliberately requires totp_enabled — during initial setup
    (before confirmation) totp_confirm() below verifies the raw secret
    directly instead, since totp_enabled is still False at that point."""
    if not admin.totp_secret or not admin.totp_enabled:
        return False
    return pyotp.TOTP(admin.totp_secret).verify(code, valid_window=1)


def _open_otp_session(admin: AdminUser) -> None:
    """Shared by both login-gate verify routes (email OTP and TOTP
    login) — opens the same otp_verified_until window either way, so
    require_admin treats a session opened by an authenticator code
    identically to one opened by an emailed code."""
    admin.otp_verified_until = datetime.now(timezone.utc) + timedelta(minutes=OTP_SESSION_MINUTES)


@auth_bp.post("/register")
@require_firebase_auth
@limiter.limit("10 per hour")
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

    Rate-limited (10/hour, keyed by Firebase UID) — this is an
    unauthenticated-in-effect entry point for anyone with a valid
    Firebase token, so it's the cheapest place for someone to hammer
    the DB with retried signups.
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
@limiter.limit("60 per minute")
def get_current_identity():
    """Lets the frontend check 'am I an admin, and what state is my
    request in' without a separate round trip. `otp_required` tells the
    frontend whether to show the per-login OTP screen (email code, or
    authenticator code once TOTP is set up — the frontend decides which
    based on how the admin signed in). Separately, `totp_setup_required`
    tells the frontend whether the one-time, unskippable authenticator
    setup step still needs to happen — this stays true across every
    login until the admin confirms TOTP once, then stays false forever
    after, regardless of OTP session state. An active admin who hasn't
    completed OTP (or whose OTP session has expired) still gets
    is_admin: true, since they ARE an admin, just not yet fully signed
    in.

    There is no path here for a non-admin to become one — becoming an
    admin only happens via a superadmin promoting an existing
    registration (see admins.py). This endpoint just reports status.

    Note: unlike admin-only routes, /me does NOT go through
    require_admin, so it does not renew the OTP session window. That's
    intentional — the frontend polls this defensively (see AuthPage's
    redirectAfterAuth) and polling shouldn't itself keep an otherwise-
    idle session alive.

    Rate-limited generously (60/minute) since this is a defensively-
    polled endpoint — the limit exists to catch a runaway polling loop
    or abuse, not to interfere with normal use.
    """
    admin = AdminUser.query.filter_by(firebase_uid=g.firebase_user["uid"]).first()
    if admin is None:
        return jsonify({"is_admin": False, "status": None})

    is_active = admin.is_active_admin()
    return jsonify({
        "is_admin": is_active,
        "otp_required": is_active and not admin.is_otp_verified(),
        "totp_setup_required": is_active and not admin.totp_enabled,
        **admin.to_dict(),
    })


@auth_bp.post("/otp/request")
@require_firebase_auth
@limiter.limit("10 per hour")
def request_otp():
    """
    Mails a fresh 6-digit code to an active admin's email, once they've
    already passed Firebase sign-in. The code is only ever stored as a
    hash and expires after OTP_TTL_MINUTES.

    Rate-limited on top of the existing OTP_RESEND_COOLDOWN_SECONDS
    cooldown — the cooldown stops rapid-fire resends, but a caller could
    still wait it out and grind through it dozens of times an hour,
    which is what this caps (and it protects Brevo send volume/cost).
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
@limiter.limit("6 per day")
def verify_otp():
    """
    Checks the submitted code against the stored hash. On success, opens
    an OTP-verified session window (OTP_SESSION_MINUTES) that require_admin
    checks — and slides forward on activity — on every subsequent admin
    request.

    Rate-limited to 6/day (keyed by Firebase UID) — this is effectively
    the "complete login" step, so it gets the strict daily cap. It
    stacks with OTP_MAX_ATTEMPTS (5 wrong guesses burns the current
    code), so a caller can't burn through a fresh code every few
    minutes all day; they get 6 completion attempts total, successful
    or not.
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
    _open_otp_session(admin)
    db.session.commit()

    return jsonify({
        "otp_required": False,
        "totp_setup_required": not admin.totp_enabled,
        **admin.to_dict(),
    })


@auth_bp.post("/totp/verify")
@require_firebase_auth
@limiter.limit("20 per hour")
def totp_login_verify():
    """
    Per-login session gate for admins who've already completed TOTP
    setup (totp_enabled) and are signing in via a method the frontend
    treats as authenticator-gated rather than email-OTP-gated —
    currently: Google. Checks the submitted 6-digit authenticator code
    and, on success, opens the exact same otp_verified_until session
    window that email OTP verification opens (see _open_otp_session),
    so require_admin can't tell the difference afterward.

    Deliberately a separate endpoint from /otp/verify (email code)
    rather than a shared one — the two need very different rate limits
    (this one is the normal per-login path for authenticator-gated
    admins, so it can't be capped as tightly as the 6/day email-OTP
    limit), and the frontend already knows which one to call based on
    how the admin signed in.

    Requires only basic Firebase auth (not @require_admin) — same as
    /otp/verify — because this IS what opens the admin session; there's
    no OTP-verified session yet to require.
    """
    payload = request.get_json(silent=True) or {}
    code = str(payload.get("code") or "").strip()
    if len(code) != 6 or not code.isdigit():
        return jsonify({"error": "code must be exactly 6 digits"}), 400

    admin = AdminUser.query.filter_by(firebase_uid=g.firebase_user["uid"]).first()
    if admin is None or not admin.is_active_admin():
        return jsonify({"error": "Administrator access required"}), 403
    if not admin.totp_enabled:
        return jsonify({"error": "Authenticator app is not set up on this account yet"}), 400

    if not _verify_totp_code(admin, code):
        return jsonify({"error": "Incorrect code"}), 401

    _open_otp_session(admin)
    db.session.commit()

    return jsonify({
        "otp_required": False,
        "totp_setup_required": not admin.totp_enabled,
        **admin.to_dict(),
    })


@auth_bp.post("/totp/setup")
@require_admin
@limiter.limit("10 per hour")
def totp_setup():
    """
    Generates a fresh TOTP secret for the authenticated admin and returns:
      - secret    (base32 string, shown as a manual-entry backup)
      - qr_code   (base64-encoded PNG — render as <img src="data:image/png;base64,...">)
      - totp_uri  (otpauth:// URI — optional, for manual entry)

    The secret is stored immediately but totp_enabled stays False until
    the admin calls POST /api/auth/totp/confirm with a valid code,
    proving they actually scanned the QR (not just that a secret
    exists). This is the mandatory onboarding security step — the
    frontend keeps the admin on this screen (per totp_setup_required
    from /me) until confirmation succeeds.

    Gated by @require_admin rather than bare Firebase auth: setup only
    makes sense once an admin has an active, OTP-verified session, same
    as every other admin-only action.
    """
    admin = g.admin_user

    secret = pyotp.random_base32()
    admin.totp_secret = secret
    admin.totp_enabled = False  # not active until confirmed
    db.session.commit()

    qr_b64 = _build_totp_qr_base64(secret, admin.email)
    totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(name=admin.email, issuer_name=TOTP_ISSUER_NAME)

    return jsonify({
        "secret": secret,
        "qr_code": qr_b64,
        "totp_uri": totp_uri,
    })


@auth_bp.post("/totp/confirm")
@require_admin
@limiter.limit("10 per hour")
def totp_confirm():
    """
    Activates TOTP for the authenticated admin by verifying the first
    code entered from their authenticator app.

    Body: { "code": "123456" }

    Flips totp_enabled to True on success — the one and only thing that
    turns totp_setup_required (from /me) to False, permanently, for
    this admin. This is the "approval" the frontend's onboarding gate
    is waiting on before it unlocks the dashboard.
    """
    admin = g.admin_user
    payload = request.get_json(silent=True) or {}
    code = str(payload.get("code") or "").strip()

    if len(code) != 6 or not code.isdigit():
        return jsonify({"error": "code must be exactly 6 digits"}), 400

    if not admin.totp_secret:
        return jsonify({"error": "No TOTP secret found — call POST /api/auth/totp/setup first"}), 400

    # Verified against the raw secret directly (not _verify_totp_code)
    # because totp_enabled is still False at this point in the flow.
    if not pyotp.TOTP(admin.totp_secret).verify(code, valid_window=1):
        return jsonify({
            "error": "Code is incorrect or expired. Make sure your device's time is accurate and try again."
        }), 400

    admin.totp_enabled = True
    record_audit(
        admin_id=admin.id,
        action="admin_enabled_totp",
        resource_type="admin_user",
        resource_id=admin.id,
        description=f"{admin.email} linked an authenticator app",
    )
    db.session.commit()

    return jsonify({"totp_enabled": True})


@auth_bp.delete("/totp/disable")
@require_admin
@limiter.limit("5 per hour")
def totp_disable():
    """
    Removes TOTP from the authenticated admin's account. Requires the
    current TOTP code as confirmation — so a stolen/replayed Bearer
    token alone can't strip MFA off an account without also having the
    admin's actual authenticator app.

    Body: { "code": "123456" }

    Note: this puts the admin back into totp_setup_required territory
    (per /me) — the onboarding gate will block dashboard access again
    until they set TOTP up again.
    """
    admin = g.admin_user
    payload = request.get_json(silent=True) or {}
    code = str(payload.get("code") or "").strip()

    if not admin.totp_enabled:
        return jsonify({"error": "TOTP is not enabled on this account"}), 400
    if len(code) != 6 or not code.isdigit():
        return jsonify({"error": "Provide your current authenticator code to confirm"}), 400
    if not _verify_totp_code(admin, code):
        return jsonify({"error": "Incorrect code — TOTP not disabled"}), 400

    admin.clear_totp()
    record_audit(
        admin_id=admin.id,
        action="admin_disabled_totp",
        resource_type="admin_user",
        resource_id=admin.id,
        description=f"{admin.email} removed their authenticator app",
    )
    db.session.commit()

    return jsonify({"totp_enabled": False})


@auth_bp.post("/logout")
@require_firebase_auth
@limiter.limit("30 per hour")
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