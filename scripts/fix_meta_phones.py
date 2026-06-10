"""Compare Google Sheet lead rows against existing DB leads, match by name,
and optionally fill in missing phone numbers.

Dry-run by default — prints what would change.
Pass --apply to write phone updates to the DB.

Run from the worktree root with the venv + .env present:
  python -m scripts.fix_meta_phones
  python -m scripts.fix_meta_phones --apply
"""
from __future__ import annotations

import re
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.database import get_db
from app.services.lead_intake import (
    _open_spreadsheet, _normalize_phone, _strip_meta_id, _FIELD_CANDIDATES,
)

APPLY = "--apply" in sys.argv

# ─── Manual mappings for leads whose names differ between sheet and DB ────────
# sheet row index (1-based) → DB lead id
# These are leads where name-matching fails due to spelling diffs or Unicode
# decorative fonts that get stripped to empty.
_MANUAL: dict[int, int] = {
    1:  35,   # "Ramchander Chefs" → "ramachander chef"
    10: 42,   # "𝑆𝑝𝑎𝑛𝑑𝑎𝑛𝑎 𝑁𝑒𝑚𝑎𝑙𝑖" (Unicode italic) → "Spandana"
    11: 43,   # "सत्यम् आनऺद" (Devanagari) → "Satyam"
}

# ─── False-positive word-matches to ignore ────────────────────────────────────
# row index → DB lead id pairs that share a word but are different people
_EXCLUDE: set[tuple[int, int]] = {
    (34, 32),  # "sk king" matched "sk masiur" by word "sk" — different people
}


def _pick(row: dict, lower_map: dict, field: str) -> str:
    for cand in _FIELD_CANDIDATES.get(field, []):
        if cand in lower_map:
            val = row.get(lower_map[cand], "")
            if val not in (None, ""):
                return str(val).strip()
    return ""


def _name_words(name: str) -> set[str]:
    """Meaningful words in name (lowercase alpha only, length > 1)."""
    return {w for w in re.sub(r"[^a-z ]", "", name.lower()).split() if len(w) > 1}


def main():
    db = get_db()
    all_leads = db.list_leads()
    lead_by_id = {l.id: l for l in all_leads}

    phone_to_lead: dict[str, object] = {}
    for lead in all_leads:
        if lead.contact:
            norm = _normalize_phone(lead.contact)
            if norm:
                phone_to_lead[norm] = lead

    empty_phone_leads = [l for l in all_leads if not (l.contact and _normalize_phone(l.contact))]
    name_index: dict[str, list] = {}
    for lead in empty_phone_leads:
        for word in _name_words(lead.client_name or ""):
            name_index.setdefault(word, []).append(lead)

    print("Loading sheet rows…")
    sh = _open_spreadsheet()
    worksheets = sh.worksheets()
    all_rows: list[dict] = []
    for ws in worksheets:
        for r in ws.get_all_records():
            r["_tab"] = ws.title
            all_rows.append(r)
    print(f"  {len(all_rows)} rows across {len(worksheets)} tab(s)\n")

    updates: list[tuple[int, str, str, str]] = []   # (lead_id, contact, sheet_name, reason)
    new_leads: list[tuple[int, dict]] = []
    already_ok: list[tuple[int, dict, object]] = []

    for rownum, row in enumerate(all_rows, start=1):
        lower_map  = {str(k).strip().lower(): k for k in row.keys()}
        name       = _pick(row, lower_map, "client_name")
        raw_phone  = _pick(row, lower_map, "contact")
        norm_phone = _normalize_phone(raw_phone) if raw_phone else ""
        contact    = re.sub(r"^p:", "", raw_phone).strip() if raw_phone else ""

        # 1. Phone match — already in DB with correct phone
        if norm_phone and norm_phone in phone_to_lead:
            already_ok.append((rownum, row, phone_to_lead[norm_phone]))
            continue

        # 2. Manual mapping (Unicode / spelling overrides)
        if rownum in _MANUAL:
            db_lead = lead_by_id.get(_MANUAL[rownum])
            if db_lead and contact:
                updates.append((db_lead.id, contact, name, f"manual row {rownum}"))
            continue

        # 3. Word-overlap name match (skip known false positives)
        sheet_words = _name_words(name)
        candidates: dict[int, object] = {}
        for word in sheet_words:
            for lead in name_index.get(word, []):
                if (rownum, lead.id) not in _EXCLUDE:
                    candidates[lead.id] = lead

        if candidates and contact:
            # Take the best candidate (most words in common)
            best = max(candidates.values(),
                       key=lambda l: len(sheet_words & _name_words(l.client_name or "")))
            updates.append((best.id, contact, name, f"name match → db:{repr(best.client_name)}"))
            continue

        # 4. No match — new lead
        new_leads.append((rownum, row))

    # ── Report ────────────────────────────────────────────────────────────────
    print(f"{'─'*72}")
    print(f"PHONE MATCH ({len(already_ok)}) — already in DB with correct phone")
    print(f"{'─'*72}")
    for rownum, row, lead in already_ok:
        lower_map = {str(k).strip().lower(): k for k in row.keys()}
        print(f"  row {rownum:>2}: {_pick(row, lower_map, 'client_name'):<30} db id={lead.id}")

    print()
    print(f"{'─'*72}")
    print(f"PHONE UPDATES ({len(updates)}) — in DB but phone missing")
    print(f"{'─'*72}")
    for lead_id, contact, name, reason in updates:
        print(f"  id={lead_id:<4} {name:<30} → {contact}  ({reason})")

    print()
    print(f"{'─'*72}")
    print(f"NEW LEADS ({len(new_leads)}) — not in DB, would be imported")
    print(f"{'─'*72}")
    for rownum, row in new_leads:
        lower_map = {str(k).strip().lower(): k for k in row.keys()}
        name  = _pick(row, lower_map, "client_name")
        phone = _pick(row, lower_map, "contact")
        print(f"  row {rownum:>2}: {name:<30}  {phone}  [{row.get('_tab','')}]")

    print()
    print(f"{'─'*72}")
    print(f"SUMMARY")
    print(f"{'─'*72}")
    print(f"  Already correct (phone match): {len(already_ok):>3}")
    print(f"  Phone updates needed:          {len(updates):>3}")
    print(f"  Genuinely new leads:           {len(new_leads):>3}")

    if not updates:
        print("\nNo updates needed.")
        return

    print()
    if APPLY:
        print(f"APPLYING {len(updates)} update(s)…")
        for lead_id, contact, name, reason in updates:
            db.update_lead(lead_id, contact=contact)
            print(f"  Updated id={lead_id} ({name}) → {contact}")
        print("Done. Re-run dry run to confirm, then do the sheet import.")
    else:
        print("DRY RUN — pass --apply to write these updates.")


if __name__ == "__main__":
    main()
