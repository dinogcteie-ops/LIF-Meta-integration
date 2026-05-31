from datetime import date

import gspread

from app.config import get_settings
from app.services.excel import all_datasets
from app.services.google_auth import GoogleAuthError, load_google_credentials
from app.database import SheetDB


class SheetsError(Exception):
    pass


def push_to_sheet(db: SheetDB) -> str:
    s = get_settings()
    if not s.google_sheet_id:
        raise SheetsError("GOOGLE_SHEET_ID is not set in .env.")

    try:
        creds = load_google_credentials()
    except GoogleAuthError as e:
        raise SheetsError(str(e)) from e

    client = gspread.authorize(creds)
    sh = client.open_by_key(s.google_sheet_id)

    datasets = all_datasets(db)
    existing_titles = {ws.title for ws in sh.worksheets()}

    for tab_name, rows in datasets.items():
        rows = _normalize(rows)
        cols = max((len(r) for r in rows), default=1)
        if tab_name in existing_titles:
            ws = sh.worksheet(tab_name)
            ws.clear()
        else:
            ws = sh.add_worksheet(title=tab_name, rows=max(len(rows) + 5, 50), cols=max(cols, 10))
        if rows:
            ws.update(values=rows, range_name="A1")

    return sh.url


def _normalize(rows: list[list]) -> list[list]:
    out = []
    for row in rows:
        out.append([v.isoformat() if isinstance(v, date) else v for v in row])
    return out
