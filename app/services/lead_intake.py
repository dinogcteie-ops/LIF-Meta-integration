"""Inbound-lead capture from a Google Sheet (Meta Lead Ads export format).

Multi-tab strategy
------------------
Meta appends leads to an existing tab or creates new tabs over time.  To handle
both without ever double-importing:

* **Phone number** and **Meta lead ID** are the dedup keys — running against
  the same rows twice is always safe.
* ``leads_intake_done_tabs`` (stored in Settings) tracks tabs that have been
  fully imported and are no longer the active tab.  Daily runs skip them.
* The **last tab** in the spreadsheet is always rescanned (Meta is still
  appending to it); all other tabs are scanned once, then marked done.
* First run (empty done-list): every tab is processed — historical import.

Column mapping covers the Meta Lead Ads export format:
  full_name, phone_number (p:+91… prefix stripped), city, campaign_name,
  platform (ig→Instagram fb→Meta), is_organic, what's_your_wedding_date?,
  _what_are_you_looking_for?, what's_your_approximate_wedding_photography_budget?

Run with ``?dry_run=1`` first; the response shows real headers + a sample.
"""
from __future__ import annotations

import re
from datetime import datetime

from app.config import get_settings
from app.database import SheetDB
from app.validators import normalize_phone


# ─── Column-name candidates ───────────────────────────────────────────────────

_FIELD_CANDIDATES: dict[str, list[str]] = {
    "client_name": [
        "full_name", "full name", "client name", "name", "your name",
        "couple name",
    ],
    "contact": [
        "phone_number", "phone number", "contact", "phone", "mobile",
        "whatsapp", "contact number", "mobile number",
    ],
    "event_type": [
        "_what_are_you_looking_for?", "what are you looking for",
        "what's_your_event_type?", "event type", "event", "occasion",
        "type of event", "service", "function",
    ],
    "date": [
        "what's_your_wedding_date?", "what's your wedding date?",
        "event date", "tentative date", "date", "function date",
        "wedding date",
    ],
    "notes": [
        "notes", "message", "requirements", "details", "comments",
        "remarks", "additional info",
    ],
    "budget": [
        "what's_your_approximate_wedding_photography_budget?",
        "what's your approximate wedding photography budget?",
        "budget", "approximate budget",
    ],
    "city":          ["city", "location"],
    "meta_id":       ["id"],
    "platform":      ["platform"],
    "is_organic":    ["is_organic", "is organic"],
    "campaign_name": ["campaign_name", "campaign name"],
    "ad_name":       ["ad_name", "ad name"],
}

_SERVICE_TO_EVENT_TYPE: dict[str, str] = {
    "bridal_photography":   "Wedding",
    "full_wedding_coverage": "Wedding",
    "couple_shoot":         "Portrait",
    "customized_package":   "Wedding",
    "engagement":           "Engagement",
    "reception":            "Reception",
}

_DONE_KEY = "leads_intake_done_tabs"


class IntakeError(Exception):
    pass


# ─── Sheet helpers ────────────────────────────────────────────────────────────

def _open_spreadsheet():
    import gspread
    from app.services.google_auth import load_google_credentials

    s = get_settings()
    if not s.leads_intake_sheet_id:
        raise IntakeError("LEADS_INTAKE_SHEET_ID is not set.")
    gc = gspread.authorize(load_google_credentials())
    return gc.open_by_key(s.leads_intake_sheet_id)


def _pick(row: dict, lower_map: dict, field: str) -> str:
    for cand in _FIELD_CANDIDATES.get(field, []):
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
    return None


# Single source of truth for phone comparison lives in app.validators;
# kept under the old name because this module's dedup logic (and its tests)
# refer to it as _normalize_phone.
_normalize_phone = normalize_phone


def _strip_meta_id(raw: str) -> str:
    return re.sub(r'^l:', '', (raw or "").strip(), flags=re.IGNORECASE)


# ─── Row processor ────────────────────────────────────────────────────────────

