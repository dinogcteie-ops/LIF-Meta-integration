# Life in Frame — Studio CRM

FastAPI + Jinja/HTMX studio CRM (events, clients, leads, payees, expenses,
receivables/payables, reports, client portal) with a **Zoho-style UI**, backed by
**Supabase Postgres**, with **Meta (Facebook/Instagram) Ads** lead capture and
metrics. Hosted behind **Netlify** (domain/CDN proxy) → **Render** (Python app).

## Architecture

```
Browser → Netlify (domain, HTTPS, CDN, proxy)  →  FastAPI on Render  →  Supabase Postgres
                                                        └→ Meta Graph API (leads + insights)
Meta → POST /webhooks/meta/leads (creates Leads)
```

The data layer is abstracted behind `app/services/db.py` (`Database`), returning the
dataclasses in `app/domain.py`. Routes/templates are storage-agnostic.

## Local development

```bash
python -m venv .venv
.venv\Scripts\pip install ".[dev]"      # Windows  (use .venv/bin/pip on macOS/Linux)
copy .env.example .env                   # then edit values (SQLite works out of the box)
.venv\Scripts\uvicorn app.main:app --reload --port 8000
```

Visit http://127.0.0.1:8000 and log in with `APP_PASSWORD`. Run tests with `pytest`.

## Deploy

### 1. Supabase (database)
1. Create a project at supabase.com (free tier).
2. Copy the **pooled** connection string (Project → Database → Connection pooling,
   port `6543`) → use as `DATABASE_URL`.

### 2. Render (Python app)
1. New → Blueprint, point at this repo (`render.yaml`).
2. Set env vars: `APP_PASSWORD`, `DATABASE_URL` (Supabase pooled), and the `META_*`
   values. Tables are auto-created on first boot.
3. Confirm `https://<app>.onrender.com/healthz` returns `{"status":"ok"}`.

### 3. Netlify (public front)
1. Edit `netlify.toml`: set the redirect target to your Render URL.
2. Deploy the site; set env `BACKEND_ORIGIN` (Render URL) and `META_REFRESH_TOKEN`
   (= backend `META_VERIFY_TOKEN`) for the scheduled metrics refresh.
3. Point your custom domain at Netlify.

### 4. Meta Ads
1. Create a Meta app (Business), add **Webhooks** + **Lead Ads**; connect your Page.
2. Generate a long-lived **Page access token** → `META_PAGE_ACCESS_TOKEN`; set
   `META_APP_SECRET`, `META_AD_ACCOUNT_ID` (numeric), and `META_VERIFY_TOKEN`.
3. Subscribe the webhook to `leadgen` at
   `https://<your-domain>/webhooks/meta/leads` using your verify token.
4. View campaign metrics + captured leads at `/meta`.

## One-time data migration (old Google Sheet → Supabase)

```bash
# Set GOOGLE_SHEET_ID + GOOGLE_SA_JSON_PATH (or _BASE64) and DATABASE_URL (target), then:
.venv\Scripts\pip install ".[dev]"
.venv\Scripts\python -m scripts.migrate_sheets_to_db
```

The script preserves ids, is idempotent, and resets Postgres sequences afterward.

## Security note

If migrating from the old Hugging Face deployment: rotate the previously-committed
Hugging Face token and Google service-account key. Keep all secrets in env vars —
never commit `.env` or `*-sa.json` (see `.gitignore`).
