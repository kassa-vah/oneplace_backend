import os


class Config:
    """Base configuration. Values are pulled from environment variables so
    nothing sensitive ever lives in source control (spec #90)."""

    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-me")

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        # Render/Supabase Postgres drops idle connections; pre_ping avoids
        # serving requests against a dead connection.
        "pool_pre_ping": True,
    }

    FIREBASE_CREDENTIALS_PATH = os.environ.get("FIREBASE_CREDENTIALS_PATH")
    SUPERADMIN_FIREBASE_UID = os.environ.get("SUPERADMIN_FIREBASE_UID")

    # Not yet used for real sends — see app/services/email.py. Setting
    # this just flips EmailService.enabled once a real implementation
    # replaces the logging stub.
    BREVO_API_KEY = os.environ.get("BREVO_API_KEY")

    CORS_ORIGINS = [
        origin.strip()
        for origin in os.environ.get("CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]

    JSON_SORT_KEYS = False


class DevelopmentConfig(Config):
    DEBUG = True

    # SQLite for local development.
    # DATABASE_URL can still override this if needed.
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "sqlite:///oneplace.db"
    )


class ProductionConfig(Config):
    DEBUG = False

    # PostgreSQL in production.
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")

    # Support providers that still return postgres:// URLs.
    if SQLALCHEMY_DATABASE_URI and SQLALCHEMY_DATABASE_URI.startswith(
        "postgres://"
    ):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace(
            "postgres://",
            "postgresql://",
            1
        )


class TestingConfig(Config):
    TESTING = True

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "TEST_DATABASE_URL",
        "sqlite:///:memory:"
    )


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}