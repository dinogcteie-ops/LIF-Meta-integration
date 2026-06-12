"""Throwaway rehearsal: old-schema stand-in → alembic upgrade through 0003."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VENV = r"C:\Users\DineshMani\Downloads\LIF-CRM\.venv\Scripts"
os.environ["DATABASE_URL"] = "sqlite:///./rehearsal4.db"

from app.db.engine import init_db, engine  # noqa: E402
from sqlalchemy import text  # noqa: E402

init_db()
with engine.begin() as c:
    # Strip everything 0002 + 0003 add, simulating today's prod schema.
    for col in ("budget_range", "city", "triage", "triage_source",
                "triage_reason", "triaged_at", "first_response_at"):
        c.execute(text(f"ALTER TABLE leads DROP COLUMN {col}"))
    for col in ("birthday", "anniversary"):
        c.execute(text(f"ALTER TABLE clients DROP COLUMN {col}"))
    c.execute(text("DROP TABLE communication_log"))
print("old-schema stand-in ready")

for args in (["stamp", "0001_baseline"], ["upgrade", "head"], ["current"]):
    r = subprocess.run([os.path.join(VENV, "alembic.exe"), *args],
                       capture_output=True, text=True, env=os.environ)
    print(f"alembic {' '.join(args)}: rc={r.returncode}")
    if r.returncode != 0:
        print(r.stderr[-800:])
        sys.exit(1)
    if args == ["current"]:
        print(r.stdout.strip())

with engine.connect() as c:
    lead_cols = [row[1] for row in c.execute(text("PRAGMA table_info(leads)"))]
    client_cols = [row[1] for row in c.execute(text("PRAGMA table_info(clients)"))]
    tables = [row[0] for row in c.execute(
        text("SELECT name FROM sqlite_master WHERE type='table'"))]
assert "first_response_at" in lead_cols and "triage" in lead_cols, lead_cols
assert "birthday" in client_cols and "anniversary" in client_cols, client_cols
assert "communication_log" in tables, tables
print("upgrade verified: 0002 + 0003 columns and table present")
