"""One-time cleanup: merge duplicate / inconsistent payee rows into one canonical
row each, re-pointing their expenses first so nothing is orphaned.

The directory backfill (``migrate_backfill_directory.py``) faithfully materialized
the free-text ``paid_to`` values, which left a couple of near-duplicates:
``meta`` / ``Meta ADS`` and ``Sathish`` / ``sathish advance``. This folds each set
into a single canonical payee. Non-duplicate payees (Adobe, Canva subscriptions,
salaries, rent, etc.) are intentionally left untouched.

For each group: pick/ensure the canonical row (rename it to the exact canonical
name), move every expense from the alias rows onto it, then delete the now-empty
alias rows. Matched case-insensitively by name. Idempotent — a second run finds
only the canonical row and does nothing.

Preview first (no writes):
    python merge_payees.py

Apply the changes:
    python merge_payees.py --apply
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("LIF_ENV", "production")

from app.database import get_db

# (canonical display name, [every name — incl. the canonical — to fold together]).
MERGES: list[tuple[str, list[str]]] = [
    ("Meta Ads", ["meta", "Meta ADS", "Meta Ads"]),
    ("Sathish",  ["Sathish", "sathish advance"]),
]


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def main() -> None:
    apply = "--apply" in sys.argv
    db = get_db()

    payees = db.list_payees()
    # include_estimates so estimate-status expenses are re-pointed too (else a
    # delete could orphan / fail on a row still referencing the alias payee).
    expenses = db.list_expenses(include_estimates=True)
    exp_by_payee: dict[int, list] = {}
    for e in expenses:
        if e.payee_id:
            exp_by_payee.setdefault(e.payee_id, []).append(e)

    plans: list[dict] = []
    for canonical, names in MERGES:
        name_keys = {_norm(n) for n in names}
        matched = [p for p in payees if _norm(p.name) in name_keys]
        if not matched:
            continue
        # Prefer an existing row already named exactly canonical; else keep the
        # lowest id and rename it.
        keep = next((p for p in matched if p.name == canonical), None) or \
               min(matched, key=lambda p: p.id)
        aliases = [p for p in matched if p.id != keep.id]
        if not aliases and keep.name == canonical:
            continue   # already clean
        moved = sum(len(exp_by_payee.get(a.id, [])) for a in aliases)
        plans.append({"canonical": canonical, "keep": keep,
                      "aliases": aliases, "moved": moved})

    # ── Report ────────────────────────────────────────────────────────────────
    print(f"\nScanned {len(payees)} payees, {len(expenses)} expenses.\n")
    if not plans:
        print("Nothing to merge — payees already consistent.\n")
        return
    for pl in plans:
        keep = pl["keep"]
        print(f"Merge -> '{pl['canonical']}' (keep payee #{keep.id}"
              f"{'' if keep.name == pl['canonical'] else f', rename from {keep.name!r}'})")
        for a in pl["aliases"]:
            n = len(exp_by_payee.get(a.id, []))
            print(f"    fold in #{a.id} {a.name!r}  ({n} expense(s) re-pointed, then deleted)")
        print()

    if not apply:
        print("DRY RUN -- no changes written. Re-run with --apply to commit.\n")
        return

    # ── Apply ─────────────────────────────────────────────────────────────────
    for pl in plans:
        keep = pl["keep"]
        if keep.name != pl["canonical"]:
            db.update_payee(keep.id, name=pl["canonical"], payee_type=keep.payee_type,
                            phone=keep.phone, email=keep.email, notes=keep.notes)
            print(f"  RENAMED payee #{keep.id}: {keep.name!r} -> {pl['canonical']!r}")
        for a in pl["aliases"]:
            for e in exp_by_payee.get(a.id, []):
                db.set_expense_payee(e.id, keep.id)
            db.delete_payee(a.id)
            print(f"  MERGED #{a.id} {a.name!r} -> #{keep.id} "
                  f"({len(exp_by_payee.get(a.id, []))} expense(s))")
    print("\nDone.\n")


if __name__ == "__main__":
    main()
