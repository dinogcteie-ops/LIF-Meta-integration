"""Studio settings, recurring expense generation, and audit log — Phase 3."""
from datetime import date as date_cls

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.database import get_db, SheetDB
from app.templating import refresh_template_globals, templates

router = APIRouter()

ENTITY_TYPES = ["event", "payment", "expense", "client", "payee"]


# ── Settings ──────────────────────────────────────────────────────────────────

@router.get("/settings")
def settings_page(request: Request, db: SheetDB = Depends(get_db)):
    studio = db.get_settings_dict()
    cats   = {c.id: c for c in db.list_categories()}
    recurring = [e for e in db.list_expenses() if e.is_recurring]
    for e in recurring:
        e.category = cats.get(e.category_id)
    today = date_cls.today()
    return templates.TemplateResponse(
        request, "settings.html", {
            "studio": studio,
            "recurring": recurring,
            "today_year":  today.year,
            "today_month": today.month,
        }
    )


@router.post("/settings")
def save_settings(
    request: Request,
    studio_name:           str = Form("Life in Frame"),
    studio_sub:            str = Form("Studio Finance"),
    currency_symbol:       str = Form("₹"),
    ar_grace_days:         str = Form("0"),
    reminder_cadence_days: str = Form("7"),
    db: SheetDB = Depends(get_db),
):
    db.set_settings({
        "studio_name":           studio_name.strip() or "Life in Frame",
        "studio_sub":            studio_sub.strip()  or "Studio Finance",
        "currency_symbol":       currency_symbol.strip() or "₹",
        "ar_grace_days":         str(max(0, int(ar_grace_days or 0))),
        "reminder_cadence_days": str(max(1, int(reminder_cadence_days or 7))),
    })
    # Refresh Jinja2 globals immediately so all pages see the new values
    refresh_template_globals(db.get_settings_dict())
    request.session["flash"] = "Settings saved."
    return RedirectResponse(url="/settings", status_code=303)


# ── Notification / follow-up reminder settings ────────────────────────────────

@router.post("/settings/notifications")
def save_notifications(
    request: Request,
    followup_recipients: str = Form(""),
    followup_enabled:    str = Form(""),   # checkbox: present only when checked
    db: SheetDB = Depends(get_db),
):
    db.set_settings({
        "followup_recipients": followup_recipients.strip(),
        "followup_enabled":    "on" if followup_enabled else "off",
    })
    request.session["flash"] = "Follow-up reminder settings saved."
    return RedirectResponse(url="/settings", status_code=303)


# ── Recurring expense generation ──────────────────────────────────────────────

@router.post("/settings/generate_recurring")
def generate_recurring(
    request: Request,
    year:  str = Form(""),
    month: str = Form(""),
    db: SheetDB = Depends(get_db),
):
    today = date_cls.today()
    yr = int(year)  if year.strip()  else today.year
    mo = int(month) if month.strip() else today.month
    created = db.generate_recurring_expenses(yr, mo)
    n = len(created)
    if n:
        request.session["flash"] = (
            f"Generated {n} recurring expense{'s' if n != 1 else ''} "
            f"for {yr}-{mo:02d}. Review them below."
        )
    else:
        request.session["flash"] = (
            f"No new recurring expenses needed for {yr}-{mo:02d} "
            "(all already exist or no templates defined)."
        )
    return RedirectResponse(url="/expenses", status_code=303)


# ── Audit log ─────────────────────────────────────────────────────────────────

@router.get("/audit")
def audit_log(
    request: Request,
    entity_type: str = "",
    db: SheetDB = Depends(get_db),
):
    entries = db.list_audit(limit=200, entity_type=entity_type or None)
    return templates.TemplateResponse(
        request, "audit.html", {
            "entries": entries,
            "entity_types": ENTITY_TYPES,
            "filter_type": entity_type,
        }
    )
