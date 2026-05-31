from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.auth import verify_password
from app.templating import templates

router = APIRouter()


@router.get("/login")
def login_form(request: Request, error: str | None = None):
    if request.session.get("user"):
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": error})


@router.post("/login")
def login_submit(request: Request, password: str = Form(...)):
    if not verify_password(password):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Incorrect password."}, status_code=401
        )
    request.session["user"] = "owner"
    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
