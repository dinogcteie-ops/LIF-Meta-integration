"""Server-side input validation helpers.

Routes are plain ``Form(...)`` handlers that redirect with a session flash on
problems, so these are pure functions returning ``(value, error)`` tuples —
no Pydantic models, no exceptions for user mistakes. ``error`` is a
human-readable string ready for the flash banner, or ``None`` when the value
is acceptable.

``normalize_phone`` is the single source of truth for phone comparison across
the app (lead intake dedup, duplicate warnings, WhatsApp links): Indian
numbers collapse to their last 10 digits so "+91 98765 43210", "p:+919876543210"
and "98765-43210" all match.
"""
from __future__ import annotations

import math
import re
from datetime import date, timedelta
from enum import Enum
from typing import Optional, Type

# ─── Phone / email ────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def normalize_phone(raw: str) -> str:
    """Strip Meta's 'p:' prefix and all non-digits; return the last 10 digits.

    Returns whatever digits remain (possibly "") when fewer than 10 — callers
    treat short results as "not comparable", never as a match key.
    """
    cleaned = re.sub(r"^p:", "", (raw or "").strip(), flags=re.IGNORECASE)
    digits = re.sub(r"\D", "", cleaned)
    return digits[-10:] if len(digits) >= 10 else digits


def valid_email(raw: str) -> bool:
    """Cheap shape check — one @, a dot in the domain. Empty is not valid."""
    return bool(_EMAIL_RE.match((raw or "").strip()))


# ─── Amounts ──────────────────────────────────────────────────────────────────

def parse_amount(value: float, label: str, minimum: float = 0.0,
                 maximum: float = 100_000.0) -> tuple[float, Optional[str]]:
    """Validate a ₹-lakh amount. Rejects negatives, NaN/inf and absurd values.

    The 1-lakh-crore default ceiling (₹1L × 100,000) is purely a fat-finger
    guard — no studio quote or expense legitimately reaches it.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0, f"{label} must be a number."
    if math.isnan(v) or math.isinf(v):
        return 0.0, f"{label} must be a number."
    if v < minimum:
        return 0.0, f"{label} cannot be negative."
    if v > maximum:
        return 0.0, f"{label} looks too large — amounts are in ₹ lakhs."
    return round(v, 4), None


# ─── Dates ────────────────────────────────────────────────────────────────────

def parse_date_safe(value: str, label: str = "Date",
                    years_back: int = 30, years_ahead: int = 20
                    ) -> tuple[Optional[date], Optional[str]]:
    """Parse an ISO date from a form field. Empty → (None, None).

    Malformed input is an error, never a 500; dates outside a sane window
    (default 30y back / 20y ahead) are rejected as typos (e.g. year 0203).
    """
    value = (value or "").strip()
    if not value:
        return None, None
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return None, f"{label} is not a valid date."
    today = date.today()
    if d < today - timedelta(days=365 * years_back):
        return None, f"{label} is too far in the past — check the year."
    if d > today + timedelta(days=365 * years_ahead):
        return None, f"{label} is too far in the future — check the year."
    return d, None


# ─── Enums ────────────────────────────────────────────────────────────────────

def parse_enum(enum_cls: Type[Enum], raw: str, label: str,
               default: Optional[Enum] = None
               ) -> tuple[Optional[Enum], Optional[str]]:
    """Coerce a form string to an enum member without raising.

    Empty input falls back to ``default`` (which may be None). Unknown values
    return an error instead of the ValueError-turned-500 the routes had before.
    """
    raw = (raw or "").strip()
    if not raw:
        return default, None
    try:
        return enum_cls(raw), None
    except ValueError:
        return default, f"Invalid {label}: '{raw}'."


# ─── Duplicate lookup helpers ─────────────────────────────────────────────────

def find_phone_match(phone: str, candidates) -> Optional[object]:
    """Return the first object in ``candidates`` whose phone-ish field matches
    ``phone`` after normalization. Candidates expose ``.contact`` (leads) or
    ``.phone`` (clients). Comparison keys shorter than 10 digits never match.
    """
    key = normalize_phone(phone)
    if len(key) < 10:
        return None
    for c in candidates:
        other = getattr(c, "contact", None) or getattr(c, "phone", None) or ""
        if normalize_phone(other) == key:
            return c
    return None
