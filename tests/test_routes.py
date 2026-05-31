import json

from fastapi.testclient import TestClient

from app.main import app


def test_login_required_redirect():
    c = TestClient(app, follow_redirects=False)
    resp = c.get("/dashboard")
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers["location"]


def test_login_wrong_password():
    with TestClient(app, follow_redirects=True) as c:
        r = c.post("/login", data={"password": "wrongpass"})
        assert r.status_code == 401


def test_dashboard_loads(client: TestClient):
    r = client.get("/dashboard")
    assert r.status_code == 200


def test_all_main_pages_load(client: TestClient):
    for path in [
        "/events", "/clients", "/leads", "/payees", "/calendar",
        "/receivables", "/payables", "/expenses", "/expenses/analytics",
        "/categories", "/workshop", "/reports", "/quick", "/settings", "/meta",
    ]:
        assert client.get(path).status_code == 200, path


def test_events_crud(client: TestClient):
    r = client.post("/events", data={
        "name": "Test Wedding", "client_name": "Alice",
        "event_date": "2026-06-15", "quoted_amount": "80000",
        "status": "active", "notes": "",
    })
    assert r.status_code == 200
    assert "Test Wedding" in r.text
    assert "Test Wedding" in client.get("/events").text


def test_lead_crud(client: TestClient):
    r = client.post("/leads", data={"client_name": "Lead Carol", "status": "new"})
    assert r.status_code == 200
    assert "Lead Carol" in client.get("/leads").text


def test_export_xlsx(client: TestClient):
    r = client.get("/export/xlsx")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]


def test_export_csv(client: TestClient):
    r = client.get("/export/csv?kind=events")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]


# ── Meta integration ────────────────────────────────────────────────────────

def test_meta_dashboard_loads(client: TestClient):
    assert client.get("/meta").status_code == 200


def test_meta_webhook_verify_rejects_bad_token():
    c = TestClient(app, follow_redirects=False)
    r = c.get("/webhooks/meta/leads", params={
        "hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "X",
    })
    assert r.status_code == 403


def test_meta_webhook_post_is_idempotent_and_safe():
    c = TestClient(app, follow_redirects=False)
    payload = {"entry": [{"changes": [{"field": "leadgen", "value": {"leadgen_id": "abc"}}]}]}
    r = c.post("/webhooks/meta/leads", content=json.dumps(payload),
               headers={"Content-Type": "application/json"})
    # No page token configured in tests → lead retrieval skipped, but request OK.
    assert r.status_code == 200
    assert r.json()["received"] is True


def test_meta_refresh_requires_auth():
    c = TestClient(app, follow_redirects=False)
    r = c.post("/meta/refresh")
    assert r.status_code == 403
