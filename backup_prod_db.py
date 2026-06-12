"""Snapshot the production (Supabase) database to a timestamped JSON file.

pg_dump is not available on this machine, so this reflects every table via
SQLAlchemy and serialises all rows to JSON — a restorable copy made with only
the app's existing dependencies. Run BEFORE any data migration.

    python backup_prod_db.py

Writes to backups/prod_backup_<UTC timestamp>.json (created if absent). Run
from the repo root with .env present (it points at production Supabase).
"""
import datetime as dt
import decimal
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("LIF_ENV", "production")

from sqlalchemy import MetaData, select

from app.db.engine import engine


def _encode(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    return v


def main() -> None:
    md = MetaData()
    md.reflect(bind=engine)
    snapshot = {"_meta": {"taken_at": dt.datetime.utcnow().isoformat() + "Z",
                          "dialect": engine.dialect.name}}
    counts = {}
    with engine.connect() as conn:
        for name, table in md.tables.items():
            rows = []
            for row in conn.execute(select(table)).mappings():
                rows.append({k: _encode(v) for k, v in row.items()})
            snapshot[name] = rows
            counts[name] = len(rows)

    out_dir = Path(__file__).parent / "backups"
    out_dir.mkdir(exist_ok=True)
    stamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"prod_backup_{stamp}.json"
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    total = sum(counts.values())
    print(f"\nBacked up {len(counts)} tables, {total} rows total:")
    for name in sorted(counts):
        print(f"  {name:<28} {counts[name]:>6}")
    size_kb = out_path.stat().st_size / 1024
    print(f"\nWrote {out_path}  ({size_kb:,.1f} KB)\n")


if __name__ == "__main__":
    main()
