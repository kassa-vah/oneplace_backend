import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials

_firebase_app = None


def init_firebase(app):
    """Initialize the Firebase Admin SDK once, using the service account
    path from config. Safe to call multiple times (e.g. under the reloader)."""
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    cred_path = app.config.get("FIREBASE_CREDENTIALS_PATH")
    if not cred_path:
        app.logger.warning(
            "FIREBASE_CREDENTIALS_PATH not set — Firebase auth routes will fail "
            "until this is configured."
        )
        return None

    cred = credentials.Certificate(cred_path)
    _firebase_app = firebase_admin.initialize_app(cred)
    return _firebase_app


class InvalidFirebaseToken(Exception):
    pass


def verify_id_token(id_token: str) -> dict:
    """Verify a Firebase ID token and return its decoded claims.

    Raises InvalidFirebaseToken on any failure — callers should not need
    to know about firebase_admin's specific exception types.
    """
    if not id_token:
        raise InvalidFirebaseToken("Missing token")

    try:
        return firebase_auth.verify_id_token(id_token)
    except Exception as exc:  # noqa: BLE001 — deliberately broad; see docstring
        raise InvalidFirebaseToken(str(exc)) from exc
