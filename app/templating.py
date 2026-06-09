import os
import time

from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR, get_settings

settings = get_settings()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Cache-busting tag for our own CSS/JS. Changes per deploy (Render sets
# RENDER_GIT_COMMIT), so browsers fetch fresh assets after each release instead
# of serving stale cached files. Falls back to process start time locally.
ASSET_VERSION = (os.environ.get("RENDER_GIT_COMMIT") or "")[:8] or str(int(time.time()))
templates.env.globals["asset_version"] = ASSET_VERSION


def _indian_format(n: int) -> str:
    """Format integer using Indian number grouping: 1,00,000."""
    s = str(abs(n))
    if len(s) <= 3:
        return s
    # Last 3 digits, then groups of 2
    groups = [s[-3:]]
    s = s[:-3]
    while s:
        groups.append(s[-2:])
        s = s[:-2]
    return ",".join(reversed(groups))


def _format_money(value: float | int | None) -> str:
    sym = templates.env.globals.get("currency_symbol", "₹")
    if value is None:
        return "—"
    try:
        v = float(value)
        sign = "-" if v < 0 else ""
        abs_v = abs(v)
        if abs_v == int(abs_v):
            return f"{sign}{sym}{_indian_format(int(abs_v))}"
        return f"{sign}{sym}{_indian_format(int(abs_v))}.{round((abs_v % 1) * 100):02d}"
    except (TypeError, ValueError):
        return str(value)


def _format_pct(value: float | None) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return str(value)


templates.env.filters["money"] = _format_money
templates.env.filters["pct"] = _format_pct
templates.env.filters["zip"] = zip
# Legacy globals (overwritten by refresh_template_globals at startup)
templates.env.globals["currency"] = settings.currency
templates.env.globals["currency_symbol"] = "₹"
templates.env.globals["studio_name"] = "Life in Frame"
templates.env.globals["studio_sub"]  = "Studio Finance"
templates.env.globals["ar_grace_days"] = 0
templates.env.globals["reminder_cadence_days"] = 7


def refresh_template_globals(studio_settings: dict) -> None:
    """Sync Jinja2 globals from DB-backed studio settings.

    Call at startup (after DB init) and whenever settings are saved.
    """
    templates.env.globals["studio_name"]          = studio_settings.get("studio_name", "Life in Frame")
    templates.env.globals["studio_sub"]           = studio_settings.get("studio_sub", "Studio Finance")
    templates.env.globals["currency_symbol"]      = studio_settings.get("currency_symbol", "₹")
    templates.env.globals["ar_grace_days"]        = int(studio_settings.get("ar_grace_days") or 0)
    templates.env.globals["reminder_cadence_days"] = int(studio_settings.get("reminder_cadence_days") or 7)
