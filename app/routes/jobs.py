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
from app.services.recurring import post_due_recurring
from app.services.reminders import build_followup_digest, build_new_leads_email, due_followups

log = logging.getLogger(__name__)
router = APIRouter()


def _notify_new_leads(imported_leads: list, db) -> None:
    """Email owner(s) about newly imported leads. Silent on any failure."""
    if not email_configured():
        return
    studio = db.get_settings_dict()
    recipients = [e.strip() for e in (studio.get("role_owners") or "").split(",")
                  if e.strip()]
    if not recipients:
        return
    try:
        subject, html = build_new_leads_email(
            imported_leads, get_settings().public_base_url or "https://lifcrm.netlify.app"
        )
        send_email(subject, html, recipients)
    except EmailError as exc:
        log.warning("New-lead notification failed: %s", exc)


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


@router.post("/jobs/recurring-expenses")
def recurring_expenses_job(request: Request, dry_run: bool = False,
                           db: SheetDB = Depends(get_db)):
    """Materialize due recurring expenses for the current month (idempotent)."""
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=403)
    summary = post_due_recurring(db, dry_run=dry_run)
    if _logged_in(request):
        if summary["posted"]:
            request.session["flash"] = (
                f"Posted {summary['posted']} recurring expense(s) as pending "
                f"({summary['skipped']} up to date)."
            )
        else:
            request.session["flash"] = "Recurring expenses are up to date — nothing posted."
        return RedirectResponse(url="/expenses", status_code=303)
    return JSONResponse(summary)


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

    if not dry_run and summary["imported"] > 0:
        _notify_new_leads(summary["imported_leads"], db)

    if ui:
        if dry_run:
            request.session["flash"] = (
                f"Dry run: {summary['total_rows']} row(s) across "
                f"{summary['tabs_processed']} tab(s); would import "
                f"{summary['imported']}, skip {summary['skipped']} (already exist). "
                f"Headers seen: {', '.join(summary['headers']) or 'none'}."
            )
            return RedirectResponse(url="/settings", status_code=303)
        request.session["flash"] = (
            f"Imported {summary['imported']} new lead(s) from "
            f"{summary['tabs_processed']} tab(s) "
            f"({summary['skipped']} skipped — already in CRM)."
        )
        return RedirectResponse(url="/leads", status_code=303)
    return JSONResponse(summary)
