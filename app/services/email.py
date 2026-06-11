"""Email sender with two transports.

Render's free tier blocks outbound SMTP (connections to smtp.gmail.com:465 fail
with "Network is unreachable"), so production sends via the **Gmail API over
HTTPS** (port 443, which is allowed). Locally — where SMTP works fine — it falls
back to SMTP, so no refresh token is needed for dev.

Both transports build the same RFC-822 message; only the final dispatch differs:
- Gmail API: base64url the message and POST it to gmail.googleapis.com, authed
  with a short-lived access token refreshed from GMAIL_REFRESH_TOKEN.
- SMTP: the original smtplib SSL send.

send_email_with_images() adds inline CID-attached PNGs (the Instagram report).
"""
from __future__ import annotations

import base64
import smtplib
import time
from email.message import EmailMessage
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

import requests

from app.config import get_settings

_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

# Cached Gmail access token (the Render process is long-lived/cron-warmed, so a
# module-level cache avoids a token refresh on every send).
_token_cache: dict = {"token": "", "exp": 0.0}


class EmailError(Exception):
    pass


def _gmail_api_configured() -> bool:
    s = get_settings()
    return bool(s.gmail_refresh_token and s.google_client_id and s.google_client_secret)


def email_configured() -> bool:
    """True if either transport can send (Gmail API in prod, SMTP locally)."""
    s = get_settings()
    return _gmail_api_configured() or bool(s.smtp_user and s.smtp_password)


# ─── Gmail API transport (HTTPS) ──────────────────────────────────────────────

def _gmail_access_token() -> str:
    """Exchange the refresh token for a short-lived access token (cached)."""
    if _token_cache["token"] and _token_cache["exp"] > time.time() + 60:
        return _token_cache["token"]
    s = get_settings()
    try:
        resp = requests.post(_OAUTH_TOKEN_URL, data={
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret,
            "refresh_token": s.gmail_refresh_token,
            "grant_type": "refresh_token",
        }, timeout=15)
    except requests.RequestException as exc:
        raise EmailError(f"Could not reach Google token endpoint: {exc}") from exc
    data = resp.json() if resp.content else {}
    token = data.get("access_token", "")
    if not resp.ok or not token:
        detail = data.get("error_description") or data.get("error") or resp.text[:200]
        raise EmailError(f"Gmail token refresh failed ({resp.status_code}): {detail}")
    _token_cache["token"] = token
    _token_cache["exp"] = time.time() + int(data.get("expires_in", 3600))
    return token


def _send_via_gmail_api(msg) -> None:
    token = _gmail_access_token()
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        resp = requests.post(
            _GMAIL_SEND_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"raw": raw}, timeout=30,
        )
    except requests.RequestException as exc:
        raise EmailError(f"Could not reach Gmail API: {exc}") from exc
    if not resp.ok:
        raise EmailError(f"Gmail API send failed ({resp.status_code}): {resp.text[:300]}")


# ─── SMTP transport (local dev fallback) ──────────────────────────────────────

def _send_via_smtp(msg, timeout: int = 30) -> None:
    s = get_settings()
    try:
        with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=timeout) as server:
            server.login(s.smtp_user, s.smtp_password)
            server.send_message(msg)
    except Exception as exc:  # noqa: BLE001 — surface any SMTP failure uniformly
        raise EmailError(f"Failed to send email: {exc}") from exc


def _dispatch(msg) -> None:
    """Send via the Gmail API if configured (prod), else SMTP (local dev)."""
    if _gmail_api_configured():
        _send_via_gmail_api(msg)
    else:
        _send_via_smtp(msg)


# ─── Public API ───────────────────────────────────────────────────────────────

def send_email(subject: str, html: str, recipients: list[str],
               text: str | None = None) -> None:
    """Send one HTML email to ``recipients``.

    Raises EmailError if no transport is configured or the send fails, so
    callers can surface a clear message instead of a silent no-op.
    """
    s = get_settings()
    if not email_configured():
        raise EmailError(
            "Email is not configured. Set GMAIL_REFRESH_TOKEN (+ GOOGLE_CLIENT_ID/"
            "SECRET) for the Gmail API, or SMTP_USER/SMTP_PASSWORD for SMTP."
        )
    clean = [r.strip() for r in recipients if r and r.strip()]
    if not clean:
        raise EmailError("No recipients provided.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("Life in Frame CRM", s.email_from))
    msg["To"] = ", ".join(clean)
    msg.set_content(text or "This message requires an HTML-capable email client.")
    msg.add_alternative(html, subtype="html")

    _dispatch(msg)


def send_email_with_images(
    subject: str,
    html: str,
    images: dict[str, bytes],
    recipients: list[str],
    text: str | None = None,
) -> None:
    """Send an HTML email with inline PNG images embedded via CID references.

    ``images`` maps CID name → PNG bytes.  Reference them in HTML as
    ``<img src="cid:my_chart">``.  Raises EmailError on any failure.

    MIME structure:
      multipart/mixed
        multipart/alternative
          text/plain
          multipart/related
            text/html  (references cid:… images)
            image/png  Content-ID: <cid>
            …
    """
    s = get_settings()
    if not email_configured():
        raise EmailError(
            "Email is not configured. Set GMAIL_REFRESH_TOKEN (+ GOOGLE_CLIENT_ID/"
            "SECRET) for the Gmail API, or SMTP_USER/SMTP_PASSWORD for SMTP."
        )
    clean = [r.strip() for r in recipients if r and r.strip()]
    if not clean:
        raise EmailError("No recipients provided.")

    # Build multipart/related: HTML body + inline images
    related = MIMEMultipart("related")
    related.attach(MIMEText(html, "html", "utf-8"))
    for cid, png_bytes in images.items():
        img = MIMEImage(png_bytes, "png")
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline", filename=f"{cid}.png")
        related.attach(img)

    # Wrap in multipart/alternative so plain-text clients get a fallback
    alternative = MIMEMultipart("alternative")
    alternative.attach(
        MIMEText(text or "This email requires an HTML-capable client.", "plain", "utf-8")
    )
    alternative.attach(related)

    # Outer envelope
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = formataddr(("Life in Frame CRM", s.email_from))
    msg["To"]      = ", ".join(clean)
    msg.attach(alternative)

    _dispatch(msg)
