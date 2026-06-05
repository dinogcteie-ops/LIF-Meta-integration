"""Minimal SMTP email sender (Gmail by default).

Stdlib only. Configured via the SMTP_* settings in app.config. Used for the
follow-up reminder digest; kept generic so other notifications can reuse it.
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from app.config import get_settings


class EmailError(Exception):
    pass


def email_configured() -> bool:
    s = get_settings()
    return bool(s.smtp_user and s.smtp_password)


def send_email(subject: str, html: str, recipients: list[str],
               text: str | None = None) -> None:
    """Send one HTML email to ``recipients``.

    Raises EmailError if SMTP credentials are missing or the send fails, so
    callers can surface a clear message instead of a silent no-op.
    """
    s = get_settings()
    if not email_configured():
        raise EmailError(
            "SMTP is not configured. Set SMTP_USER and SMTP_PASSWORD "
            "(a Gmail app password) in the environment."
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

    try:
        with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=30) as server:
            server.login(s.smtp_user, s.smtp_password)
            server.send_message(msg)
    except Exception as exc:  # noqa: BLE001 — surface any SMTP failure uniformly
        raise EmailError(f"Failed to send email: {exc}") from exc
