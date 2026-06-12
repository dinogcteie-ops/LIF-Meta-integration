"""Throwaway rehearsal: simulate prod's old schema, then run alembic upgrade."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VENV = r"C:\Users\DineshMani\Downloads\LIF-CRM\.venv\Scripts"
os.environ["DATABASE_URL"] = "sqlite:///./rehearsal3.db"

from app.db.engine import init_db, engine  # noqa: E402
from sqlalchemy import text  # noqa: E402

init_db()
with engine.begin() as c:
    for col in ("budget_range", "city", "triage", "triage_source",
                "triage_reason", "triaged_at"):
        c.execute(text(f"ALTER TABLE leads DROP COLUMN {col}"))
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
    cols = [row[1] for row in c.execute(text("PRAGMA table_info(leads)"))]
assert "triage" in cols and "budget_range" in cols, cols
print("upgrade verified: new columns present")
