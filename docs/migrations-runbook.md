# Production migration runbook (Supabase)

The local `.env` points at **production** Supabase — every Alembic/script run is
live-fire. `migrations/env.py` therefore refuses any Postgres URL unless
`CONFIRM_PROD=1` is set. Follow this checklist, in order, every time.

## Why Alembic at all

`init_db()` (`create_all`) builds fresh dev/test SQLite databases completely,
but **never alters existing tables** — adding a column to `app/db/tables.py`
does nothing to production. Every schema change therefore ships as BOTH:

1. the column/table in `app/db/tables.py` (fresh DBs get it via `create_all`), and
2. an Alembic revision in `migrations/versions/` (production gets it via `upgrade`).

## One-time setup (already done / first prod run)

Production has never run Alembic; bring it under control by **stamping** the
baseline (metadata-only, writes one row to `alembic_version`):

```powershell
$env:CONFIRM_PROD = "1"; alembic stamp 0001_baseline
```

## Per-release checklist

1. **Rehearse on SQLite.** Mind the trap: on the *new* code, `create_all`
   builds the FULL new schema, so `stamp 0001_baseline` + `upgrade head` will
   fail with duplicate columns. To simulate prod you need an **old-schema
   stand-in**: either run `init_db()` from a `main` checkout, or `init_db()` on
   the new code then `ALTER TABLE ... DROP COLUMN` the new columns before
   stamping (see `dev/rehearse_0002.py` for a worked example). Then:
   ```powershell
   $env:DATABASE_URL = "sqlite:///./lif_rehearsal.db"
   alembic stamp 0001_baseline
   alembic upgrade head                                        # must succeed
   Remove-Item env:DATABASE_URL
   ```
   (For a higher-fidelity rehearsal, restore a `pg_dump` into a throwaway
   Postgres — Docker or a second free Supabase project — and run the same.)
   Corollary for fresh dev/test DBs on current code: they are already at the
   newest schema, so bring them under Alembic with `alembic stamp head` — never
   `stamp 0001_baseline` + upgrade.
2. **Preview the exact SQL** that would hit prod (offline mode, no connection):
   ```powershell
   $env:CONFIRM_PROD = "1"
   alembic upgrade 0001_baseline:head --sql
   ```
   Read it. All statements must be additive (CREATE TABLE / ADD COLUMN /
   CREATE INDEX / UPDATE backfills). Anything destructive: stop.
3. **Back up prod** immediately before (use the DIRECT connection string from
   Supabase dashboard, not the pooler):
   ```powershell
   pg_dump "<direct-connection-url>" -Fc -f "backup-$(Get-Date -Format yyyyMMdd-HHmm).dump"
   ```
   Supabase free tier has weak automatic backups — this dump is the real safety net.
4. **Run it** (quiet window; additive migrations don't break the running app):
   ```powershell
   $env:CONFIRM_PROD = "1"; alembic upgrade head
   ```
5. **Post-checks**: row counts per touched table unchanged; for backfilled
   columns, `SELECT count(*) WHERE <col> IS NULL` is 0 (or as expected);
   `/healthz` still OK.
6. Deploy the code that *uses* the new schema only after 1–5 pass (migration
   first, code second — old code ignores new columns).

## Rules

- Revisions are **additive only** while the app deploys from `main` on push.
- Never edit a revision that has run on prod; add a new one.
- One revision per feature branch, named `NNNN_<slug>.py`, linear history
  (set `down_revision` to the current head at merge time — resolve collisions
  at the monthly batch merge).
