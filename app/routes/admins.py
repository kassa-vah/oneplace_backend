# ============================================================
# FILE BELONGS AT:  app/routes/admins.py
# ============================================================
from flask import Blueprint, jsonify, request, g

from app.extensions import db
from app.utils.decorators import require_admin, require_superadmin
from app.utils.pagination import paginate_query
from app.models.admin import AdminUser, AdminRole, AdminStatus, record_audit
from app.models.donation import Donor
from app.services.email import email_service


admins_bp = Blueprint("admins", __name__)


def _send_best_effort(fn, *args, **kwargs):
    """Every email call here follows an already-committed DB write —
    a Brevo outage shouldn't roll back an approval/suspension/promotion
    that already happened, and shouldn't 500 a request whose real work
    is done. Swallow and log rather than raise."""
    try:
        fn(*args, **kwargs)
    except Exception:
        import logging
        logging.getLogger("one_place").exception(
            "Best-effort admin notification email failed (action already committed)"
        )


@admins_bp.get("/api/admin/admins")
@require_admin
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

    _send_best_effort(
        email_service.send_admin_approved_email,
        to_email=target.email,
        to_name=target.name or target.email,
    )

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

    _send_best_effort(
        email_service.send_admin_suspended_email,
        to_email=target.email,
        to_name=target.name or target.email,
    )

    return jsonify(target.to_dict())


# --- Registrations -> promote to admin/superadmin ----------------------
# This is the ONLY way an account becomes an admin without an explicit
# invitation: a superadmin looks at who's already registered (Donor
# records with a firebase_uid — i.e. real accounts, not one-time guest
# donors) and promotes one directly. There is no request/approve step
# because the superadmin IS the approval.
#
# Path is /api/admins/registrations (plural "admins") rather than
# /api/admin/... like the rest of this file — that's a deliberate
# mismatch with the rest of the backend's naming, kept ONLY because
# it's the exact contract the frontend (RegistrationsManager) already
# calls. Don't "fix" this to /api/admin/registrations without updating
# the frontend to match.

def _registration_dict(donor, admin_record):
    """Shape matches RegistrationsManager's expected row exactly:
    { id, name, email, registered_at, role, status }. role/status are
    "donor"/"registered" until a superadmin promotes them — this lets
    the frontend compare role ordinally (donor < admin < superadmin)
    to decide which promote buttons to hide."""
    name = " ".join(filter(None, [donor.first_name, donor.last_name])) or None
    return {
        "id": donor.id,
        "name": name,
        "email": donor.email,
        "registered_at": donor.created_at.isoformat(),
        "role": admin_record.role if admin_record else "donor",
        "status": admin_record.status if admin_record else "registered",
    }


@admins_bp.get("/api/admins/registrations")
@require_admin
def list_registrations():
    donors = Donor.query.filter(Donor.firebase_uid.isnot(None)).order_by(Donor.created_at.desc()).all()

    items = []
    for donor in donors:
        admin_record = AdminUser.query.filter_by(firebase_uid=donor.firebase_uid).first()
        items.append(_registration_dict(donor, admin_record))

    # Frontend expects a plain array, not the {items, pagination} envelope
    # used everywhere else in this backend — matching that contract here.
    return jsonify(items)


@admins_bp.post("/api/admins/registrations/<string:donor_id>/promote")
@require_superadmin
def promote_registration(donor_id):
    donor = Donor.query.get(donor_id)
    if donor is None or not donor.firebase_uid:
        return jsonify({"error": "Registration not found"}), 404

    payload = request.get_json(silent=True) or {}
    role = payload.get("role")
    if role not in AdminRole.ALL:
        return jsonify({"error": f"role must be one of {list(AdminRole.ALL)}"}), 400

    # NOTE: no server-side confirm requirement here, unlike an earlier
    # version of this endpoint — the frontend's ConfirmDialog already
    # gates this client-side ("Yes, promote"), and the contract this
    # route matches sends exactly { role }. If you want defense in
    # depth against a forged request bypassing that dialog, add back:
    #   if role == AdminRole.SUPERADMIN and not payload.get("confirm"):
    #       return jsonify({"error": "..."}), 400
    # and have the frontend send confirm: true alongside role.

    admin = AdminUser.query.filter_by(firebase_uid=donor.firebase_uid).first()
    if admin is None:
        admin = AdminUser(firebase_uid=donor.firebase_uid, email=donor.email)
        db.session.add(admin)

    admin.role = role
    admin.status = AdminStatus.ACTIVE
    db.session.flush()

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_promoted_registration",
        resource_type="admin_user",
        resource_id=admin.id,
        description=f"Promoted {donor.email} to {role}",
    )
    db.session.commit()

    _send_best_effort(email_service.send_admin_promotion, donor.email, role)

    # Same shape as list_registrations's rows — the frontend patches
    # this straight into local state after promoting.
    return jsonify(_registration_dict(donor, admin))