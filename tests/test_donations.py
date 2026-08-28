# ============================================================
# FILE BELONGS AT:  tests/test_donations.py
# ============================================================
"""
Manual donation bookkeeping — admins log what SwipeSimple already
processed; nothing here talks to a payment provider.
"""


def test_create_donation_requires_admin(client, cause_id):
    r = client.post("/api/admin/donations", json={"cause_id": cause_id, "amount": 50})
    assert r.status_code == 401


def test_admin_can_log_a_donation(client, cause_id, super_headers):
    r = client.post(
        "/api/admin/donations",
        json={"cause_id": cause_id, "amount": 50, "donor_name": "Jane Doe", "donor_email": "jane@example.com", "method": "SwipeSimple"},
        headers=super_headers,
    )
    assert r.status_code == 201
    body = r.get_json()
    assert body["status"] == "recorded"
    assert body["amount"] == "50.00" or body["amount"] == "50"
    assert body["method"] == "SwipeSimple"


def test_negative_amount_rejected(client, cause_id, super_headers):
    r = client.post("/api/admin/donations", json={"cause_id": cause_id, "amount": -10}, headers=super_headers)
    assert r.status_code == 400


def test_zero_amount_rejected(client, cause_id, super_headers):
    r = client.post("/api/admin/donations", json={"cause_id": cause_id, "amount": 0}, headers=super_headers)
    assert r.status_code == 400


def test_unknown_cause_rejected(client, super_headers):
    r = client.post("/api/admin/donations", json={"cause_id": "nope", "amount": 10}, headers=super_headers)
    assert r.status_code == 404


def test_anonymous_donation_hides_donor_info_in_response(client, cause_id, super_headers):
    r = client.post(
        "/api/admin/donations",
        json={"cause_id": cause_id, "amount": 20, "donor_name": "Secret Donor", "is_anonymous": True},
        headers=super_headers,
    )
    body = r.get_json()
    assert body["is_anonymous"] is True
    assert body["donor_name"] is None


def test_list_donations_admin_only(client, cause_id, super_headers):
    client.post("/api/admin/donations", json={"cause_id": cause_id, "amount": 15}, headers=super_headers)
    r = client.get("/api/admin/donations", headers=super_headers)
    assert r.status_code == 200
    assert len(r.get_json()["items"]) == 1

    r_unauth = client.get("/api/admin/donations")
    assert r_unauth.status_code == 401


def test_update_donation_corrects_a_typo(client, cause_id, super_headers):
    create = client.post("/api/admin/donations", json={"cause_id": cause_id, "amount": 15, "donor_name": "Jhon"}, headers=super_headers)
    donation_id = create.get_json()["id"]

    r = client.patch(f"/api/admin/donations/{donation_id}", json={"donor_name": "John"}, headers=super_headers)
    assert r.status_code == 200
    assert r.get_json()["donor_name"] == "John"


def test_update_cannot_change_status_directly(client, cause_id, super_headers):
    create = client.post("/api/admin/donations", json={"cause_id": cause_id, "amount": 15}, headers=super_headers)
    donation_id = create.get_json()["id"]

    r = client.patch(f"/api/admin/donations/{donation_id}", json={"status": "refunded"}, headers=super_headers)
    assert r.status_code == 400


def test_refund_marks_donation_refunded(client, cause_id, super_headers):
    create = client.post("/api/admin/donations", json={"cause_id": cause_id, "amount": 15}, headers=super_headers)
    donation_id = create.get_json()["id"]

    r = client.post(f"/api/admin/donations/{donation_id}/refund", json={"reason": "duplicate entry"}, headers=super_headers)
    assert r.status_code == 200
    assert r.get_json()["status"] == "refunded"
    assert r.get_json()["refund_reason"] == "duplicate entry"


def test_cannot_refund_twice(client, cause_id, super_headers):
    create = client.post("/api/admin/donations", json={"cause_id": cause_id, "amount": 15}, headers=super_headers)
    donation_id = create.get_json()["id"]
    client.post(f"/api/admin/donations/{donation_id}/refund", json={}, headers=super_headers)

    r = client.post(f"/api/admin/donations/{donation_id}/refund", json={}, headers=super_headers)
    assert r.status_code == 400


def test_export_csv(client, cause_id, super_headers):
    client.post("/api/admin/donations", json={"cause_id": cause_id, "amount": 15}, headers=super_headers)
    r = client.get("/api/admin/donations/export", headers=super_headers)
    assert r.status_code == 200
    assert r.mimetype == "text/csv"
    assert b"donor_name" in r.data


def test_donors_consent_requires_auth(client):
    r = client.post("/api/donors/consent", json={"agreed": True})
    assert r.status_code == 401


def test_donors_consent_records_registration(client, donor_headers):
    r = client.post("/api/donors/consent", json={"agreed": True}, headers=donor_headers)
    assert r.status_code == 201
    assert r.get_json()["status"] == "recorded"


def test_donors_consent_requires_agreed_true(client, donor_headers):
    r = client.post("/api/donors/consent", json={"agreed": False}, headers=donor_headers)
    assert r.status_code == 400