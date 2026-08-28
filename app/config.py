
# ============================================================
# FILE BELONGS AT:  app/config.py
# ============================================================
import os


class Config:
    """Base configuration."""

    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-me")

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    FIREBASE_CREDENTIALS_PATH = os.environ.get(
        "FIREBASE_CREDENTIALS_PATH"
    )

    BREVO_API_KEY = os.environ.get("BREVO_API_KEY")

    CORS_ORIGINS = [
        origin.strip()
        for origin in os.environ.get("CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]

    JSON_SORT_KEYS = False


class DevelopmentConfig(Config):
    DEBUG = True

    # Local development database.
    SQLALCHEMY_DATABASE_URI = "sqlite:///oneplace.db"

    # SQLite does not need PostgreSQL connection-pool settings.
    SQLALCHEMY_ENGINE_OPTIONS = {}


class ProductionConfig(Config):
    DEBUG = False

    # PostgreSQL will be configured through DATABASE_URL when deployed.
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")

    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
    }


class TestingConfig(Config):
    TESTING = True

    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}

