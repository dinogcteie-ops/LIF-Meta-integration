import bcrypt as _bcrypt
from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings

settings = get_settings()
_PUBLIC_PATHS = {"/login", "/logout", "/healthz", "/inquiry", "/meta/refresh"}
_PUBLIC_PREFIXES = ("/static/", "/portal/", "/webhooks/")

def verify_password(plain: str) -> bool:
    try:
        stored = settings.app_password
        h = _bcrypt.hashpw(stored.encode(), _bcrypt.gensalt())
        return _bcrypt.checkpw(plain.encode(), h)
    except Exception:
        return False


def is_logged_in(request: Request) -> bool:
    return bool(request.session.get("user"))


class LoginRequiredMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            path in _PUBLIC_PATHS
            or any(path.startswith(p) for p in _PUBLIC_PREFIXES)
        ):
            return await call_next(request)
        if not is_logged_in(request):
            return RedirectResponse(url="/login", status_code=303)
        return await call_next(request)
