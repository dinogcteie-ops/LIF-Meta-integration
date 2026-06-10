"""WhatsApp deep-link helpers (free wa.me links — no Business API).

A ``https://wa.me/<number>?text=...`` URL opens WhatsApp with the chat and
message pre-filled; the owner just hits send. This is the zero-cost first rung
of client payment reminders — if volume ever justifies it, the WhatsApp
Business API slots in behind the same call sites for *automated* sends.
"""
from __future__ import annotations

import re
from urllib.parse import quote

_DIGITS = re.compile(r"\D+")


def wa_number(phone: str | None) -> str | None:
    """Normalize a free-text phone to wa.me digit format (country code, no '+').

    Indian-first rules: bare 10-digit numbers get the 91 prefix; a leading 0 is
    a domestic trunk prefix and is replaced by 91. Anything already carrying a
    country code passes through. Too short to be real -> None.
    """
    if not phone:
        return None
    digits = _DIGITS.sub("", phone)
    if len(digits) == 10:
        return "91" + digits
    if len(digits) == 11 and digits.startswith("0"):
        return "91" + digits[1:]
    if len(digits) >= 11:
        return digits
    return None


def wa_link(phone: str | None, text: str) -> str | None:
    """Full wa.me URL with the message pre-filled, or None if no usable phone."""
    num = wa_number(phone)
    if not num:
        return None
    return f"https://wa.me/{num}?text={quote(text)}"


def payment_reminder_text(studio_name: str, client_name: str | None,
                          event_name: str, pending_display: str) -> str:
    """A polite, ready-to-send payment reminder message."""
    greeting = f"Hi {client_name}," if client_name else "Hi,"
    return (
        f"{greeting}\n\n"
        f"Greetings from {studio_name}! A gentle reminder that a balance of "
        f"{pending_display} is pending for \"{event_name}\".\n\n"
        f"Please let us know if you have any questions. Thank you!"
    )
