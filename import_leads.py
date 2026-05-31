"""One-time import of leads from 'LIF enquiries - Sheet1.csv'.

Run:  python import_leads.py
"""
import csv
import os
import sys
from datetime import date, datetime
from pathlib import Path

# Ensure app modules are importable
sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("LIF_ENV", "production")

from app.database import get_db


# ─── Status mapping from CSV to app enum ─────────────────────────────────────
STATUS_MAP = {
    "quote sent":  "quoted",
    "complete":    "won",
    "not working": "lost",
    "meeting":     "new",
}

# ─── Event type normalization ─────────────────────────────────────────────────
EVENT_TYPE_MAP = {
    "wedding":             "Wedding",
    "wed":                 "Wedding",
    "engagement":          "Engagement",
    "engagement/ wedding": "Wedding",
    "receptiom":           "Reception",
    "reception":           "Reception",
}


def parse_date_flexible(s: str):
    """Try to parse various date formats from the CSV. Returns date or None."""
    s = (s or "").strip()
    if not s:
        return None
    # Try DD/MM/YY and DD/MM/YYYY
    for fmt in ("%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None  # Non-parseable dates like "july dec", "Nov", "12th sep" stay as notes


def parse_quote(s: str) -> float:
    """Parse quote in lakhs (e.g. '4.25' → 4.25)."""
    s = (s or "").strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def determine_source(meta_campaign: str, referral: str) -> str:
    """Determine lead source from meta_campaign and referral columns."""
    referral = (referral or "").strip()
    meta = (meta_campaign or "").strip().lower()
    if referral:
        return "Referral"
    if meta == "yes":
        return "Instagram"  # Meta campaigns are typically Instagram/Facebook
    return ""


def main():
    csv_path = Path(__file__).parent.parent / "LIF enquiries - Sheet1.csv"
    if not csv_path.exists():
        # Try alternate location
        csv_path = Path(r"C:\Users\DineshMani\Downloads\LIF enquiries - Sheet1.csv")
    if not csv_path.exists():
        print(f"ERROR: CSV file not found at {csv_path}")
        sys.exit(1)

    print(f"Reading CSV from: {csv_path}")
    db = get_db()

    # Check existing leads to avoid duplicates
    existing = db.list_leads()
    existing_names = {l.client_name.lower().strip() for l in existing}
    print(f"Found {len(existing)} existing leads in database.")

    imported = 0
    skipped = 0

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            client_name = (row.get("CLIENT NAME") or "").strip()
            if not client_name:
                continue

            # Skip if already exists
            if client_name.lower() in existing_names:
                print(f"  SKIP (exists): {client_name}")
                skipped += 1
                continue

            # Parse fields
            enquiry_date_str = (row.get("ENQUIRY DATE") or "").strip()
            event_type_raw = (row.get("EVENT TYPE") or "").strip().lower()
            num_events_raw = (row.get("NO OF EVENTS") or "").strip()
            quote_raw = (row.get("QUOTE ") or row.get("QUOTE") or "").strip()
            revised_quote_raw = (row.get("REVISED QUOTE") or "").strip()
            event_date_str = (row.get("EVENT DATE") or "").strip()
            status_raw = (row.get("STATUS") or "").strip().lower()
            follow_ups = (row.get("FOLLOW UPS") or "").strip()
            rejection_reason = (row.get("REJECTION REASON") or "").strip()
            meta_campaign = (row.get("META CAMPAIGN") or "").strip()
            referral = (row.get("REFERAL") or "").strip()

            # Map event type
            event_type = EVENT_TYPE_MAP.get(event_type_raw, "Wedding" if "wed" in event_type_raw else "Other")

            # Parse number of events
            try:
                num_events = int(float(num_events_raw)) if num_events_raw else 0
            except (ValueError, TypeError):
                num_events = 0

            # Parse dates
            tentative_date = parse_date_flexible(event_date_str)

            # Parse quotes (stored in lakhs)
            quoted_amount = parse_quote(quote_raw)
            revised_quote = parse_quote(revised_quote_raw)

            # Map status
            status = STATUS_MAP.get(status_raw, "quoted")  # default to quoted for "quote sent"

            # Determine source
            source = determine_source(meta_campaign, referral)

            # Meta campaign boolean
            is_meta = meta_campaign.lower() == "yes"

            # Build notes with unparseable event date info
            notes_parts = []
            if event_date_str and not tentative_date:
                notes_parts.append(f"Event date: {event_date_str}")
            notes = "; ".join(notes_parts)

            # Create the lead
            lead = db.create_lead(
                client_name=client_name,
                contact="",
                event_type=event_type,
                tentative_date=tentative_date,
                source=source,
                status=status,
                quoted_amount=quoted_amount,
                notes=notes,
                num_events=num_events,
                revised_quote=revised_quote,
                follow_ups=follow_ups,
                rejection_reason=rejection_reason,
                meta_campaign=is_meta,
                referral_name=referral,
            )
            print(f"  IMPORTED #{lead.id}: {client_name} ({event_type}, {status}, ₹{quoted_amount}L)")
            imported += 1
            existing_names.add(client_name.lower())

    print(f"\nDone! Imported: {imported}, Skipped: {skipped}")


if __name__ == "__main__":
    main()
