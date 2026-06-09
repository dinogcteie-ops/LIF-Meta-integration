"""Estimated (planning-only) expenses must be invisible to all actual-money
calculations, and only surface when explicitly requested."""
from datetime import date, timedelta

from app.services.reports import cash_flow_alerts, event_profit, payables_aging


def test_payable_alert_names_category_and_event(db):
    cat = db.list_categories(active_only=True)[0]
    ev = db.create_event(name="ZZ Alert Wedding", quoted_amount=50_000, status="active")
    db.create_expense(date_=date.today() - timedelta(days=40), category_id=cat.id,
                      scope="event", payment_status="pending", amount=12_345,
                      paid_amount=0, event_id=ev.id, paid_to="None")  # blank-ish payee
    msgs = [a.message for a in cash_flow_alerts(db, date.today())]
    # Names the category + event, not the confusing "None" payee.
    assert any(f"{cat.name} — ZZ Alert Wedding" in m and "unpaid 40 days" in m for m in msgs), msgs
    assert not any(m.startswith("None ") for m in msgs)


def test_estimate_excluded_from_actuals(db):
    cat = db.list_categories(active_only=True)[0]
    ev = db.create_event(name="EstimateTest", quoted_amount=100_000, status="active")
    # A real payable (pending) and a planning estimate on the same event.
    db.create_expense(date_=date(2026, 1, 1), category_id=cat.id, scope="event",
                      payment_status="pending", amount=10_000, paid_amount=0, event_id=ev.id)
    db.create_expense(date_=date(2026, 1, 1), category_id=cat.id, scope="event",
                      payment_status="estimated", amount=50_000, paid_amount=0, event_id=ev.id)

    # Default list excludes the estimate; opt-ins include it.
    assert {e.amount for e in db.list_expenses(event_id=ev.id)} == {10_000}
    assert sum(e.amount for e in db.list_expenses(event_id=ev.id, include_estimates=True)) == 60_000
    assert [e.amount for e in db.list_expenses(event_id=ev.id, status="estimated")] == [50_000]

    # Payables see only the actual pending cost, not the estimate.
    rows, _ = payables_aging(db)
    assert {r.expense.amount for r in rows if r.expense.event_id == ev.id} == {10_000}

    # Event profit counts the actual cost only.
    ep = event_profit(db, ev.id)
    assert ep.expense == 10_000


def test_event_detail_renders_estimate_section(db, client):
    cat = db.list_categories(active_only=True)[0]
    ev = db.create_event(name="EstimateUI", quoted_amount=100_000, status="active")
    db.create_expense(date_=date(2026, 3, 1), category_id=cat.id, scope="event",
                      payment_status="estimated", amount=25_000, paid_amount=0, event_id=ev.id)
    r = client.get(f"/events/{ev.id}")
    assert r.status_code == 200
    assert "Estimated costs" in r.text
    assert "Projected profit" in r.text


def test_get_expense_still_returns_estimate(db):
    cat = db.list_categories(active_only=True)[0]
    e = db.create_expense(date_=date(2026, 2, 1), category_id=cat.id, scope="company",
                          payment_status="estimated", amount=7_000, paid_amount=0)
    # Editable by id even though it's hidden from the default ledger.
    got = db.get_expense(e.id)
    assert got is not None and got.payment_status.value == "estimated"
