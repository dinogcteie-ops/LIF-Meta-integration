"""One-time migration: normalize legacy free-text lead 'rejection_reason'
values onto the standardized LostReason categories.

Before the Lost-reason dropdown existed, this field was free text (e.g.
"budget 2L", "didn't pick call"). This script keyword-maps those onto the
canonical categories in app.enums.LostReason. Values that are already
canonical are left untouched; anything it can't confidently classify is
reported so you can fix it by hand in the UI.

Run a preview first (no writes):
    python migrate_lost_reasons.py

Apply the changes:
    python migrate_lost_reasons.py --apply
"""
import os
import sys
from pathlib import Path

# Ensure app modules are importable + use the production DB from .env
sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("LIF_ENV", "production")

from app.database import get_db
from app.enums import LostReason

# Canonical values already in the enum — never rewrite these.
CANONICAL = {r.value for r in LostReason}

# ─── Keyword → canonical category mapping ────────────────────────────────────
# Each entry: (canonical LostReason value, [substrings that imply it]).
# Order matters — the first rule whose keyword appears in the lowercased
# reason text wins, so the more specific rules are listed first.
RULES: list[tuple[str, list[str]]] = [
    (LostReason.spam.value, [
        "spam", "invalid", "fake", "bot", "junk", "wrong number", "test enquiry",
    ]),
    (LostReason.unreachable.value, [
        "did not pick", "didnt pick", "didn't pick", "not pick", "no pick",
        "not picking", "no response", "no reply", "not reachable", "unreachable",
        "no answer", "not answering", "call not", "number switched off",
        "switched off", "not responding", "ghost",
    ]),
    (LostReason.date_conflict.value, [
        "same date", "same day", "date not available", "date unavailable",
        "already booked", "booked on", "date clash", "date conflict",
        "calendar", "not available on", "we were booked", "fully booked",
    ]),
    (LostReason.budget.value, [
        "budget", "price", "pricing", "costly", "expensive", "too high",
        "too costly", "out of budget", "low budget", "rate", "quote high",
        "cost", "cheaper", "afford", "money", "negotiat",
    ]),
    (LostReason.slow_followup.value, [
        "delay", "delayed", "late", "follow up", "follow-up", "followup",
        "no follow", "slow", "took too long", "response time", "lost momentum",
        "we were late", "missed follow",
    ]),
    (LostReason.competitor.value, [
        "competitor", "other photographer", "another photographer",
        "another studio", "other studio", "went with", "chose other",
        "booked someone", "different vendor", "someone else",
    ]),
    (LostReason.out_of_area.value, [
        "location", "out of station", "outstation", "travel", "too far",
        "far away", "service area", "different city", "other city",
        "outside", "distance",
    ]),
    (LostReason.event_cancelled.value, [
        "postpone", "postponed", "cancel", "cancelled", "called off",
        "not happening", "event off", "no longer", "dropped the plan",
    ]),
    (LostReason.style_mismatch.value, [
        "style", "portfolio", "did not like", "didn't like", "not like our",
        "quality", "work not", "not impressed", "look", "aesthetic",
    ]),
    (LostReason.not_serious.value, [
        "just asking", "just browsing", "browsing", "enquiry only",
        "not serious", "window", "casual", "timepass", "not genuine",
    ]),
]


def classify(reason: str) -> str | None:
    """Return the canonical category for a free-text reason, or None if unsure."""
    text = reason.lower()
    for canonical, keywords in RULES:
        if any(kw in text for kw in keywords):
            return canonical
    return None


def main() -> None:
    apply = "--apply" in sys.argv
    db = get_db()
    leads = db.list_leads()

    to_change: list[tuple] = []   # (lead, old, new)
    already_ok = 0
    unmapped: dict[str, int] = {}  # distinct unrecognized value -> count

    for lead in leads:
        old = (lead.rejection_reason or "").strip()
        if not old:
            continue
        if old in CANONICAL:
            already_ok += 1
            continue
        new = classify(old)
        if new is None:
            unmapped[old] = unmapped.get(old, 0) + 1
        else:
            to_change.append((lead, old, new))

    # ── Report ───────────────────────────────────────────────────────────────
    print(f"\nScanned {len(leads)} leads.")
    print(f"  Already standardized : {already_ok}")
    print(f"  Will be mapped       : {len(to_change)}")
    print(f"  Could not classify   : {sum(unmapped.values())} "
          f"({len(unmapped)} distinct)\n")

    if to_change:
        print("Proposed changes:")
        print(f"  {'#':>5}  {'Client':<24}  {'Old reason':<28}  ->  New category")
        print(f"  {'-'*5}  {'-'*24}  {'-'*28}      {'-'*28}")
        for lead, old, new in to_change:
            name = (lead.client_name or "")[:24]
            print(f"  {lead.id:>5}  {name:<24}  {old[:28]:<28}  ->  {new}")
        print()

    if unmapped:
        print("Unmapped values (left unchanged -- fix manually in the UI, or "
              "add a keyword rule and re-run):")
        for val, n in sorted(unmapped.items(), key=lambda x: -x[1]):
            print(f"  ({n}x)  {val!r}")
        print()

    if not apply:
        print("DRY RUN -- no changes written. Re-run with --apply to commit.\n")
        return

    if not to_change:
        print("Nothing to apply.\n")
        return

    # ── Apply: update only rejection_reason, preserving every other field ─────
    updated = 0
    for lead, old, new in to_change:
        db.update_lead(
            lead.id,
            client_name=lead.client_name, contact=lead.contact,
            event_type=lead.event_type, tentative_date=lead.tentative_date,
            source=lead.source, status=lead.status,
            quoted_amount=lead.quoted_amount, notes=lead.notes,
            client_id=lead.client_id,
            num_events=lead.num_events, revised_quote=lead.revised_quote,
            follow_ups=lead.follow_ups, rejection_reason=new,
            meta_campaign=lead.meta_campaign, referral_name=lead.referral_name,
            followup_status=lead.followup_status, followup_date=lead.followup_date,
        )
        updated += 1
        print(f"  UPDATED #{lead.id}: {old!r} -> {new!r}")

    print(f"\nDone! Updated {updated} lead(s).\n")


if __name__ == "__main__":
    main()
