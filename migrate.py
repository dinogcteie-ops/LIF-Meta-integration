"""One-time migration: SQLite lif.db → Google Sheets DB tabs."""
import sqlite3
import sys

# Bootstrap app settings so sheetdb can load credentials
sys.path.insert(0, ".")

from app.services.sheetdb import SheetDB, _open_spreadsheet, _ensure_tab, _HEADERS
from app.services.sheetdb import _T_CATS, _T_EVENTS, _T_PAYMENTS, _T_EXPENSES

DB_PATH = "lif.db"


def migrate():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    db = SheetDB()
    sh = _open_spreadsheet()

    # ── Clear all DB tabs and rewrite from SQLite ─────────────────────────────
    for tab_name in [_T_CATS, _T_EVENTS, _T_PAYMENTS, _T_EXPENSES]:
        ws = sh.worksheet(tab_name)
        ws.clear()
        ws.update([_HEADERS[tab_name]], "A1")
    print("Cleared all DB tabs.")

    # ── Categories ─────────────────────────────────────────────────────────────
    rows = cur.execute(
        "SELECT id, name, scope, is_active, created_at FROM expense_category ORDER BY id"
    ).fetchall()
    if rows:
        data = [[r["id"], r["name"], r["scope"], "TRUE" if r["is_active"] else "FALSE",
                 r["created_at"] or ""] for r in rows]
        sh.worksheet(_T_CATS).append_rows(data)
    print(f"Migrated {len(rows)} categories.")

    # ── Events ─────────────────────────────────────────────────────────────────
    rows = cur.execute(
        "SELECT id, name, client_name, event_date, quoted_amount, status, notes, created_at FROM event ORDER BY id"
    ).fetchall()
    if rows:
        data = [[r["id"], r["name"], r["client_name"] or "", r["event_date"] or "",
                 r["quoted_amount"], r["status"], r["notes"] or "", r["created_at"] or ""]
                for r in rows]
        sh.worksheet(_T_EVENTS).append_rows(data)
    print(f"Migrated {len(rows)} events.")

    # ── Payments ───────────────────────────────────────────────────────────────
    rows = cur.execute(
        "SELECT id, event_id, amount, payment_date, notes, created_at FROM event_payment ORDER BY id"
    ).fetchall()
    if rows:
        data = [[r["id"], r["event_id"], r["amount"], r["payment_date"] or "",
                 r["notes"] or "", r["created_at"] or ""] for r in rows]
        sh.worksheet(_T_PAYMENTS).append_rows(data)
    print(f"Migrated {len(rows)} payments.")

    # ── Expenses ───────────────────────────────────────────────────────────────
    rows = cur.execute(
        "SELECT id, date, event_id, category_id, scope, payment_status, amount, "
        "paid_amount, paid_to, notes, created_at FROM expense ORDER BY id"
    ).fetchall()
    if rows:
        data = [[r["id"], r["date"] or "", r["event_id"] or "", r["category_id"],
                 r["scope"], r["payment_status"], r["amount"], r["paid_amount"] or 0,
                 r["paid_to"] or "", r["notes"] or "", r["created_at"] or ""]
                for r in rows]
        sh.worksheet(_T_EXPENSES).append_rows(data)
    print(f"Migrated {len(rows)} expenses.")

    conn.close()
    print("\nMigration complete! Refresh the app to see your data.")


if __name__ == "__main__":
    migrate()
