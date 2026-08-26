from flask import Blueprint, jsonify, request, g

from app.extensions import db
from app.utils.decorators import require_firebase_auth
from app.utils.security import hash_token
from app.models.admin import AdminUser, AdminStatus, AdminInvitation, InvitationStatus, record_audit
from app.models.donation import normalize_email


auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@auth_bp.get("/me")
@require_firebase_auth
def get_current_identity():
    """Lets the frontend check 'am I an admin, and what state is my
    request in' without a separate round trip."""
    admin = AdminUser.query.filter_by(firebase_uid=g.firebase_user["uid"]).first()
    if admin is None:
        return jsonify({"is_admin": False, "status": None})
    return jsonify({"is_admin": admin.is_active_admin(), **admin.to_dict()})


@auth_bp.post("/invitations/accept")
@require_firebase_auth
def accept_invitation():
    """
    The other half of spec #72's preferred flow: a superadmin invited
    this email (see admins.py), and the invitee redeems the plaintext
    token here while signed into Firebase. The token is only ever
    compared by its hash — never stored or logged in the clear.
    """
    payload = request.get_json(silent=True) or {}
    token = payload.get("token")
    if not token:
        return jsonify({"error": "token is required"}), 400

    invitation = AdminInvitation.query.filter_by(
        token_hash=hash_token(token), status=InvitationStatus.PENDING
    ).first()
    if invitation is None:
        return jsonify({"error": "Invalid or already-used invitation"}), 400

    from datetime import datetime, timezone

    if invitation.expires_at < datetime.now(timezone.utc):
        invitation.status = InvitationStatus.EXPIRED
        db.session.commit()
        return jsonify({"error": "This invitation has expired"}), 400

    firebase_uid = g.firebase_user["uid"]
    firebase_email = normalize_email(g.firebase_user.get("email", ""))
    if firebase_email != normalize_email(invitation.email):
        return jsonify({"error": "This invitation was issued to a different email"}), 403

    admin = AdminUser.query.filter_by(firebase_uid=firebase_uid).first()
    if admin is None:
        admin = AdminUser(firebase_uid=firebase_uid, email=firebase_email)
        db.session.add(admin)

    admin.role = invitation.role
    admin.status = AdminStatus.ACTIVE

    invitation.status = InvitationStatus.ACCEPTED
    invitation.accepted_at = datetime.now(timezone.utc)
    db.session.flush()  # ensures admin.id is populated for new admins

    record_audit(
        admin_id=admin.id,
        action="admin_accepted_invitation",
        resource_type="admin_invitation",
        resource_id=invitation.id,
        description=f"{firebase_email} accepted invitation for role {invitation.role}",
    )
    db.session.commit()

    return jsonify(admin.to_dict())
