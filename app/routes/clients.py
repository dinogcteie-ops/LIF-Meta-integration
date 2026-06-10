"""Client directory — Phase 2."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.database import get_db, SheetDB
from app.services.reports import event_profits, top_clients
from app.rbac import require
from app.templating import templates

router = APIRouter(dependencies=[Depends(require("directory.view"))])


@router.get("/clients")
def list_clients(request: Request, db: SheetDB = Depends(get_db)):
    clients = db.list_clients()
    # Compute total events + revenue per client for the list table
    clients_map = {c.id: c for c in clients}
    event_rows = event_profits(db)
    stats = top_clients(event_rows, clients_map, n=999)
    # Build a stats lookup keyed by client_id (linked) or name (unlinked)
    stats_by_cid = {s.client_id: s for s in stats if s.client_id}
    return templates.TemplateResponse(
        request, "clients/list.html", {
            "clients": clients,
            "stats_by_cid": stats_by_cid,
        }
    )


@router.get("/clients/new")
def new_client_form(request: Request):
    return templates.TemplateResponse(
        request, "clients/form.html", {"client": None}
    )


@router.post("/clients")
def create_client(
    request: Request,
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    db: SheetDB = Depends(get_db),
):
    c = db.create_client(
        name=name.strip(),
        phone=phone.strip() or None,
        email=email.strip() or None,
        address=address.strip() or None,
        notes=notes.strip() or None,
    )
    return RedirectResponse(url=f"/clients/{c.id}", status_code=303)


@router.get("/clients/{client_id}")
def client_detail(client_id: int, request: Request, db: SheetDB = Depends(get_db)):
    client = db.get_client(client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    # Gather all events for this client:
    # 1) linked by client_id  2) legacy free-text match on client_name
    event_rows = event_profits(db)
    linked = [
        r for r in event_rows
        if (r.event.client_id == client_id)
        or (r.event.client_id is None and r.event.client_name
            and r.event.client_name.lower() == client.name.lower())
    ]
    total_quoted   = round(sum(r.event.quoted_amount for r in linked), 2)
    total_received = round(sum(r.income for r in linked), 2)
    total_pending  = round(sum(r.pending_from_client for r in linked), 2)

    return templates.TemplateResponse(
        request, "clients/detail.html", {
            "client": client,
            "event_rows": linked,
            "total_quoted": total_quoted,
            "total_received": total_received,
            "total_pending": total_pending,
        }
    )


@router.get("/clients/{client_id}/edit")
def edit_client_form(client_id: int, request: Request, db: SheetDB = Depends(get_db)):
    client = db.get_client(client_id)
    if client is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "clients/form.html", {"client": client}
    )


@router.post("/clients/{client_id}")
def update_client(
    client_id: int,
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    db: SheetDB = Depends(get_db),
):
    c = db.update_client(
        client_id,
        name=name.strip(),
        phone=phone.strip() or None,
        email=email.strip() or None,
        address=address.strip() or None,
        notes=notes.strip() or None,
    )
    if c is None:
        raise HTTPException(status_code=404)
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/delete")
def delete_client(client_id: int, db: SheetDB = Depends(get_db)):
    if db.get_client(client_id) is None:
        raise HTTPException(status_code=404)
    db.delete_client(client_id)
    return RedirectResponse(url="/clients", status_code=303)
