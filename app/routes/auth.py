import secrets
import time
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.auth import client_ip, verify_password
from app.config import get_settings
from app.database import get_db, SheetDB
from app.rbac import role_for_email
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


def _google_configured() -> bool:
    s = get_settings()
    return bool(s.google_client_id and s.google_client_secret)


def _google_redirect_uri(request: Request) -> str:
    """The OAuth callback URL. In prod, GOOGLE_REDIRECT_BASE pins it to the
    public domain (behind the Netlify proxy the request's own host would be the
    Render hostname); locally the request URL is used as-is."""
    base = (get_settings().google_redirect_base or str(request.base_url)).rstrip("/")
    return f"{base}/auth/google/callback"


@router.get("/login")
def login_form(request: Request, error: str | None = None):
    if request.session.get("user"):
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse(
        request, "login.html",
        {"error": error, "google_enabled": _google_configured()},
    )


@router.post("/login")
def login_submit(request: Request, password: str = Form(...)):
    ip = client_ip(request)
    remaining = _lockout_remaining(ip)
    if remaining:
        return templates.TemplateResponse(
            request, "login.html",
            {"error": f"Too many attempts. Try again in {remaining}s.",
             "google_enabled": _google_configured()},
            status_code=429,
        )
    if not verify_password(password):
        _record_failure(ip)
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Incorrect password.", "google_enabled": _google_configured()},
            status_code=401,
        )
    _clear_failures(ip)
    request.session["user"] = "owner"
    # RBAC: the password login is the owner. The future Google sign-in will set
    # this same key from an email->role mapping instead (see app/rbac.py).
    request.session["role"] = "owner"
    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ─── Google sign-in (OAuth 2.0 authorization-code flow) ─────────────────────
# Authentication only — authorization stays in app/rbac.py. The signed-in
# email is resolved to a role via the Settings-managed lists (role_owners
# etc.); an email on no list is rejected, so access is invitation-only.

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def _login_error(request: Request, message: str, status_code: int = 401):
    return templates.TemplateResponse(
        request, "login.html",
        {"error": message, "google_enabled": _google_configured()},
        status_code=status_code,
    )


@router.get("/auth/google")
def google_start(request: Request):
    if not _google_configured():
        raise HTTPException(status_code=404)
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    params = {
        "client_id": get_settings().google_client_id,
        "redirect_uri": _google_redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return RedirectResponse(url=f"{_GOOGLE_AUTH_URL}?{urlencode(params)}", status_code=303)


@router.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "",
                    error: str = "", db: SheetDB = Depends(get_db)):
    if not _google_configured():
        raise HTTPException(status_code=404)
    if error:
        return _login_error(request, "Google sign-in was cancelled.")
    expected_state = request.session.pop("oauth_state", None)
    if not state or not expected_state or not secrets.compare_digest(state, expected_state):
        return _login_error(request, "Sign-in session expired — please try again.")
    if not code:
        return _login_error(request, "Google sign-in failed — no code returned.")

    s = get_settings()
    try:
        token_resp = requests.post(_GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret,
            "redirect_uri": _google_redirect_uri(request),
            "grant_type": "authorization_code",
        }, timeout=15)
        access_token = token_resp.json().get("access_token", "")
        if not token_resp.ok or not access_token:
            return _login_error(request, "Google sign-in failed — could not verify the code.")
        info_resp = requests.get(
            _GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
        info = info_resp.json() if info_resp.ok else {}
    except requests.RequestException:
        return _login_error(request, "Could not reach Google — please try again.", 502)

    email = (info.get("email") or "").strip().lower()
    if not email or not info.get("email_verified", False):
        return _login_error(request, "Google account has no verified email.")

    role = role_for_email(email, db.get_settings_dict())
    if role is None:
        return _login_error(
            request, f"{email} is not authorized for this app. "
                     "Ask an owner to add you under Settings → Access & roles.", 403)

    request.session["user"] = email
    request.session["role"] = role.value
    return RedirectResponse(url="/dashboard", status_code=303)
