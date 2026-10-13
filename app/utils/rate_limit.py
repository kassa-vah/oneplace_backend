# ============================================================
# FILE BELONGS AT:  app/utils/rate_limit.py
# ============================================================
from flask import g
from flask_limiter.util import get_remote_address


def rate_limit_key():
   
    admin_user = getattr(g, "admin_user", None)
    if admin_user is not None:
        return f"admin:{admin_user.id}"

    firebase_user = getattr(g, "firebase_user", None)
    if firebase_user is not None:
        uid = firebase_user.get("uid")
        if uid:
            return f"firebase:{uid}"

    return get_remote_address()