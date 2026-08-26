"""
Google reCAPTCHA verification. Gates subscription sign-up so it can't
be trivially scripted — a bot can hit an unauthenticated-looking form
even though the endpoint itself requires Firebase auth, since Firebase
accounts themselves are easy to script-create.

Set RECAPTCHA_SECRET_KEY (from https://www.google.com/recaptcha/admin)
to enable real verification. With no secret key configured, this fails
CLOSED (returns False) rather than silently accepting unverified
submissions — a missing config should never look like a passing check.
"""
import requests

RECAPTCHA_VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"


def verify_recaptcha(token, secret_key, remote_ip=None) -> bool:
    if not token or not secret_key:
        return False

    payload = {"secret": secret_key, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        response = requests.post(RECAPTCHA_VERIFY_URL, data=payload, timeout=5)
        result = response.json()
    except (requests.RequestException, ValueError):
        return False

    return bool(result.get("success"))
