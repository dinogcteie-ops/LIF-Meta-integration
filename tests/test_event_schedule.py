"""Event edit: structured 3-stage payment schedule + quoted-amount precision.

Covers the changes that removed the manual delivery-stage control and the
freeform schedule textarea in favour of a Booking advance / Event day /
On delivery editor, and fixed the quoted-amount field truncating decimals.
"""
import json

from fastapi.testclient import TestClient

from app.routes.events import _compose_schedule, _split_schedule
from app.services.db import Database


# ─── compose: 3 standard rows + freeform extras → sorted JSON ─────────────────

def test_compose_schedule_orders_and_labels():
    raw = _compose_schedule(
        [
            ("2026-03-01", "20000", "On delivery"),
            ("2025-12-01", "50000", "Booking advance"),
            ("", "", "Event day"),            # blank row is skipped
        ],
        "2026-01-10 : 30000 : Pre-shoot",     # freeform extra
    )
    items = json.loads(raw)
    # Sorted by date; blank Event-day row dropped; extra merged in
    assert [i["label"] for i in items] == ["Booking advance", "Pre-shoot", "On delivery"]
    assert [i["date"] for i in items] == ["2025-12-01", "2026-01-10", "2026-03-01"]


def test_compose_schedule_empty_returns_none():
    assert _compose_schedule([("", "", "Booking advance")], "") is None


# ─── split: stored JSON → 3 standard rows + leftover extras ───────────────────

def test_split_schedule_routes_standard_and_extra():
    raw = json.dumps([
        {"date": "2025-12-01", "amount": 50000, "label": "Booking advance"},
        {"date": "2026-02-01", "amount": 25000, "label": "Event day"},
        {"date": "2026-01-10", "amount": 30000, "label": "Pre-shoot"},
    ])
    std, extra = _split_schedule(raw)
    assert std["advance"] == {"date": "2025-12-01", "amount": "50000"}
    assert std["eventday"] == {"date": "2026-02-01", "amount": "25000"}
    assert std["delivery"] == {"date": "", "amount": ""}      # none supplied
    assert "Pre-shoot" in extra and "2026-01-10" in extra


def test_split_schedule_empty():
    std, extra = _split_schedule(None)
    assert extra == ""
    assert all(v == {"date": "", "amount": ""} for v in std.values())


# ─── route: structured fields persist; decimal quote is preserved ─────────────

def test_update_event_structured_schedule_and_decimal_quote(client: TestClient, db: Database):
    ev = db.create_event(name="Sai Prasad", quoted_amount=2.5, status="active")
    r = client.post(
        f"/events/{ev.id}",
        data={
            "name": "Sai Prasad",
            "status": "active",
            "quoted_amount": "2.5",           # fractional must survive the round-trip
            "adv_date": "2025-12-01", "adv_amount": "50000",
            "eventday_date": "2026-02-01", "eventday_amount": "25000",
            "delivery_date": "", "delivery_amount": "",
            "payment_schedule_extra": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    fresh = db.get_event(ev.id)
    assert fresh.quoted_amount == 2.5         # not truncated to 2
    sched = json.loads(fresh.payment_due_dates)
    assert {i["label"] for i in sched} == {"Booking advance", "Event day"}
