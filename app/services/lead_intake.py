"""Interim inbound-lead capture from a Google Sheet.

Until the Meta webhook is the sole lead source, new enquiries arrive in a Google
Sheet (typically a Google Form responses tab — append-only). This reads new rows
and creates Leads via the existing Database layer.

Dedup uses a simple, robust high-water mark: the number of data rows already
imported, stored in the ``leads_intake_cursor`` setting. Each run imports rows
beyond the cursor, then advances it. This assumes the sheet is append-only
(true for Form responses); it never edits the sheet.

The column mapping is intentionally tolerant — it matches several common header
spellings case-insensitively. Run the endpoint with ``?dry_run=1`` first: the
summary echoes the sheet's real headers and a sample so the mapping can be
confirmed/tightened to the actual sheet.
"""
from __future__ import annotations

from datetime import date, datetime

from app.config import get_settings
from app.database import SheetDB

_CURSOR_KEY = "leads_intake_cursor"

# Candidate header names (lowercased) → lead field. First match wins.
_FIELD_CANDIDATES: dict[str, list[str]] = {
    "client_name": ["client name", "name", "full name", "your name", "couple name"],
    "contact":     ["contact", "phone", "mobile", "whatsapp", "contact number",
                    "phone number", "mobile number"],
    "event_type":  ["event type", "event", "occasion", "type of event", "service",
                    "function"],
    "date":        ["event date", "tentative date", "date", "function date",
                    "wedding date"],
    "source":      ["source", "how did you hear", "how did you hear about us",
                    "reference", "referral"],
    "notes":       ["notes", "message", "requirements", "details", "comments",
                    "remarks", "additional info"],
}


class IntakeError(Exception):
    pass


def _open_worksheet():
    import gspread  # lazy — keeps the import cost off the request path
    from app.services.google_auth import load_google_credentials

    s = get_settings()
    if not s.leads_intake_sheet_id:
        raise IntakeError("LEADS_INTAKE_SHEET_ID is not set.")
    gc = gspread.authorize(load_google_credentials())
    sh = gc.open_by_key(s.leads_intake_sheet_id)
    try:
        return sh.worksheet(s.leads_intake_tab)
    except Exception as exc:  # noqa: BLE001
        titles = [ws.title for ws in sh.worksheets()]
        raise IntakeError(
            f"Worksheet '{s.leads_intake_tab}' not found. Available tabs: {titles}"
        ) from exc


def _pick(row: dict, lower_map: dict, field: str) -> str:
    for cand in _FIELD_CANDIDATES[field]:
        if cand in lower_map:
            val = row.get(lower_map[cand], "")
            if val not in (None, ""):
                return str(val).strip()
    return ""


def _parse_date(value: str):
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None  # unparseable dates are dropped (kept in notes instead)


def run_intake(db: SheetDB, dry_run: bool = False) -> dict:
    """Import new sheet rows as leads. Returns a summary dict."""
    ws = _open_worksheet()
    records = ws.get_all_records()  # list[dict], keyed by header row
    headers = list(records[0].keys()) if records else []

    settings = db.get_settings_dict()
    try:
        cursor = int(settings.get(_CURSOR_KEY) or 0)
    except ValueError:
        cursor = 0

    new_rows = records[cursor:]
    imported, skipped = 0, 0
    sample: list[dict] = []

    for row in new_rows:
        lower_map = {str(k).strip().lower(): k for k in row.keys()}
        name = _pick(row, lower_map, "client_name")
        if not name:
            skipped += 1
            continue
        contact    = _pick(row, lower_map, "contact")
        event_type = _pick(row, lower_map, "event_type")
        source     = _pick(row, lower_map, "source") or "Google Form"
        notes      = _pick(row, lower_map, "notes")
        raw_date   = _pick(row, lower_map, "date")
        tentative  = _parse_date(raw_date)
        if raw_date and not tentative:
            notes = (notes + f"\nEvent date (raw): {raw_date}").strip()

        if len(sample) < 5:
            sample.append({"client_name": name, "contact": contact,
                           "event_type": event_type, "date": raw_date,
                           "source": source})

        if not dry_run:
            db.create_lead(
                client_name=name, contact=contact, event_type=event_type,
                tentative_date=tentative, source=source, status="new",
                notes=notes,
            )
        imported += 1

    cursor_after = cursor + len(new_rows)
    if not dry_run and new_rows:
        db.set_settings({_CURSOR_KEY: str(cursor_after)})

    return {
        "dry_run": dry_run,
        "total_rows": len(records),
        "cursor_before": cursor,
        "cursor_after": cursor if dry_run else cursor_after,
        "new_rows": len(new_rows),
        "imported": imported,
        "skipped": skipped,
        "headers": headers,
        "sample": sample,
    }
