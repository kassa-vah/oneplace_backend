from __future__ import annotations

import os
import logging

from flask import Flask
from dotenv import load_dotenv

from app.config import config_by_name
from app.extensions import db, migrate, cors
from app.error_handlers import register_error_handlers
from app.services.firebase import init_firebase
from app.services.email import email_service


def create_app(config_name: str | None = None) -> Flask:
    load_dotenv()

    config_name = config_name or os.environ.get("FLASK_ENV", "development")
    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])

    logging.basicConfig(level=logging.INFO)

    if config_name != "testing" and not app.config.get("SQLALCHEMY_DATABASE_URI"):
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill in "
            "DATABASE_URL with your Postgres connection string (from Supabase "
            "or Render), then try again."
        )

    db.init_app(app)
    migrate.init_app(app, db)
    cors.init_app(app, origins=app.config["CORS_ORIGINS"] or "*")
    email_service.init_app(app)

    # Import models so Alembic/SQLAlchemy registers all tables before
    # migrations run.
    from app import models  # noqa: F401

    if config_name != "testing":
        init_firebase(app)

    # One blueprint per resource — public/self-service and admin routes
    # for the same resource live in the same file (see route comments
    # for why), told apart by path and by @require_admin/@require_superadmin.
    from app.routes.health import health_bp
    from app.routes.causes import causes_bp
    from app.routes.beneficiaries import beneficiaries_bp
    from app.routes.donations import donations_bp
    from app.routes.subscriptions import subscriptions_bp
    from app.routes.auth import auth_bp
    from app.routes.admins import admins_bp
    from app.routes.content import content_bp
    from app.routes.metrics import metrics_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(causes_bp)
    app.register_blueprint(beneficiaries_bp)
    app.register_blueprint(donations_bp)
    app.register_blueprint(subscriptions_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admins_bp)
    app.register_blueprint(content_bp)
    app.register_blueprint(metrics_bp)

    register_error_handlers(app)

    from app.cli import register_cli
    register_cli(app)

    return app
