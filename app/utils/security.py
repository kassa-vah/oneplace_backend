"""
Small crypto/token helpers shared across routes. Nothing here talks to
the database — keeps admin invitation tokens and idempotency keys
generated the same way everywhere they're needed.
"""
import hashlib
import secrets


def generate_invitation_token() -> str:
    """Plaintext token handed to the invitee once. Never stored as-is —
    only its hash is persisted (spec #73)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_idempotency_key() -> str:
    """For server-generated idempotency keys, e.g. when a frontend
    doesn't supply its own (spec #61/#96)."""
    return secrets.token_hex(16)