def _process_row(row: dict, existing_phones: set, existing_meta_ids: set,
                 dry_run: bool, db: SheetDB) -> str:
    """Process one sheet row. Returns 'imported', 'skipped', or 'no_name'."""
    lower_map = {str(k).strip().lower(): k for k in row.keys()}

    name = _pick(row, lower_map, "client_name")
    if not name:
        return "no_name"

    raw_phone  = _pick(row, lower_map, "contact")
    phone      = _normalize_phone(raw_phone) if raw_phone else ""
    if phone and phone in existing_phones:
        return "skipped"

    raw_meta_id = _strip_meta_id(_pick(row, lower_map, "meta_id"))
    if raw_meta_id and raw_meta_id in existing_meta_ids:
        return "skipped"

    raw_date    = _pick(row, lower_map, "date")
    tentative   = _parse_date(raw_date)
    raw_service = _pick(row, lower_map, "event_type")
    event_type  = _SERVICE_TO_EVENT_TYPE.get(
        raw_service.lower().replace(" ", "_"), raw_service or ""
    )
    budget      = _pick(row, lower_map, "budget")
    city        = _pick(row, lower_map, "city")
    campaign    = _pick(row, lower_map, "campaign_name")
    ad          = _pick(row, lower_map, "ad_name")
    platform    = _pick(row, lower_map, "platform").lower()
    is_organic  = _pick(row, lower_map, "is_organic").lower()
    base_notes  = _pick(row, lower_map, "notes")

    note_parts = []
    if budget:
        note_parts.append(f"Budget: {budget}")
    if raw_service:
        note_parts.append(f"Looking for: {raw_service.replace('_', ' ')}")
    if city:
        note_parts.append(f"City: {city}")
    if raw_date and not tentative:
        note_parts.append(f"Wedding date (raw): {raw_date}")
    if ad:
        note_parts.append(f"Ad: {ad}")
    if base_notes:
        note_parts.append(base_notes)
    notes = " | ".join(note_parts)

    meta_campaign = (is_organic == "false")
    source = "Instagram" if platform == "ig" else "Meta"
    contact = re.sub(r'^p:', '', raw_phone).strip() if raw_phone else ""

    if not dry_run:
        db.create_lead(
            client_name=name, contact=contact, event_type=event_type,
            tentative_date=tentative, source=source, status="new",
            notes=notes, meta_campaign=meta_campaign,
            meta_lead_id=raw_meta_id or None,
            meta_campaign_name=campaign or None,
            budget_range=budget, city=city,
        )
        if phone:
            existing_phones.add(phone)
        if raw_meta_id:
            existing_meta_ids.add(raw_meta_id)

    return "imported"


# ─── Main entry point ─────────────────────────────────────────────────────────

def run_intake(db: SheetDB, dry_run: bool = False) -> dict:
    """Import leads from all unprocessed tabs + the latest (active) tab.

    Returns a summary dict with per-tab breakdown and totals.
    """
    sh = _open_spreadsheet()
    worksheets = sh.worksheets()
    if not worksheets:
        return {"dry_run": dry_run, "tabs_processed": 0,
                "total_rows": 0, "imported": 0, "skipped": 0,
                "headers": [], "sample": [], "tabs": []}

    settings     = db.get_settings_dict()
    done_tabs    = {t.strip() for t in
                   (settings.get(_DONE_KEY) or "").split(",") if t.strip()}
    latest_title = worksheets[-1].title   # always rescan the last (active) tab

    # Decide which tabs to open this run
    to_process = [ws for ws in worksheets
                  if ws.title not in done_tabs or ws.title == latest_title]

    # Build dedup sets from existing leads (once, before processing any tab)
    existing_leads   = db.list_leads()
    existing_phones  = {_normalize_phone(l.contact)
                        for l in existing_leads if l.contact}
    existing_meta_ids = {str(l.meta_lead_id)
                         for l in existing_leads if l.meta_lead_id}

    total_imported = total_skipped = total_rows = 0
    all_headers: list[str]       = []
    sample: list[dict]           = []
    imported_leads: list[dict]   = []
    tab_summaries: list[dict]    = []
    newly_done: list[str]        = []

    for ws in to_process:
        records = ws.get_all_records()
        if not all_headers and records:
            all_headers = list(records[0].keys())

        tab_imported = tab_skipped = 0
        for row in records:
            result = _process_row(row, existing_phones, existing_meta_ids,
                                  dry_run, db)
            if result == "imported":
                tab_imported += 1
                lower_map = {str(k).strip().lower(): k for k in row.keys()}
                raw_phone = _pick(row, lower_map, "contact")
                raw_svc   = _pick(row, lower_map, "event_type")
                lead_info = {
                    "name":      _pick(row, lower_map, "client_name"),
                    "phone":     re.sub(r"^p:", "", raw_phone).strip() if raw_phone else "",
                    "city":      _pick(row, lower_map, "city"),
                    "event_type": _SERVICE_TO_EVENT_TYPE.get(
                                    raw_svc.lower().replace(" ", "_"), raw_svc),
                    "campaign":  _pick(row, lower_map, "campaign_name"),
                    "tab":       ws.title,
                }
                imported_leads.append(lead_info)
                if len(sample) < 5:
                    sample.append(lead_info)
            else:
                tab_skipped += 1

        total_imported += tab_imported
        total_skipped  += tab_skipped
        total_rows     += len(records)

        tab_summaries.append({
            "tab": ws.title, "rows": len(records),
            "imported": tab_imported, "skipped": tab_skipped,
        })

        # Mark as done if it's not the latest tab (latest still gets new rows)
        if not dry_run and ws.title != latest_title:
            newly_done.append(ws.title)

    if not dry_run and newly_done:
        updated = done_tabs | set(newly_done)
        db.set_settings({_DONE_KEY: ", ".join(sorted(updated))})

    return {
        "dry_run":        dry_run,
        "tabs_total":     len(worksheets),
        "tabs_processed": len(to_process),
        "total_rows":     total_rows,
        "imported":       total_imported,
        "skipped":        total_skipped,
        "headers":        all_headers,
        "sample":         sample,
        "imported_leads": imported_leads,
        "tabs":           tab_summaries,
    }
