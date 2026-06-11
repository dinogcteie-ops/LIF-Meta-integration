"""Minimal SMTP email sender (Gmail by default).

Stdlib only. Configured via the SMTP_* settings in app.config. Used for the
follow-up reminder digest; kept generic so other notifications can reuse it.
send_email_with_images() adds support for inline CID-attached PNG images
(used by the Instagram lead report).
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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
            "SMTP is not configured. Set SMTP_USER and SMTP_PASSWORD "
            "(a Gmail app password) in the environment."
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

    try:
        with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=60) as server:
            server.login(s.smtp_user, s.smtp_password)
            server.send_message(msg)
    except Exception as exc:  # noqa: BLE001
        raise EmailError(f"Failed to send email: {exc}") from exc
