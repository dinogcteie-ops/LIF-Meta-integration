from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.database import get_db, SheetDB
from app.enums import CategoryScope
from app.templating import templates

router = APIRouter()


@router.get("/categories")
def list_categories(request: Request, db: SheetDB = Depends(get_db)):
    cats = db.list_categories()
    grouped: dict[str, list] = {}
    for c in cats:
        grouped.setdefault(c.scope.value, []).append(c)
    return templates.TemplateResponse(
        request, "categories.html", {"grouped": grouped, "scopes": list(CategoryScope)},
    )


@router.post("/categories")
def create_category(
    name: str = Form(...),
    scope: str = Form(...),
    monthly_budget: str = Form("0"),
    db: SheetDB = Depends(get_db),
):
    budget = _parse_budget(monthly_budget)
    db.create_category(name=name.strip(), scope=scope, monthly_budget=budget or 0.0)
    return RedirectResponse(url="/categories", status_code=303)


@router.post("/categories/{cat_id}")
def toggle_or_rename(
    cat_id: int,
    name: str = Form(""),
    is_active: str = Form(""),
    monthly_budget: str = Form(""),
    db: SheetDB = Depends(get_db),
):
    cat = db.get_category(cat_id)
    if cat is None:
        raise HTTPException(status_code=404)
    new_name = name.strip() if name.strip() else cat.name
    db.update_category(
        cat_id,
        name=new_name,
        is_active=(is_active == "on"),
        monthly_budget=_parse_budget(monthly_budget),
    )
    return RedirectResponse(url="/categories", status_code=303)


def _parse_budget(s: str):
    """Return float or None (None = preserve existing)."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        return max(0.0, float(s))
    except ValueError:
        return None


@router.post("/categories/{cat_id}/delete")
def delete_category(cat_id: int, db: SheetDB = Depends(get_db)):
    cat = db.get_category(cat_id)
    if cat is None:
        raise HTTPException(status_code=404)
    db.update_category(cat_id, name=cat.name, is_active=False)
    return RedirectResponse(url="/categories", status_code=303)
