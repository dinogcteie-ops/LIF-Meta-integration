"""One-time backfill: populate the Clients and Payees directories from data
that already lives on events and expenses.

The studio started with empty Clients and Payees lists even though events carry
a free-text ``client_name`` and expenses carry a free-text ``paid_to``. This
script materializes those into real directory rows and links them back:

  * Clients  — for every **completed** or **booked** event that has a client
    name but is not yet linked to a Client row, find-or-create a Client (matched
    case-insensitively by name) and set the event's ``client_id``.
  * Payees   — for every expense that names a ``paid_to`` but is not yet linked
    to a Payee row, find-or-create a Payee (matched by name) and set the
    expense's ``payee_id``.

Existing directory rows are reused (never duplicated); names that already match
are linked, not recreated. Re-runnable / idempotent — a second run finds
everything already linked and does nothing.

Preview first (no writes):
    python migrate_backfill_directory.py

Apply the changes:
    python migrate_backfill_directory.py --apply
"""
import os
import sys
from pathlib import Path

# Ensure app modules are importable + use the production DB from .env
sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("LIF_ENV", "production")

from app.database import get_db
from app.enums import EventStatus

# Event statuses that represent a real, won engagement worth a Client record.
CLIENT_STATUSES = {EventStatus.completed.value, EventStatus.booked.value}

# paid_to placeholders that aren't real payee names.
_BLANK_PAYEE = {"", "none", "n/a", "na", "-", "unspecified"}

# Default directory type for a vendor/freelancer reconstructed from free text.
DEFAULT_PAYEE_TYPE = "freelancer"


def _norm(name: str) -> str:
    return (name or "").strip().lower()


def main() -> None:
    apply = "--apply" in sys.argv
    db = get_db()

    events   = db.list_events()
    expenses = db.list_expenses()

    # ── Clients ───────────────────────────────────────────────────────────────
    # name(lower) -> client_id, seeded with whatever already exists.
    client_index: dict[str, int] = {_norm(c.name): c.id for c in db.list_clients()}
    clients_to_create: dict[str, str] = {}   # name(lower) -> display name
    events_to_link: list[tuple] = []          # (event, resolved display name)
    skipped_no_name = 0
    already_linked  = 0

    for ev in events:
        if ev.status.value not in CLIENT_STATUSES:
            continue
        if ev.client_id:
            already_linked += 1
            continue
        name = (ev.client_name or "").strip()
        if not name:
            skipped_no_name += 1
            continue
        key = _norm(name)
        if key not in client_index and key not in clients_to_create:
            clients_to_create[key] = name
        events_to_link.append((ev, name))

    # ── Payees ────────────────────────────────────────────────────────────────
    payee_index: dict[str, int] = {_norm(p.name): p.id for p in db.list_payees()}
    payees_to_create: dict[str, str] = {}     # name(lower) -> display name
    expenses_to_link: list[tuple] = []        # (expense, resolved display name)
    exp_already_linked = 0
    exp_no_payee       = 0

    for e in expenses:
        if e.payee_id:
            exp_already_linked += 1
            continue
        name = (e.paid_to or "").strip()
        if _norm(name) in _BLANK_PAYEE:
            exp_no_payee += 1
            continue
        key = _norm(name)
        if key not in payee_index and key not in payees_to_create:
            payees_to_create[key] = name
        expenses_to_link.append((e, name))

    # ── Report ────────────────────────────────────────────────────────────────
    print(f"\nScanned {len(events)} events, {len(expenses)} expenses.\n")
    print("CLIENTS (from completed/booked events)")
    print(f"  New clients to create : {len(clients_to_create)}")
    print(f"  Events to link        : {len(events_to_link)}")
    print(f"  Already linked        : {already_linked}")
    print(f"  Skipped (no name)     : {skipped_no_name}\n")
    if clients_to_create:
        print("  New client rows:")
        for name in sorted(clients_to_create.values(), key=str.lower):
            print(f"    + {name}")
        print()

    print("PAYEES (from expense paid_to)")
    print(f"  New payees to create  : {len(payees_to_create)}")
    print(f"  Expenses to link      : {len(expenses_to_link)}")
    print(f"  Already linked        : {exp_already_linked}")
    print(f"  Skipped (no paid_to)  : {exp_no_payee}\n")
    if payees_to_create:
        print("  New payee rows:")
        for name in sorted(payees_to_create.values(), key=str.lower):
            print(f"    + {name}")
        print()

    if not apply:
        print("DRY RUN -- no changes written. Re-run with --apply to commit.\n")
        return

    if not (events_to_link or expenses_to_link):
        print("Nothing to apply.\n")
        return

    # ── Apply: create directory rows, then link records ───────────────────────
    for key, name in clients_to_create.items():
        client = db.create_client(name=name)
        client_index[key] = client.id
        print(f"  CLIENT created #{client.id}: {name!r}")

    linked_events = 0
    for ev, name in events_to_link:
        cid = client_index[_norm(name)]
        db.set_event_client(ev.id, cid)
        linked_events += 1
    print(f"  Linked {linked_events} event(s) to clients.")

    for key, name in payees_to_create.items():
        payee = db.create_payee(name=name, payee_type=DEFAULT_PAYEE_TYPE)
        payee_index[key] = payee.id
        print(f"  PAYEE created #{payee.id}: {name!r}")

    linked_expenses = 0
    for e, name in expenses_to_link:
        pid = payee_index[_norm(name)]
        db.set_expense_payee(e.id, pid)
        linked_expenses += 1
    print(f"  Linked {linked_expenses} expense(s) to payees.")

    print(f"\nDone! Created {len(clients_to_create)} client(s) + "
          f"{len(payees_to_create)} payee(s); linked {linked_events} event(s) "
          f"and {linked_expenses} expense(s).\n")


if __name__ == "__main__":
    main()
