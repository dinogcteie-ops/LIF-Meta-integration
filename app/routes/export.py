from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response

from app.database import get_db, SheetDB
from app.services.drive import DriveError, push_workbook_to_drive
from app.services.excel import build_csv, build_workbook
from app.services.sheets import SheetsError, push_to_sheet

router = APIRouter()

VALID_CSV_KINDS = {"events", "payments", "expenses", "summary"}


@router.get("/export/xlsx")
def export_xlsx(db: SheetDB = Depends(get_db)):
    data     = build_workbook(db)
    filename = f"lif-export-{date.today().isoformat()}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/csv")
def export_csv(kind: str = Query("events"), db: SheetDB = Depends(get_db)):
    if kind not in VALID_CSV_KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {VALID_CSV_KINDS}")
    data     = build_csv(db, kind)
    filename = f"lif-{kind}-{date.today().isoformat()}.csv"
    return Response(
        content=data,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/export/sheets")
def export_sheets(request: Request, db: SheetDB = Depends(get_db)):
    try:
        url = push_to_sheet(db)
    except SheetsError as e:
        request.session["flash"] = f"Sheets export failed: {e}"
        return RedirectResponse(url="/reports", status_code=303)
    except Exception as e:
        request.session["flash"] = f"Sheets export failed: {type(e).__name__}: {e}"
        return RedirectResponse(url="/reports", status_code=303)
    request.session["flash"] = f"Pushed to Google Sheet: {url}"
    return RedirectResponse(url="/reports", status_code=303)


@router.post("/export/drive")
def export_drive(request: Request, db: SheetDB = Depends(get_db)):
    try:
        url = push_workbook_to_drive(db)
    except (DriveError, SheetsError) as e:
        request.session["flash"] = f"Drive export failed: {e}"
        return RedirectResponse(url="/reports", status_code=303)
    except Exception as e:
        request.session["flash"] = f"Drive export failed: {type(e).__name__}: {e}"
        return RedirectResponse(url="/reports", status_code=303)
    request.session["flash"] = f"Uploaded Excel file to Google Drive: {url}"
    return RedirectResponse(url="/reports", status_code=303)
