"""Update event quoted amounts, dates, and add client payments."""
import datetime, os

os.environ.setdefault("DATABASE_URL", "sqlite:///./lif.db")
os.environ.setdefault("APP_PASSWORD", "testpass123")
os.environ.setdefault("SECRET_KEY", "import-script-key")

from sqlmodel import Session, select
from app.database import engine
from app.models import Event, EventPayment

# event_name → (event_date, quoted_amount, amount_received)
# pending = quoted - received
DATA = {
    "Sushmitha":  (datetime.date(2026, 5,  6), 300_000, 300_000),
    "Swetha":     (datetime.date(2026, 5, 10), 340_000, 300_000),
    "Chandru":    (datetime.date(2026, 5,  9),  90_000,  90_000),
    "Sai Prasad": (datetime.date(2026, 5, 13), 230_000, 200_000),
}

with Session(engine) as session:
    for name, (ev_date, quoted, received) in DATA.items():
        ev = session.exec(select(Event).where(Event.name == name)).first()
        if ev is None:
            print(f"  SKIP — event '{name}' not found")
            continue

        ev.event_date    = ev_date
        ev.quoted_amount = quoted
        session.add(ev)

        # remove any existing payments for this event before re-adding
        existing = session.exec(
            select(EventPayment).where(EventPayment.event_id == ev.id)
        ).all()
        for p in existing:
            session.delete(p)
        session.flush()

        # add the received payment (date = event date)
        session.add(EventPayment(
            event_id=ev.id,
            amount=received,
            payment_date=ev_date,
            notes="Client payment",
        ))

        pending = quoted - received
        print(f"  {name}: date={ev_date}, quoted={quoted:,}, received={received:,}, pending={pending:,}")

    session.commit()
    print("\nDone.")
