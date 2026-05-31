"""One-time bulk import for the May 2026 expenses."""
import datetime
import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite:///./lif.db")
os.environ.setdefault("APP_PASSWORD", "testpass123")
os.environ.setdefault("SECRET_KEY", "import-script-key")

from sqlmodel import Session, select
from app.database import engine, init_db
from app.models import (
    CategoryScope, Event, EventStatus, Expense, ExpenseCategory, PaymentStatus
)


def get_or_create_event(session: Session, name: str) -> Event:
    ev = session.exec(select(Event).where(Event.name == name)).first()
    if ev:
        return ev
    ev = Event(name=name, status=EventStatus.active)
    session.add(ev)
    session.commit()
    session.refresh(ev)
    print(f"  Created event: {name}")
    return ev


def get_or_create_category(session: Session, name: str) -> ExpenseCategory:
    cat = session.exec(
        select(ExpenseCategory)
        .where(ExpenseCategory.name == name)
        .where(ExpenseCategory.scope == CategoryScope.event)
    ).first()
    if cat:
        return cat
    cat = ExpenseCategory(name=name, scope=CategoryScope.event, is_active=True)
    session.add(cat)
    session.commit()
    session.refresh(cat)
    print(f"  Created category: {name}")
    return cat


# Raw data: (date_str D/M/YYYY, event_name, category_name, amount, days_or_None)
ROWS = [
    ("6/5/2026",  "Sushmitha",  "Photographer-1",  24000, "4"),
    ("6/5/2026",  "Sushmitha",  "Photographer-2",  16000, "2"),
    ("6/5/2026",  "Sushmitha",  "Videographer-1",  35000, "4"),
    ("6/5/2026",  "Sushmitha",  "Videographer-2",  24000, "2"),
    ("6/5/2026",  "Sushmitha",  "Hard Disk",        11000, None),
    ("6/5/2026",  "Sushmitha",  "Travel",            9000, "4"),
    ("6/5/2026",  "Sushmitha",  "Food",              2500, "4"),
    ("6/5/2026",  "Sushmitha",  "Album",            40000, None),
    ("6/5/2026",  "Sushmitha",  "Editor-1",         25000, None),
    ("6/5/2026",  "Sushmitha",  "Editor-3",         25000, None),
    ("10/5/2026", "Swetha",     "Photographer-1",   51000, "3"),
    ("10/5/2026", "Swetha",     "Videographer-1",   20000, "2"),
    ("10/5/2026", "Swetha",     "Photographer-2",   25000, "3"),
    ("10/5/2026", "Swetha",     "Hard Disk",         13000, None),
    ("10/5/2026", "Swetha",     "Videographer-2",   30000, "3"),
    ("10/5/2026", "Swetha",     "Videographer-3",   25000, "3"),
    ("10/5/2026", "Swetha",     "Equipment Rent",    9000, "3"),
    ("10/5/2026", "Swetha",     "Travel",             7000, "4"),
    ("10/5/2026", "Swetha",     "Miscellaneous",      2000, None),
    ("10/5/2026", "Swetha",     "Album",             30000, None),
    ("10/5/2026", "Swetha",     "Editor-1",          15000, None),
    ("10/5/2026", "Swetha",     "Editor-2",          15000, None),
    ("9/5/2026",  "Chandru",    "Photographer-1",    15000, "1"),
    ("9/5/2026",  "Chandru",    "Videographer-1",    15000, "1"),
    ("9/5/2026",  "Chandru",    "Editor-1",           6500, None),
    ("9/5/2026",  "Chandru",    "Editor-2",           8000, None),
    ("9/5/2026",  "Chandru",    "Album",             10000, None),
    ("9/5/2026",  "Chandru",    "Commission",         7500, None),
    ("13/5/2026", "Sai Prasad", "Videographer-1",    42000, "1"),
    ("13/5/2026", "Sai Prasad", "Photographer-1",    40500, "1"),
    ("13/5/2026", "Sai Prasad", "Live Link",          6000, None),
    ("13/5/2026", "Sai Prasad", "Equipment Rent",     6000, None),
    ("13/5/2026", "Sai Prasad", "Album",             30000, None),
    ("13/5/2026", "Sai Prasad", "Travel",             3500, None),
    ("13/5/2026", "Sai Prasad", "Hard Disk",         11000, None),
    ("13/5/2026", "Sai Prasad", "Food",               5000, None),
    ("13/5/2026", "Sai Prasad", "Editor-1",          15000, None),
    ("13/5/2026", "Sai Prasad", "Editor-2",          15000, None),
    ("13/5/2026", "Sai Prasad", "Reels",              5000, None),
    ("13/5/2026", "Sai Prasad", "Commission",        10000, None),
    ("13/5/2026", "Sai Prasad", "Miscellaneous",     10000, None),
]


def parse_date(s: str) -> datetime.date:
    d, m, y = s.split("/")
    return datetime.date(int(y), int(m), int(d))


def main():
    init_db()
    with Session(engine) as session:
        print("Importing events and categories…")
        # pre-cache all events and categories needed
        event_names = sorted({r[1] for r in ROWS})
        cat_names   = sorted({r[2] for r in ROWS})

        events = {n: get_or_create_event(session, n) for n in event_names}
        cats   = {n: get_or_create_category(session, n) for n in cat_names}

        print(f"\nInserting {len(ROWS)} expenses…")
        inserted = 0
        for date_str, ev_name, cat_name, amount, days in ROWS:
            notes = f"{days} day(s)" if days else None
            exp = Expense(
                date=parse_date(date_str),
                event_id=events[ev_name].id,
                category_id=cats[cat_name].id,
                scope=CategoryScope.event,
                payment_status=PaymentStatus.paid,
                amount=float(amount),
                paid_amount=float(amount),
                notes=notes,
            )
            session.add(exp)
            inserted += 1

        session.commit()
        print(f"\nDone — {inserted} expenses imported across {len(events)} events.")
        print("\nEvent totals:")
        for name, ev in sorted(events.items()):
            session.refresh(ev)
            total = sum(e.amount for e in ev.expenses)
            print(f"  {name}: INR {total:,.0f}")


if __name__ == "__main__":
    main()
