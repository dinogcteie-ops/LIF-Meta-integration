# CLAUDE.md — operational guide for agents

Life in Frame — a studio CRM for a photography company. FastAPI + Jinja/HTMX,
server-rendered, Zoho-style UI. This file is the standing context so deployment
and architecture instructions don't have to be repeated each session. Human-facing
setup docs live in [README.md](README.md).

## Deployment — READ THIS FIRST

**To deploy: `git push origin main`. That's it.** Render watches `main` and
auto-deploys (`render.yaml` → `autoDeploy: true`), building the Docker image and
swapping in the new version once `/healthz` passes (~2–5 min on free tier).

### Release workflow — branch per change, batch-deploy (READ BEFORE PUSHING)

The owner verifies every change on **localhost** first and ships on a **monthly
cadence**, not per-change. So:

- **Every new piece of work gets its own branch** (`feat/...`). Implement, run tests,
  and let the owner verify locally. **Do NOT merge to `main` or push** unless the owner
  explicitly says "deploy" / "ship now".
- Because `git push origin main` *is* a production deploy (Render auto-deploys), pushing
  `main` is the single irreversible action — treat it as the release gate.
- At the agreed release time (end of month), branches fast-forward/merge into `main`
  and push once as a batch.
- Keep the feature list in [FEATURES.md](FEATURES.md) current: when you add a feature,
  add a row with status `On branch (pending deploy)`; flip it to `Live` after the
  batch ships.

- **Public URL:** https://lifcrm.netlify.app — Netlify is *only* a domain/HTTPS/CDN
  proxy. It runs no Python; it forwards every request to Render (`netlify.toml`
  redirect → `https://lif-crm.onrender.com`). Changing app behavior never touches
  Netlify.
- **The app runs on Render:** `https://lif-crm.onrender.com` (service `lif-crm`,
  Docker runtime, uses the repo `Dockerfile`).
- **There is NO Fly.io.** Any `fly.toml` / `flyctl` / `fly deploy` is wrong — it was
  a dead config and has been deleted. Do not recreate it or run Fly commands.
- The Fly account on this machine owns nothing; ignore it entirely.

## Database

- **Supabase Postgres** in every environment. Render's `DATABASE_URL` secret and the
  local `.env` point at the **same** Supabase instance, so local scripts/dry-runs hit
  **production data** — treat writes accordingly.
- Schema auto-creates on boot. There is no separate migration step for normal deploys
  (no Alembic run needed for ordinary feature work).
- Data access is abstracted behind `app/services/db.py` (`Database`, returned by
  `get_db()`), yielding the dataclasses in `app/domain.py`. Enums in `app/enums.py`.
  Routes/templates are storage-agnostic — change data shape via the `Database` layer.

## Running & verifying locally

- **The virtualenv lives at the repo root `.venv`**, NOT inside `.claude/worktrees/*`.
  From a worktree, invoke it by path, e.g.
  `C:\Users\DineshMani\Downloads\LIF-CRM\.venv\Scripts\python.exe`, or
  `...\.venv\Scripts\uvicorn.exe app.main:app --reload --port 8000`.
