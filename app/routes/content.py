from flask import Blueprint, jsonify, request, g

from app.extensions import db
from app.utils.decorators import require_admin
from app.utils.pagination import paginate_query
from app.models.content import ContactMessage
from app.models.admin import record_audit
from app.services.email import email_service

content_bp = Blueprint("content", __name__)


@content_bp.post("/api/contact")
def submit_contact_message():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    email = (payload.get("email") or "").strip()
    message = (payload.get("message") or "").strip()

    if not name or not email or not message:
        return jsonify({"error": "name, email, and message are required"}), 400

    contact_message = ContactMessage(
        name=name, email=email, subject=payload.get("subject"), message=message
    )
    db.session.add(contact_message)
    db.session.commit()

    email_service.send_contact_ack(contact_message)

    return jsonify({"status": "received"}), 201


@content_bp.get("/api/admin/contact-messages")
@require_admin
def list_contact_messages():
    query = ContactMessage.query

    resolved_param = request.args.get("resolved")
    if resolved_param is not None:
        query = query.filter_by(resolved=resolved_param.lower() == "true")

    query = query.order_by(ContactMessage.created_at.desc())
    result = paginate_query(query)

    return jsonify(
        {
            "items": [m.to_admin_dict() for m in result["items"]],
            "pagination": result["pagination"],
        }
    )


@content_bp.post("/api/admin/contact-messages/<string:message_id>/resolve")
@require_admin
def resolve_contact_message(message_id):
    message = ContactMessage.query.get(message_id)
    if message is None:
        return jsonify({"error": "Message not found"}), 404

    message.resolved = True

    record_audit(
        admin_id=g.admin_user.id,
        action="admin_resolved_contact_message",
        resource_type="contact_message",
        resource_id=message.id,
    )
    db.session.commit()
    return jsonify(message.to_admin_dict())
