"""One-time migration: Google Sheets (old SheetDB) → Postgres/SQLite (new Database).

Copies every entity in FK-safe order, **preserving primary-key ids** so existing
references (event_id, client_id, payee_id, category_id) stay valid. Idempotent:
rows whose id already exists in the target are skipped.

Prerequisites (env / .env):
  GOOGLE_SHEET_ID            the source spreadsheet
  GOOGLE_SA_JSON_PATH | GOOGLE_SA_JSON_BASE64   service-account credentials
  DATABASE_URL              the TARGET database (Supabase Postgres recommended)

Run:
  python -m scripts.migrate_sheets_to_db
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, text

from app.db.engine import SessionLocal, engine, init_db
from app.db.tables import (
    AuditRow, CategoryRow, ClientRow, EventRow, ExpenseRow, LeadRow,
    PayeeRow, PaymentRow, SettingRow,
)


def _existing_ids(session, Row) -> set[int]:
    return set(session.scalars(select(Row.id)).all())


def _enumval(v) -> str:
    """Return the .value of an enum, or the value unchanged if it's already a str."""
    return getattr(v, "value", v)


def migrate() -> None:
    from app.services.sheetdb import SheetDB  # imports gspread lazily

    print("Connecting to source Google Sheet…")
    old = SheetDB()

    print("Ensuring target schema exists…")
    init_db()

    session = SessionLocal()
    counts: dict[str, int] = {}
    try:
        # ── Categories ──
        have = _existing_ids(session, CategoryRow)
        for c in old.list_categories():
            if c.id in have:
                continue
            session.add(CategoryRow(id=c.id, name=c.name, scope=_enumval(c.scope),
                                    is_active=c.is_active, created_at=c.created_at,
                                    monthly_budget=c.monthly_budget))
        session.flush(); counts["categories"] = len(old.list_categories())

        # ── Clients ──
        have = _existing_ids(session, ClientRow)
        for c in old.list_clients():
            if c.id in have:
                continue
            session.add(ClientRow(id=c.id, name=c.name, phone=c.phone, email=c.email,
                                  address=c.address, notes=c.notes, created_at=c.created_at))
        session.flush(); counts["clients"] = len(old.list_clients())

        # ── Payees ──
        have = _existing_ids(session, PayeeRow)
        for p in old.list_payees():
            if p.id in have:
                continue
            session.add(PayeeRow(id=p.id, name=p.name, payee_type=p.payee_type,
                                 phone=p.phone, email=p.email, notes=p.notes,
                                 created_at=p.created_at))
        session.flush(); counts["payees"] = len(old.list_payees())

        # ── Events ──
        have = _existing_ids(session, EventRow)
        for e in old.list_events():
            if e.id in have:
                continue
            session.add(EventRow(id=e.id, name=e.name, client_name=e.client_name,
                                 event_date=e.event_date, quoted_amount=e.quoted_amount,
                                 status=_enumval(e.status), notes=e.notes,
                                 created_at=e.created_at, event_type=e.event_type,
                                 location=e.location, referral_source=e.referral_source,
                                 payment_due_dates=e.payment_due_dates,
                                 last_reminder_sent=e.last_reminder_sent,
                                 reminder_notes=e.reminder_notes, client_id=e.client_id,
                                 delivery_status=e.delivery_status))
        session.flush(); counts["events"] = len(old.list_events())

        # ── Payments ──
        have = _existing_ids(session, PaymentRow)
        for p in old.list_payments():
            if p.id in have:
                continue
            session.add(PaymentRow(id=p.id, event_id=p.event_id, amount=p.amount,
                                   payment_date=p.payment_date, notes=p.notes,
                                   created_at=p.created_at))
        session.flush(); counts["payments"] = len(old.list_payments())

        # ── Expenses ──
        have = _existing_ids(session, ExpenseRow)
        for x in old.list_expenses():
            if x.id in have:
                continue
            session.add(ExpenseRow(id=x.id, date=x.date, event_id=x.event_id,
                                   category_id=x.category_id, scope=_enumval(x.scope),
                                   payment_status=_enumval(x.payment_status),
                                   amount=x.amount, paid_amount=x.paid_amount,
                                   paid_to=x.paid_to, notes=x.notes, created_at=x.created_at,
                                   payee_id=x.payee_id, is_recurring=x.is_recurring,
                                   recurring_day=x.recurring_day, payment_type=x.payment_type))
        session.flush(); counts["expenses"] = len(old.list_expenses())

        # ── Leads ──
        have = _existing_ids(session, LeadRow)
        for l in old.list_leads():
            if l.id in have:
                continue
            session.add(LeadRow(id=l.id, client_name=l.client_name, contact=l.contact,
                                event_type=l.event_type, tentative_date=l.tentative_date,
                                source=l.source, status=l.status, quoted_amount=l.quoted_amount,
                                notes=l.notes, created_at=l.created_at, client_id=l.client_id,
                                num_events=l.num_events, revised_quote=l.revised_quote,
                                follow_ups=l.follow_ups, rejection_reason=l.rejection_reason,
                                meta_campaign=l.meta_campaign, referral_name=l.referral_name,
                                followup_status=l.followup_status, followup_date=l.followup_date))
        session.flush(); counts["leads"] = len(old.list_leads())

        # ── Settings (key/value; ids auto) ──
        have_keys = set(session.scalars(select(SettingRow.key)).all())
        for key, value in old.get_settings_dict().items():
            if key in have_keys:
                continue
            session.add(SettingRow(key=key, value=str(value), updated_at=""))
        counts["settings"] = len(old.get_settings_dict())

        # ── Audit log ──
        have = _existing_ids(session, AuditRow)
        audit = old.list_audit(limit=100000)
        for a in audit:
            if a.id in have:
                continue
            session.add(AuditRow(id=a.id, timestamp=a.timestamp, entity_type=a.entity_type,
                                 entity_id=a.entity_id, action=a.action, summary=a.summary))
        counts["audit"] = len(audit)

        session.commit()
        _reset_sequences(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    print("\nMigration complete. Source row counts:")
    for k, v in counts.items():
        print(f"  {k:12s}: {v}")
    print("\nVerify these against the target DB and the Google Sheet tabs.")


def _reset_sequences(session) -> None:
    """Bump Postgres id sequences past the max migrated id. No-op on SQLite."""
    if not engine.url.get_backend_name().startswith("postgres"):
        return
    tables = ["expense_categories", "events", "event_payments", "expenses",
              "clients", "payees", "settings", "audit_log", "leads", "meta_metrics"]
    for t in tables:
        session.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {t}), 1), true)"
        ))
    print("Reset Postgres id sequences.")


if __name__ == "__main__":
    migrate()
