"""One-time helper: mint a Gmail API refresh token for sending app email.

Render blocks outbound SMTP, so the app sends via the Gmail API over HTTPS
(see app/services/email.py). That needs a long-lived refresh token with the
``gmail.send`` scope for the sending account (e.g. dinogcteie@gmail.com).

Prerequisites (one-time, in Google Cloud Console for the project that owns your
existing GOOGLE_CLIENT_ID):
  1. APIs & Services → Library → enable **Gmail API**.
  2. OAuth consent screen: under "Data access" add the scope
     ``https://www.googleapis.com/auth/gmail.send``. If the app is in "Testing"
     mode, make sure the sending Gmail address is listed as a Test user.
  3. The OAuth client already has the redirect URI used here
     (http://localhost:8000/auth/google/callback) registered — no change needed.

Run (from the repo root, with the root .env + venv present, app NOT running so
port 8000 is free):

    python scripts/get_gmail_refresh_token.py

A browser opens; sign in as the SENDING account and approve. The script prints a
refresh token. Set it on Render as **GMAIL_REFRESH_TOKEN** (GOOGLE_CLIENT_ID /
GOOGLE_CLIENT_SECRET are already there). Done.
"""
from __future__ import annotations

import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs

import requests

from app.config import get_settings

REDIRECT_URI = "http://localhost:8000/auth/google/callback"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/gmail.send"

_received: dict = {}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        qs = parse_qs(urlparse(self.path).query)
        _received["code"] = (qs.get("code") or [""])[0]
        _received["error"] = (qs.get("error") or [""])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = ("Authorization received — you can close this tab and return to the terminal."
               if _received["code"] else f"Authorization failed: {_received['error']}")
        self.wfile.write(f"<html><body><h3>{msg}</h3></body></html>".encode())

    def log_message(self, *args):  # silence the default request logging
        pass


def main() -> int:
    s = get_settings()
    if not (s.google_client_id and s.google_client_secret):
        print("ERROR: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set in .env.",
              file=sys.stderr)
        return 1

    params = {
        "client_id": s.google_client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",      # ask for a refresh token
        "prompt": "consent",           # force a fresh refresh token every run
    }
    auth_url = f"{AUTH_URL}?{urlencode(params)}"

    try:
        server = HTTPServer(("localhost", 8000), _Handler)
    except OSError as exc:
        print(f"ERROR: could not bind localhost:8000 ({exc}). "
              "Stop the dev server (uvicorn) first, then re-run.", file=sys.stderr)
        return 1

    print("\nOpening browser for Google consent — sign in as the SENDING account.")
    print(f"If it doesn't open, paste this URL:\n\n{auth_url}\n")
    threading.Thread(target=lambda: webbrowser.open(auth_url), daemon=True).start()
    server.handle_request()   # serve exactly one request (the callback)
    server.server_close()

    if _received.get("error") or not _received.get("code"):
        print(f"ERROR: authorization failed: {_received.get('error') or 'no code returned'}",
              file=sys.stderr)
        return 1

    resp = requests.post(TOKEN_URL, data={
        "code": _received["code"],
        "client_id": s.google_client_id,
        "client_secret": s.google_client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }, timeout=20)
    data = resp.json() if resp.content else {}
    refresh = data.get("refresh_token", "")
    if not resp.ok or not refresh:
        print(f"ERROR: token exchange failed ({resp.status_code}): "
              f"{data.get('error_description') or data.get('error') or resp.text[:300]}",
              file=sys.stderr)
        if resp.ok and not refresh:
            print("(No refresh_token returned — revoke prior access at "
                  "https://myaccount.google.com/permissions and re-run, or ensure "
                  "prompt=consent + access_type=offline.)", file=sys.stderr)
        return 1

    print("\n" + "=" * 64)
    print("SUCCESS. Set this on Render as GMAIL_REFRESH_TOKEN:\n")
    print(refresh)
    print("=" * 64 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