- Worktrees have no `.env`. To run there, copy the root `.env` in temporarily and
  **delete it again afterward** (it holds production Supabase + Meta secrets). Never
  commit `.env` (it's gitignored).
- Login: the app is password-gated. `POST /login` with `password=<APP_PASSWORD>`
  (from `.env`) to get a session cookie, then GET the page you want.
- Tests: `pytest` (`tests/test_reports.py`, `tests/test_routes.py`).
- Preview tool note: `.claude/launch.json` points at `.venv\Scripts\uvicorn.exe`
  relative to the worktree, which won't exist in a worktree. The dashboard is heavy —
  `preview_screenshot` may time out; verify via DOM/`preview_eval` instead. Set a real
  viewport width (e.g. 1280) before measuring layout — the default can be ~2px and
  collapses responsive charts to 0×0.

## Codebase map

- `app/main.py` — app wiring. `app/routes/*.py` — one router per area (leads,
  dashboard, events, clients, expenses, payables, receivables, reports, meta, portal…).
- `app/services/` — `db.py` (data layer), `reports.py` (analytics/aggregations used by
  dashboard & reports), `analytics.py` (KPIs/sparklines), `meta.py`/`sheets.py` etc.
- `app/templates/` — Jinja templates; `base.html` loads Bootstrap + Chart.js globally.
  Charts are inline `<script>` per template (no central JS); pass data with `|tojson`.
- `app/static/css/style.css` — all styling. Reuse existing classes/patterns.
- `app/enums.py` — dropdown/category enums (e.g. `LeadStatus`, `LostReason`).

## Conventions

- **Estimated expenses are planning-only.** `PaymentStatus.estimated` marks a cost
  as an estimate. `db.list_expenses()` **excludes estimated rows by default** — so
  every actual-money calc (payables, profit, KPIs, bank) auto-ignores them. Opt in
  with `include_estimates=True` or `status="estimated"`. They surface only on the
  event detail's "Estimated costs / Projected profit" card.
- **Money is in lakhs (₹L).** Lead/event amounts store lakh values; the `| money`
  Jinja filter renders them with the ₹ symbol and Indian digit grouping. Form labels
  say "(₹ L)". `| pct` formats percentages.
- Match surrounding style: section headers use `# ─── Title ───` comment banners;
  templates use Bootstrap + the existing `lif-card` / `funnel-card` / `cat-dot`
  component classes.
- **Git:** work on a branch, never commit straight to `main` unless asked. Deploys go
  out by merging to `main` + pushing — so only push `main` when you intend to ship, and
  only on the owner's go-ahead (see **Release workflow** above; default is batch-deploy
  at end of month after local verification). Commit messages end with the
  `Co-Authored-By: Claude …` trailer. Pass multi-line messages via a file
  (`git commit -F`) — `@'...'@` here-strings are PowerShell-only and break under the Bash
  tool.

## One-time / maintenance scripts

- `migrate_lost_reasons.py` — normalizes legacy free-text lead "lost reasons" onto the
  `LostReason` categories. Dry-run by default; `--apply` writes. Re-runnable
  (idempotent). Run from repo root with the root venv + `.env` present.
- `scripts/migrate_sheets_to_db.py` — the original Google-Sheet → Supabase importer
  (see README). `import_leads.py` / `import_data.py` — historical CSV imports.
- Pattern for any such script: `from app.database import get_db` (a usable singleton),
  iterate via the `Database` API, default to dry-run, gate writes behind `--apply`.

## Feature areas (high level)

Events (profitability, payments, delivery status), Clients, **Leads** (pipeline with
status funnel, source conversion, standardized **lost-reason** tracking + a "Why we
lose leads" dashboard breakdown), Payees/Expenses with scopes (event/company/personal)
and budgets, Receivables/Payables aging, Reports, a client Portal, and **Meta Ads**
integration (lead capture via `POST /webhooks/meta/leads` + campaign metrics at `/meta`).

**The authoritative, up-to-date feature list with ship status lives in
[FEATURES.md](FEATURES.md)** — keep it current as features are added/shipped.

## Background jobs (cron) & email

- Token-gated job endpoints live in `app/routes/jobs.py`, gated like `/meta/refresh`
  (logged-in user **or** `?token=` == `META_VERIFY_TOKEN`); `/jobs/` is allow-listed in
  `app/auth.py`. Driven by Netlify scheduled functions (`netlify/functions/*.mjs`,
  schedules in `netlify.toml`).
  - `POST /jobs/followup-reminders` — daily Gmail digest of New/Quoted leads due for
    follow-up today (`app/services/reminders.py` + `email.py`).
  - `POST /jobs/import-leads` (`?dry_run=1`) — pulls new rows from the inbound Google
    Sheet into leads (`app/services/lead_intake.py`); dedup via a row-count cursor in
    settings.
- **Email transport — Render blocks outbound SMTP.** Connections to
  `smtp.gmail.com:465` from Render fail with `[Errno 101] Network is unreachable`,
  so SMTP **cannot** be used in production (this silently broke every email until
  found). `app/services/email.py` therefore sends via the **Gmail API over HTTPS**
  (port 443, allowed) when `GMAIL_REFRESH_TOKEN` is set, and falls back to SMTP only
  for local dev. Mint the refresh token once with
  `scripts/get_gmail_refresh_token.py` (needs Gmail API enabled + the `gmail.send`
  scope on the existing OAuth client). Never reintroduce a plain-SMTP send path for
  prod.
- Relevant env vars (set on Render): `GMAIL_REFRESH_TOKEN` (Gmail API send; reuses
  `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`), `SMTP_FROM` (From address),
  `PUBLIC_BASE_URL`, `LEADS_INTAKE_SHEET_ID`, `LEADS_INTAKE_TAB`. `SMTP_USER`/
  `SMTP_PASSWORD` are only used by the local-dev SMTP fallback. Recipients + on/off
  toggle are editable in the Settings page (DB settings).
