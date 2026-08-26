from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request, g

from app.extensions import db
from app.utils.decorators import require_superadmin
from app.utils.pagination import paginate_query
from app.utils.security import generate_invitation_token, hash_token
from app.models.admin import AdminUser, AdminRole, AdminStatus, AdminInvitation, InvitationStatus, record_audit
from app.models.donation import Donor, normalize_email
from app.services.email import email_service

# Superadmin-only management of OTHER admins: approve, suspend, invite,
# and — the main path now — promote an existing registered user
# directly. Everything here is something a superadmin does TO someone
# else's account — contrast with auth.py, which is what a signed-in
# user does to their OWN account (check status, accept an invite).
# There is deliberately no self-service "become an admin" request
# anywhere: the public has no way to discover or trigger anything here.
admins_bp = Blueprint("admins", __name__)


@admins_bp.get("/api/admin/admins")
@require_superadmin
def list_admins():
    query = AdminUser.query

    status = request.args.get("status")
    if status:
        if status not in AdminStatus.ALL:
            return jsonify({"error": f"Invalid status filter '{status}'"}), 400
        query = query.filter_by(status=status)

    query = query.order_by(AdminUser.created_at.desc())
    result = paginate_query(query)

    return jsonify(
        {
            "items": [a.to_dict() for a in result["items"]],
            "pagination": result["pagination"],
        }
    )


@admins_bp.post("/api/admin/admins/<string:admin_id>/approve")
@require_superadmin
def approve_admin(admin_id):
    target = AdminUser.query.get(admin_id)
    if target is None:
        return jsonify({"error": "Admin not found"}), 404

    target.status = AdminStatus.ACTIVE

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_approved_admin",
        resource_type="admin_user",
        resource_id=target.id,
        description=f"Approved admin access for {target.email}",
    )
    db.session.commit()
    return jsonify(target.to_dict())


@admins_bp.post("/api/admin/admins/<string:admin_id>/suspend")
@require_superadmin
def suspend_admin(admin_id):
    target = AdminUser.query.get(admin_id)
    if target is None:
        return jsonify({"error": "Admin not found"}), 404

    if target.id == g.admin_user.id:
        return jsonify({"error": "You cannot suspend your own account"}), 400

    target.status = AdminStatus.SUSPENDED

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_suspended_admin",
        resource_type="admin_user",
        resource_id=target.id,
        description=f"Suspended admin access for {target.email}",
    )
    db.session.commit()
    return jsonify(target.to_dict())


# --- Registered users -> promote to admin/superadmin -------------------
# This is the ONLY way an account becomes an admin without an explicit
# invitation: a superadmin looks at who's already registered (Donor
# records with a firebase_uid — i.e. real accounts, not one-time guest
# donors) and promotes one directly. There is no request/approve step
# because the superadmin IS the approval.

@admins_bp.get("/api/admin/registered-users")
@require_superadmin
def list_registered_users():
    query = Donor.query.filter(Donor.firebase_uid.isnot(None)).order_by(Donor.created_at.desc())
    result = paginate_query(query)

    items = []
    for donor in result["items"]:
        admin_record = AdminUser.query.filter_by(firebase_uid=donor.firebase_uid).first()
        item = donor.to_admin_dict()
        item["admin_role"] = admin_record.role if admin_record else None
        item["admin_status"] = admin_record.status if admin_record else None
        items.append(item)

    return jsonify({"items": items, "pagination": result["pagination"]})


@admins_bp.post("/api/admin/registered-users/<string:donor_id>/promote")
@require_superadmin
def promote_registered_user(donor_id):
    donor = Donor.query.get(donor_id)
    if donor is None or not donor.firebase_uid:
        return jsonify({"error": "Registered user not found"}), 404

    payload = request.get_json(silent=True) or {}
    role = payload.get("role")
    if role not in AdminRole.ALL:
        return jsonify({"error": f"role must be one of {list(AdminRole.ALL)}"}), 400

    # Promoting to superadmin is the highest-privilege action in the
    # system — require an explicit confirmation flag in the request
    # body, not just a frontend "are you sure?" dialog the backend
    # can't verify actually happened.
    if role == AdminRole.SUPERADMIN and not payload.get("confirm"):
        return jsonify({"error": "Promoting to superadmin requires confirm: true in the request body"}), 400

    admin = AdminUser.query.filter_by(firebase_uid=donor.firebase_uid).first()
    if admin is None:
        admin = AdminUser(firebase_uid=donor.firebase_uid, email=donor.email)
        db.session.add(admin)

    admin.role = role
    admin.status = AdminStatus.ACTIVE
    db.session.flush()

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_promoted_registered_user",
        resource_type="admin_user",
        resource_id=admin.id,
        description=f"Promoted {donor.email} to {role}",
    )
    db.session.commit()

    email_service.send_admin_promotion(donor.email, role)

    return jsonify(admin.to_dict())


# --- Invitations (spec #72's preferred flow) ---------------------------
# Still superadmin-only management of someone else's future access, so
# it stays in this same blueprint rather than getting its own.

@admins_bp.post("/api/admin/invitations")
@require_superadmin
def create_invitation():
    payload = request.get_json(silent=True) or {}
    email = payload.get("email")
    role = payload.get("role", AdminRole.ADMIN)

    if not email:
        return jsonify({"error": "email is required"}), 400
    if role not in AdminRole.ALL:
        return jsonify({"error": f"Invalid role '{role}'"}), 400

    token = generate_invitation_token()

    invitation = AdminInvitation(
        email=normalize_email(email),
        role=role,
        token_hash=hash_token(token),
        invited_by=g.admin_user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.session.add(invitation)
    db.session.flush()

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_invited_admin",
        resource_type="admin_invitation",
        resource_id=invitation.id,
        description=f"Invited {invitation.email} as {role}",
    )
    db.session.commit()

    # In dev, the frontend needs the plaintext token to build the invite
    # link — it's returned here once and never persisted. Once the email
    # service is fully wired, stop returning it and only email it.
    email_service.send_admin_invitation(invitation.email, invite_link=f"/accept-invite?token={token}")

    response = invitation.to_dict()
    response["token"] = token
    return jsonify(response), 201


@admins_bp.get("/api/admin/invitations")
@require_superadmin
def list_invitations():
    query = AdminInvitation.query

    status = request.args.get("status")
    if status:
        if status not in InvitationStatus.ALL:
            return jsonify({"error": f"Invalid status filter '{status}'"}), 400
        query = query.filter_by(status=status)

    query = query.order_by(AdminInvitation.created_at.desc())
    result = paginate_query(query)

    return jsonify(
        {
            "items": [i.to_dict() for i in result["items"]],
            "pagination": result["pagination"],
        }
    )


@admins_bp.post("/api/admin/invitations/<string:invitation_id>/revoke")
@require_superadmin
def revoke_invitation(invitation_id):
    invitation = AdminInvitation.query.get(invitation_id)
    if invitation is None:
        return jsonify({"error": "Invitation not found"}), 404
    if invitation.status != InvitationStatus.PENDING:
        return jsonify({"error": f"Cannot revoke an invitation with status '{invitation.status}'"}), 400

    invitation.status = InvitationStatus.REVOKED

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_revoked_invitation",
        resource_type="admin_invitation",
        resource_id=invitation.id,
    )
    db.session.commit()
    return jsonify(invitation.to_dict())
