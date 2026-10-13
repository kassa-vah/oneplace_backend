# ============================================================
# FILE BELONGS AT:  tests/test_admins.py
# ============================================================
"""
Registrations -> promote flow. This is now the ONLY way an account
becomes an admin: no invitation tokens, no self-service request.
"""


def test_registrations_requires_superadmin(client):
    r = client.get("/api/admins/registrations")
    assert r.status_code == 401


def test_registrations_lists_signed_in_accounts(client, super_headers, registered_donor):
    r = client.get("/api/admins/registrations", headers=super_headers)
    assert r.status_code == 200
    items = r.get_json()
    assert isinstance(items, list)
    assert any(item["id"] == registered_donor for item in items)
    assert items[0]["role"] == "donor"
    assert items[0]["status"] == "registered"


def test_promote_to_admin_is_active_immediately(client, super_headers, registered_donor, donor_headers):
    r = client.post(f"/api/admins/registrations/{registered_donor}/promote", json={"role": "admin"}, headers=super_headers)
    assert r.status_code == 200
    assert r.get_json()["role"] == "admin"
    assert r.get_json()["status"] == "active"

    # No separate approval step — works right away.
    r2 = client.get("/api/admin/causes", headers=donor_headers)
    assert r2.status_code == 200


def test_promote_to_superadmin_no_confirm_required(client, super_headers, registered_donor):
    """Contract intentionally matches the frontend exactly: { role }.
    No server-side 'confirm' flag — the frontend's own ConfirmDialog
    is the safeguard."""
    r = client.post(f"/api/admins/registrations/{registered_donor}/promote", json={"role": "superadmin"}, headers=super_headers)
    assert r.status_code == 200
    assert r.get_json()["role"] == "superadmin"


def test_promote_invalid_role_rejected(client, super_headers, registered_donor):
    r = client.post(f"/api/admins/registrations/{registered_donor}/promote", json={"role": "god"}, headers=super_headers)
    assert r.status_code == 400


def test_regular_admin_cannot_promote(client, super_headers, registered_donor, donor_headers):
    client.post(f"/api/admins/registrations/{registered_donor}/promote", json={"role": "admin"}, headers=super_headers)
    r = client.get("/api/admins/registrations", headers=donor_headers)
    assert r.status_code == 403


def test_no_invitation_endpoints_exist(client, super_headers):
    """The invitation system was removed entirely — these routes
    should no longer exist."""
    assert client.get("/api/admin/invitations", headers=super_headers).status_code == 404
    assert client.post("/api/admin/invitations", headers=super_headers).status_code == 404
    assert client.post("/api/auth/invitations/accept", json={"token": "x"}).status_code == 404


def test_superadmin_can_suspend_admin(client, super_headers, registered_donor, donor_headers):
    client.post(f"/api/admins/registrations/{registered_donor}/promote", json={"role": "admin"}, headers=super_headers)

    r = client.get("/api/admin/admins", headers=super_headers)
    admin_id = next(a["id"] for a in r.get_json()["items"] if a["email"] == "donor1@example.com")

    r2 = client.post(f"/api/admin/admins/{admin_id}/suspend", headers=super_headers)
    assert r2.status_code == 200
    assert r2.get_json()["status"] == "suspended"

    r3 = client.get("/api/admin/causes", headers=donor_headers)
    assert r3.status_code == 403