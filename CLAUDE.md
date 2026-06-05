# CLAUDE.md — operational guide for agents

Life in Frame — a studio CRM for a photography company. FastAPI + Jinja/HTMX,
server-rendered, Zoho-style UI. This file is the standing context so deployment
and architecture instructions don't have to be repeated each session. Human-facing
setup docs live in [README.md](README.md).

## Deployment — READ THIS FIRST

**To deploy: `git push origin main`. That's it.** Render watches `main` and
auto-deploys (`render.yaml` → `autoDeploy: true`), building the Docker image and
swapping in the new version once `/healthz` passes (~2–5 min on free tier).

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

- **Money is in lakhs (₹L).** Lead/event amounts store lakh values; the `| money`
  Jinja filter renders them with the ₹ symbol and Indian digit grouping. Form labels
  say "(₹ L)". `| pct` formats percentages.
- Match surrounding style: section headers use `# ─── Title ───` comment banners;
  templates use Bootstrap + the existing `lif-card` / `funnel-card` / `cat-dot`
  component classes.
- **Git:** work on a branch, never commit straight to `main` unless asked. Deploys go
  out by merging to `main` + pushing — so only push `main` when you intend to ship.
  Commit messages end with the `Co-Authored-By: Claude …` trailer. Pass multi-line
  messages via a file (`git commit -F`) — `@'...'@` here-strings are PowerShell-only and
  break under the Bash tool.

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
