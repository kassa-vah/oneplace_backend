from flask import Blueprint, jsonify

from app.extensions import db

health_bp = Blueprint("health", __name__)


@health_bp.get("/health")
def health():
    db_status = "ok"
    try:
        db.session.execute(db.text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_status = "unreachable"

    return jsonify({"status": "ok", "database": db_status})
