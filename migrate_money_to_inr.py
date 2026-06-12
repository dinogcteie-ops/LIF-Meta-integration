"""One-time migration: normalize lakh-stored quote fields to plain rupees.

Historically ``events.quoted_amount``, ``leads.quoted_amount`` and
``leads.revised_quote`` were entered in *lakhs* (``2.3`` meant ₹2,30,000) while
payments, expenses and budgets were stored in *rupees*. That mismatch made the
dashboard ambiguous (``₹2.30`` next to ``₹2,30,000``) and silently broke the
profit / pending / margin math in ``app/services/reports.py`` (it did
``quoted − expense`` across two units).

This converts the three quote fields to rupees (× 100,000) so the whole app
uses ONE unit.

Idempotent & safe to re-run: only values ``0 < v < THRESHOLD`` are treated as
lakhs and scaled. Lakh quotes are at most ~100 (₹1 crore); a real rupee quote
is ≥ ₹50,000 — so once scaled, a value is ≥ THRESHOLD and a re-run skips it.

Preview (no writes):
    python migrate_money_to_inr.py

Apply the changes:
    python migrate_money_to_inr.py --apply

Run from the repo root with .env present (it points at production Supabase).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("LIF_ENV", "production")

from app.database import get_db

# Lakh quotes are <= ~100; rupee quotes are >= ~50,000. Anything below this is
# treated as a legacy lakh value that still needs scaling.
THRESHOLD = 10_000.0
FACTOR = 100_000.0


def _needs_scaling(v) -> bool:
    return v is not None and 0 < float(v) < THRESHOLD


def main() -> None:
    apply = "--apply" in sys.argv
    db = get_db()
    events = db.list_events()
    leads = db.list_leads()

    # (kind, id, label, field -> (old, new)) ; one entry per row that changes.
    event_changes = []   # (event, {"quoted_amount": (old, new)})
    lead_changes = []    # (lead, {field: (old, new)})

    for ev in events:
        if _needs_scaling(ev.quoted_amount):
            event_changes.append((ev, ev.quoted_amount, round(ev.quoted_amount * FACTOR, 2)))

    for ld in leads:
        fields = {}
        if _needs_scaling(ld.quoted_amount):
            fields["quoted_amount"] = (ld.quoted_amount, round(ld.quoted_amount * FACTOR, 2))
        if _needs_scaling(ld.revised_quote):
            fields["revised_quote"] = (ld.revised_quote, round(ld.revised_quote * FACTOR, 2))
        if fields:
            lead_changes.append((ld, fields))

    # ── Report ────────────────────────────────────────────────────────────────
    print(f"\nScanned {len(events)} events, {len(leads)} leads "
          f"(threshold < {THRESHOLD:,.0f} = lakhs, scale × {FACTOR:,.0f}).\n")

    print(f"Events to rescale: {len(event_changes)}")
    for ev, old, new in event_changes:
        print(f"  #{ev.id:>4}  {(ev.name or '')[:28]:<28}  {old:>12,.2f}  ->  {new:>14,.2f}")

    print(f"\nLeads to rescale: {len(lead_changes)}")
    for ld, fields in lead_changes:
        parts = ", ".join(f"{f} {o:,.2f}->{n:,.2f}" for f, (o, n) in fields.items())
        print(f"  #{ld.id:>4}  {(ld.client_name or '')[:24]:<24}  {parts}")

    if not apply:
        print("\nDRY RUN — no changes written. Re-run with --apply to commit.\n")
        return

    if not event_changes and not lead_changes:
        print("\nNothing to apply.\n")
        return

    # ── Apply: rewrite ONLY the money fields, preserving everything else ───────
    ev_done = ld_done = 0
    for ev, _old, new in event_changes:
        db.update_event(
            ev.id,
            name=ev.name, client_name=ev.client_name, client_id=ev.client_id,
            event_date=ev.event_date, quoted_amount=new,
            status=ev.status.value if hasattr(ev.status, "value") else ev.status,
            notes=ev.notes, event_type=ev.event_type, location=ev.location,
            referral_source=ev.referral_source,
            payment_due_dates=ev.payment_due_dates,
            delivery_status=ev.delivery_status,
        )
        ev_done += 1
        print(f"  EVENT #{ev.id}: quoted_amount -> {new:,.2f}")

    for ld, fields in lead_changes:
        new_quote = fields.get("quoted_amount", (None, ld.quoted_amount))[1]
        new_revised = fields.get("revised_quote", (None, ld.revised_quote))[1]
        db.update_lead(
            ld.id,
            client_name=ld.client_name, contact=ld.contact,
            event_type=ld.event_type, tentative_date=ld.tentative_date,
            source=ld.source, status=ld.status,
            quoted_amount=new_quote, notes=ld.notes, client_id=ld.client_id,
            num_events=ld.num_events, revised_quote=new_revised,
            follow_ups=ld.follow_ups, rejection_reason=ld.rejection_reason,
            meta_campaign=ld.meta_campaign, referral_name=ld.referral_name,
            followup_status=ld.followup_status, followup_date=ld.followup_date,
            budget_range=ld.budget_range, city=ld.city,
        )
        ld_done += 1
        print(f"  LEAD  #{ld.id}: {', '.join(fields)} -> rupees")

    print(f"\nDone! Rescaled {ev_done} event(s) and {ld_done} lead(s).\n")


if __name__ == "__main__":
    main()
