import calendar as cal_mod
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Request

from app.database import get_db, SheetDB
from app.enums import EventStatus
from app.templating import templates

router = APIRouter()

_ALL_STATUSES = [s.value for s in EventStatus]


@router.get("/calendar")
def calendar_view(
    request: Request,
    year: Optional[int] = None,
    month: Optional[int] = None,
    statuses: str = "",
    db: SheetDB = Depends(get_db),
):
    today = date.today()
    year  = year  or today.year
    month = month or today.month

    # Status filter — comma-separated list. Empty = show all.
    if statuses:
        active = {s.strip() for s in statuses.split(",") if s.strip() in _ALL_STATUSES}
    else:
        active = set(_ALL_STATUSES)

    events = [ev for ev in db.list_events() if ev.status.value in active]
    events_by_date: dict[str, list] = {}
    for ev in events:
        if ev.event_date:
            key = ev.event_date.isoformat()
            events_by_date.setdefault(key, []).append(ev)

    # Calendar grid: list of weeks, each week is 7 ints (0 = empty day)
    weeks = cal_mod.monthcalendar(year, month)

    # Prev / next month navigation
    if month == 1:
        prev = (year - 1, 12)
    else:
        prev = (year, month - 1)

    if month == 12:
        next_ = (year + 1, 1)
    else:
        next_ = (year, month + 1)

    return templates.TemplateResponse(
        request,
        "calendar.html",
        {
            "year": year,
            "month": month,
            "month_name": cal_mod.month_name[month],
            "weeks": weeks,
            "events_by_date": events_by_date,
            "prev": prev,
            "next": next_,
            "today": today,
            "all_statuses":   _ALL_STATUSES,
            "active_statuses": active,
        },
    )
