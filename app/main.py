import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth import LoginRequiredMiddleware
from app.config import BASE_DIR, get_settings
from app.routes import (
    auth, calendar, categories, clients, dashboard, delivery, events, expenses, export,
    jobs, leads, meta, payables, payees, portal, quick, receivables, reports,
    settings, workshop,
)
from app.templating import templates

config = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        from app.db.engine import init_db
        from app.services.db import get_db
        from app.services.lead_report import warm_matplotlib
        from app.templating import refresh_template_globals
        init_db()
        db = get_db()
        db.seed_if_empty()
        refresh_template_globals(db.get_settings_dict())
        # Pay matplotlib's one-time font-cache cost now, so the first lead-report
        # render at request time stays fast (avoids the proxy timeout).
        warm_matplotlib()
    except Exception as exc:
        logging.warning("Could not initialise database on startup: %s", exc)
    yield


app = FastAPI(title="Life in Frame Tracker", lifespan=lifespan)


@app.middleware("http")
async def read_cache(request: Request, call_next):
    """Enable the per-request read cache for every GET, so a page's many repeated
    table reads collapse to one round-trip each (the main page-to-page latency
    against remote Supabase). GET-only: mutating requests stay uncached so a handler
    never reads its own stale pre-write snapshot. The cache is a ContextVar isolated
    per request — set here (middleware shares the endpoint's context, unlike a
    threadpooled dependency) and cleared in ``finally``."""
    if request.method != "GET":
        return await call_next(request)
    from app.services.db import get_db
    db = get_db()
    db.enable_cache()
    try:
        return await call_next(request)
    finally:
        db.disable_cache()


app.add_middleware(LoginRequiredMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.secret_key,
    https_only=config.cookie_secure,   # True on Render (COOKIE_SECURE=true); False for localhost
    same_site="lax",
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(auth.router)
app.include_router(calendar.router)
app.include_router(dashboard.router)
app.include_router(delivery.router)
app.include_router(events.router)
app.include_router(clients.router)
app.include_router(payees.router)
app.include_router(receivables.router)
app.include_router(payables.router)
app.include_router(expenses.router)
app.include_router(categories.router)
app.include_router(reports.router)
app.include_router(export.router)
app.include_router(leads.router)
app.include_router(workshop.router)
app.include_router(settings.router)
app.include_router(quick.router)
app.include_router(portal.router)
app.include_router(meta.router)
app.include_router(jobs.router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.exception_handler(404)
async def not_found(request: Request, _exc):
    return templates.TemplateResponse(
        request, "404.html", {"path": request.url.path}, status_code=404
    )


@app.exception_handler(500)
async def server_error(request: Request, _exc):
    return templates.TemplateResponse(
        request, "500.html", {}, status_code=500
    )


@app.get("/")
def root(request: Request):
    return RedirectResponse(url="/dashboard", status_code=303)
