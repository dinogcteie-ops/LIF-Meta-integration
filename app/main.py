import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth import LoginRequiredMiddleware
from app.config import BASE_DIR, get_settings
from app.routes import (
    auth, calendar, categories, clients, dashboard, events, expenses, export,
    leads, meta, payables, payees, portal, quick, receivables, reports, settings, workshop,
)
from app.templating import templates

config = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        from app.db.engine import init_db
        from app.services.db import get_db
        from app.templating import refresh_template_globals
        init_db()
        db = get_db()
        db.seed_if_empty()
        refresh_template_globals(db.get_settings_dict())
    except Exception as exc:
        logging.warning("Could not initialise database on startup: %s", exc)
    yield


app = FastAPI(title="Life in Frame Tracker", lifespan=lifespan)

app.add_middleware(LoginRequiredMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.secret_key,
    https_only=False,
    same_site="lax",
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(auth.router)
app.include_router(calendar.router)
app.include_router(dashboard.router)
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
