# ============================================================
# FILE BELONGS AT:  tests/conftest.py
# ============================================================
import os

os.environ["FLASK_ENV"] = "testing"
os.environ["TEST_DATABASE_URL"] = "sqlite:///:memory:"

import pytest

from app import create_app
from app.extensions import db
from app.models.cause import Cause, CauseStatus
from app.models.beneficiary import Beneficiary
from app.models.admin import AdminUser, AdminRole, AdminStatus
from app.models.donation import Donor


@pytest.fixture()
def app():
    application = create_app("testing")

    with application.app_context():
        db.create_all()

        beneficiary = Beneficiary(name="Test Beneficiary")
        db.session.add(beneficiary)
        db.session.flush()

        cause = Cause(
            title="Test Cause",
            slug="test-cause",
            status=CauseStatus.PUBLISHED,
            beneficiary_id=beneficiary.id,
            currency="USD",
        )
        db.session.add(cause)

        superadmin = AdminUser(
            firebase_uid="uid-super",
            email="super@example.com",
            role=AdminRole.SUPERADMIN,
            status=AdminStatus.ACTIVE,
        )
        db.session.add(superadmin)
        db.session.commit()

        application.config["TEST_CAUSE_ID"] = cause.id

        yield application

        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def cause_id(app):
    return app.config["TEST_CAUSE_ID"]


@pytest.fixture()
def firebase_identity(monkeypatch):
    """
    Both app.services.firebase and app.utils.decorators import
    verify_id_token directly (decorators.py does `from
    app.services.firebase import verify_id_token`), so both references
    need patching.

    Identity is encoded directly in the fake token string
    ("uid|email"), NOT in shared fixture state — a shared mutable
    dict here would mean whichever of super_headers/donor_headers gets
    set up LAST in a given test silently wins for every request in
    that test, regardless of which headers dict you actually pass.
    Encoding identity in the token itself lets multiple identities
    coexist correctly within a single test.
    """
    import app.services.firebase as fb_module
    import app.utils.decorators as dec_module

    def fake_verify(token):
        uid, _, email = token.partition("|")
        return {"uid": uid, "email": email}

    monkeypatch.setattr(fb_module, "verify_id_token", fake_verify)
    monkeypatch.setattr(dec_module, "verify_id_token", fake_verify)

    def headers_for(uid, email):
        return {"Authorization": f"Bearer {uid}|{email}"}

    return headers_for


@pytest.fixture()
def super_headers(firebase_identity):
    return firebase_identity("uid-super", "super@example.com")


@pytest.fixture()
def donor_headers(firebase_identity):
    return firebase_identity("uid-donor-1", "donor1@example.com")


@pytest.fixture()
def registered_donor(app, firebase_identity):
    """A Firebase account that's already completed the consent/
    registration step — i.e. a candidate a superadmin could promote."""
    with app.app_context():
        donor = Donor.find_or_create_registered(firebase_uid="uid-donor-1", email="donor1@example.com")
        from datetime import datetime, timezone
        donor.consent_accepted_at = datetime.now(timezone.utc)
        donor.consent_version = "v1"
        db.session.commit()
        donor_id = donor.id
    return donor_id