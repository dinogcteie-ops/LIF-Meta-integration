"""Scheduled / on-demand job endpoints.

Both are token-gated the same way as /meta/refresh: allowed if a logged-in user
triggered them (a button in Settings) OR the caller presents ?token= matching
meta_verify_token (the Netlify cron functions). Public-path bypass for /jobs/ is
handled in app.auth so the middleware doesn't redirect these to /login.
"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import get_settings
from app.database import get_db, SheetDB
from app.services.email import EmailError, email_configured, send_email
from app.services.lead_intake import IntakeError, run_intake
from app.services.reminders import build_followup_digest, due_followups

log = logging.getLogger(__name__)
router = APIRouter()


def _logged_in(request: Request) -> bool:
    return bool(request.session.get("user")) if request.session is not None else False


def _authorized(request: Request) -> bool:
    token = request.query_params.get("token", "")
    verify = get_settings().meta_verify_token
    return _logged_in(request) or bool(verify and token == verify)


@router.post("/jobs/followup-reminders")
def followup_reminders(request: Request, db: SheetDB = Depends(get_db)):
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=403)
    ui = _logged_in(request)
    studio = db.get_settings_dict()
    enabled = (studio.get("followup_enabled", "on") or "on").lower() != "off"
    recipients = [e.strip() for e in (studio.get("followup_recipients") or "").split(",")
                  if e.strip()]

    def _finish(payload: dict, msg: str | None = None):
        if ui:
            if msg:
                request.session["flash"] = msg
            return RedirectResponse(url="/leads", status_code=303)
        return JSONResponse(payload)

    if not enabled:
        return _finish({"skipped": "disabled"}, "Follow-up reminders are turned off in Settings.")
    if not recipients:
        return _finish({"skipped": "no_recipients"}, "No follow-up recipients set in Settings.")
    if not email_configured():
        return _finish({"error": "smtp_not_configured"},
                       "Email isn't configured (set SMTP_USER / SMTP_PASSWORD).")

    leads = due_followups(db, date.today())
    if not leads:
        return _finish({"sent": 0, "due": 0}, "No follow-ups due today — nothing sent.")

    subject, html = build_followup_digest(leads, get_settings().public_base_url)
    try:
        send_email(subject, html, recipients)
    except EmailError as exc:
        log.warning("Follow-up email failed: %s", exc)
        return _finish({"error": str(exc)}, f"Email failed: {exc}")

    return _finish(
        {"sent": len(recipients), "due": len(leads)},
        f"Sent follow-up reminder for {len(leads)} lead(s) to {len(recipients)} recipient(s).",
    )


@router.post("/jobs/import-leads")
def import_leads_job(request: Request, dry_run: bool = False,
                     db: SheetDB = Depends(get_db)):
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=403)
    ui = _logged_in(request)
    try:
        summary = run_intake(db, dry_run=dry_run)
    except IntakeError as exc:
        log.warning("Lead intake error: %s", exc)
        if ui:
            request.session["flash"] = f"Lead import error: {exc}"
            return RedirectResponse(url="/settings", status_code=303)
        return JSONResponse({"error": str(exc)}, status_code=400)

    if ui:
        if dry_run:
            request.session["flash"] = (
                f"Dry run: {summary['new_rows']} new row(s); would import "
                f"{summary['imported']}, skip {summary['skipped']}. "
                f"Headers seen: {', '.join(summary['headers']) or 'none'}."
            )
            return RedirectResponse(url="/settings", status_code=303)
        request.session["flash"] = (
            f"Imported {summary['imported']} new lead(s) from the sheet "
            f"({summary['skipped']} skipped)."
        )
        return RedirectResponse(url="/leads", status_code=303)
    return JSONResponse(summary)
