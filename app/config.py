import os


class Config:
    """Base configuration. Values are pulled from environment variables so
    nothing sensitive ever lives in source control (spec #90)."""

    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-me")

    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        # Render/Supabase Postgres drops idle connections; pre_ping avoids
        # serving requests against a dead connection.
        "pool_pre_ping": True,
    }

    FIREBASE_CREDENTIALS_PATH = os.environ.get("FIREBASE_CREDENTIALS_PATH")

    # Not yet used for real sends — see app/services/email.py. Setting
    # this just flips EmailService.enabled once a real implementation
    # replaces the logging stub.
    BREVO_API_KEY = os.environ.get("BREVO_API_KEY")

    # Gates subscription sign-up (spec: prevent scripted/bot subscription
    # spam). Get this from https://www.google.com/recaptcha/admin.
    RECAPTCHA_SECRET_KEY = os.environ.get("RECAPTCHA_SECRET_KEY")

    CORS_ORIGINS = [
        origin.strip()
        for origin in os.environ.get("CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]

    JSON_SORT_KEYS = False


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "TEST_DATABASE_URL", "sqlite:///:memory:"
    )
    # Automated tests can't reach Google's real siteverify endpoint —
    # this bypass exists ONLY here, never in DevelopmentConfig or
    # ProductionConfig, so it can't accidentally weaken the real check.
    SKIP_RECAPTCHA = True


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
