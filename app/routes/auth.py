import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.auth import client_ip, verify_password
from app.templating import templates

router = APIRouter()

# ─── Login throttling ────────────────────────────────────────────────────────
# The app is gated by a single password, so unlimited guesses would make it
# brute-forceable. Track consecutive failures per client IP in memory (the app
# runs as one process on Render) and lock the IP out briefly after too many.

_MAX_FAILURES = 5
_LOCKOUT_SECONDS = 60
_failures: dict[str, dict] = {}   # ip -> {"count": int, "locked_until": float}


def _lockout_remaining(ip: str, now: float | None = None) -> int:
    now = now or time.time()
    entry = _failures.get(ip)
    if not entry:
        return 0
    return max(0, int(entry.get("locked_until", 0) - now))


def _record_failure(ip: str) -> None:
    now = time.time()
    entry = _failures.setdefault(ip, {"count": 0, "locked_until": 0.0})
    entry["count"] += 1
    if entry["count"] >= _MAX_FAILURES:
        entry["locked_until"] = now + _LOCKOUT_SECONDS
        entry["count"] = 0
    # Opportunistic pruning so the dict can't grow without bound.
    if len(_failures) > 1000:
        for k in [k for k, v in _failures.items()
                  if v.get("locked_until", 0) < now and v.get("count", 0) == 0]:
            _failures.pop(k, None)


def _clear_failures(ip: str) -> None:
    _failures.pop(ip, None)


def _reset_throttle() -> None:
    """Test hook — wipe all throttle state."""
    _failures.clear()


@router.get("/login")
def login_form(request: Request, error: str | None = None):
    if request.session.get("user"):
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": error})


@router.post("/login")
def login_submit(request: Request, password: str = Form(...)):
    ip = client_ip(request)
    remaining = _lockout_remaining(ip)
    if remaining:
        return templates.TemplateResponse(
            request, "login.html",
            {"error": f"Too many attempts. Try again in {remaining}s."},
            status_code=429,
        )
    if not verify_password(password):
        _record_failure(ip)
        return templates.TemplateResponse(
            request, "login.html", {"error": "Incorrect password."}, status_code=401
        )
    _clear_failures(ip)
    request.session["user"] = "owner"
    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
