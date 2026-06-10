# FEATURES — Life in Frame CRM

Living inventory of what the app does and the ship status of each feature. Update this
when you add or ship a feature (see the **Release workflow** in [CLAUDE.md](CLAUDE.md)).

**Status legend**
- ✅ **Live** — deployed to production (lifcrm.netlify.app)
- ⚙️ **Live, needs config** — deployed but inert until secrets/settings are provided
- 🔄 **On branch** — built & verified, pending the end-of-month batch deploy
- 📝 **Planned** — agreed/backlog, not started

_Last updated: 2026-06-10 (latency cache + dashboard breakdowns + directory backfill, pending end-of-month deploy)._

---

## Core platform

| Feature | Status | Notes |
|---|---|---|
| Password-gated app + sessions | ✅ | `app/auth.py`; `/login`, `APP_PASSWORD` |
| Finance dashboard (KPIs, sparklines, cash-flow alerts, drill-down modals) | ✅ | `app/routes/dashboard.py`, `app/templates/dashboard.html` |
| Reports & analytics (monthly/quarterly history, projections, YoY, seasonal) | ✅ | `app/services/reports.py`, `analytics.py` |
| **Per-request read cache** (GET-only middleware) — collapses repeated table reads to one round-trip each, cutting page-to-page latency | 🔄 | `read_cache` in `app/main.py`; ContextVar cache in `db.py` |
| **Typography system** — tokenized type scale (title/KPI/eyebrow) + shared `page_header` macro; money is the visual hero, restrained titles. Themeable per tenant. Rolled out across all 22 standard pages (detail pages keep bespoke headers, same CSS tokens) | 🔄 | tokens in `style.css :root`; `app/templates/_macros.html` |
| **Pending-from-clients split** by event status (completed / ongoing / booked) on the dashboard card | 🔄 | `BankSummary.pending_*` in `reports.py` |
| **Seasonal booking pattern** shows both total and average revenue per month | 🔄 | `seasonal_analysis`; `dashboard.html` |
| Settings (studio branding, finance defaults, recurring expenses) + audit log | ✅ | `app/routes/settings.py` |
| Calendar, quick-add, export, workshop | ✅ | `app/routes/{calendar,quick,export,workshop}.py` |

## Events

| Feature | Status | Notes |
|---|---|---|
| Event CRUD + profitability (income vs expense, margins) | ✅ | `EventProfit` in `reports.py` |
| Payments tracking & collection % | ✅ | |
| Delivery status workflow (shooting → editing → review → delivered) | ✅ | |

## Clients & Payees / Expenses

| Feature | Status | Notes |
|---|---|---|
| Clients directory + per-client revenue stats | ✅ | |
| Payees/vendors + spend stats | ✅ | |
| **Backfill Clients & Payees** from existing completed/booked events + expense `paid_to` | 🔄 | one-time `migrate_backfill_directory.py` (dry-run default, `--apply`, idempotent) |
| Expenses with scopes (event/company/personal) + monthly budgets | ✅ | `budget_vs_actual` |
| **Estimate** expense status — plan event costs separately from actuals; projected profit per event | 🔄 | `PaymentStatus.estimated`; `list_expenses` excludes by default |
| Receivables & Payables aging (buckets, reminders) | ✅ | `receivables_aging`, `payables_aging` |

## Leads pipeline

| Feature | Status | Notes |
|---|---|---|
| Lead CRUD, status funnel (new/quoted/won/lost/cold), source conversion | ✅ | `app/routes/leads.py` |
| Standardized **lost reasons** (enum dropdown, legacy values preserved) | ✅ | `LostReason` in `app/enums.py` |
| Lost-reason badge on lead detail + **column on the leads list** | ✅ | |
| Legacy free-text lost-reason migration script | ✅ | `migrate_lost_reasons.py` (ran) |
| "Why we lose leads" dashboard widget (doughnut + breakdown) | ✅ | `lost_reason_breakdown` |
| Lost → follow-up status auto **done** | 🔄 | branch `feat/lead-workflow-and-loss-analytics` |
| **Force a lost reason** before marking Lost | 🔄 | same branch |
| Quoted/Cold → soft prompt for follow-up date | 🔄 | same branch |
| "Why we lose leads" **filters**: source + explicit months (rolling 12) / quarter / year / custom range | 🔄 | same branch; counts by enquiry date |
| **Lead pipeline** widget shows **Lost** + **Won % / Lost %** + the same source/date filters | 🔄 | same branch; `LeadFunnel.won_rate`/`lost_rate`, independent `pipe_*` filter |

## Dashboards — sharing

| Feature | Status | Notes |
|---|---|---|
| **Share as image** (lost-leads widget, lead-pipeline widget, whole dashboard) | 🔄 | `app/static/js/share.js` + html2canvas; Web Share API → WhatsApp, download fallback |
| **Download as image** button beside each Share button (direct PNG) | 🔄 | `downloadAsImage()` in `share.js` |

## Lead intake & notifications

| Feature | Status | Notes |
|---|---|---|
| **Meta Ads** lead capture (`POST /webhooks/meta/leads`) + metrics at `/meta` | ✅ | `app/routes/meta.py` |
| **Follow-up reminder emails** (daily Gmail digest, Settings-configurable recipients) | ⚙️ | `app/routes/jobs.py`, `reminders.py`, `email.py`; needs `SMTP_*` + recipients |
| **Google Sheet lead intake** (daily pull → leads, dry-run + cursor dedup) | ⚙️ | `app/services/lead_intake.py`; needs sheet shared with SA + `LEADS_INTAKE_*` |

## Integrations & ops

| Feature | Status | Notes |
|---|---|---|
| Supabase Postgres data layer (storage-agnostic) | ✅ | `app/services/db.py` |
| Netlify (domain/CDN/proxy) → Render (FastAPI) hosting | ✅ | `netlify.toml`, `render.yaml` |
| Scheduled jobs (Netlify cron → token-gated `/jobs/*`) | ✅ | `netlify/functions/*.mjs` |

---

## Planned / backlog

| Idea | Status | Notes |
|---|---|---|
| Loss breakdown on the Reports page with date filter | 📝 | extend `filter_lost_leads` usage |
| Record an explicit `lost_at` date when a lead is marked lost | 📝 | precise loss-timing vs the current enquiry-date proxy |
| Overdue follow-up reminders (not just due-today) | 📝 | toggle in `reminders.due_followups` |
