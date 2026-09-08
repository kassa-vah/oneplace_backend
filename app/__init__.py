from __future__ import annotations

import os
import logging

from dotenv import load_dotenv

load_dotenv()

from flask import Flask

from app.config import config_by_name
from app.extensions import db, migrate, cors, limiter
from app.error_handlers import register_error_handlers
from app.services.firebase import init_firebase
from app.services.email import email_service


def create_app(config_name: str | None = None) -> Flask:
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

    # Falls back to in-memory storage if RATELIMIT_STORAGE_URI isn't set in
    # config_by_name — fine for a single dev/worker process, but each
    # gunicorn worker in production would then enforce its own separate
    # counters. Set RATELIMIT_STORAGE_URI (e.g. a Redis URL) in your config
    # for multi-worker deployments.
    app.config.setdefault("RATELIMIT_STORAGE_URI", "memory://")
    # Adds Retry-After / X-RateLimit-* headers to every response so a
    # well-behaved client knows its remaining quota and exactly when to
    # retry after a 429, instead of guessing or polling blindly.
    app.config.setdefault("RATELIMIT_HEADERS_ENABLED", True)
    # Off by default under the test config — a test suite that hammers an
    # endpoint in a loop shouldn't start failing on 429s instead of the
    # thing it's actually testing. Override in config_by_name if you want
    # a specific test to exercise the limiter.
    app.config.setdefault("RATELIMIT_ENABLED", config_name != "testing")

    db.init_app(app)
    migrate.init_app(app, db)
    cors.init_app(app, origins=app.config["CORS_ORIGINS"] or "*")
    email_service.init_app(app)
    limiter.init_app(app)

    
    from app import models  # noqa: F401

    if config_name != "testing":
        init_firebase(app)

    
    from app.routes.health import health_bp
    from app.routes.causes import causes_bp
    from app.routes.beneficiaries import beneficiaries_bp
    from app.routes.donations import donations_bp
    from app.routes.auth import auth_bp
    from app.routes.admins import admins_bp
    from app.routes.content import content_bp
    from app.routes.metrics import metrics_bp
    from app.routes.newsletter import newsletter_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(causes_bp)
    app.register_blueprint(beneficiaries_bp)
    app.register_blueprint(donations_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admins_bp)
    app.register_blueprint(content_bp)
    app.register_blueprint(metrics_bp)
    app.register_blueprint(newsletter_bp)

    register_error_handlers(app)

    from app.cli import register_cli
    register_cli(app)

    return app