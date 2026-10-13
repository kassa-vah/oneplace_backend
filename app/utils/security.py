"""
Small crypto/token helpers shared across routes. Nothing here talks to
the database — keeps OTP codes (login) generated and verified the
same way everywhere they're needed.
"""
import hashlib
import hmac
import secrets


def generate_otp_code(length: int = 6) -> str:
    """Cryptographically-secure numeric OTP, zero-padded to `length` digits."""
    upper_bound = 10 ** length
    return f"{secrets.randbelow(upper_bound):0{length}d}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str, token_hash: str) -> bool:
    """
    Timing-safe comparison. Use this instead of `hash_token(x) == y`
    anywhere the comparison matters for security (OTP checks,
    invitation-token redemption) — a plain `==` on hashes leaks tiny
    timing differences an attacker could exploit over many attempts.
    """
    if not token_hash:
        return False
    return hmac.compare_digest(hash_token(token), token_hash)


def generate_idempotency_key() -> str:
    """For server-generated idempotency keys, e.g. when a frontend
    doesn't supply its own (spec #61/#96)."""
    return secrets.token_hex(16)