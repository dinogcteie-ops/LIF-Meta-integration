"""Role-based access control (RBAC) scaffold.

Authentication and authorization are deliberately separated so the planned
Google-OAuth sign-in is a drop-in swap:

  * Today: the single password login stores ``session["role"] = "owner"``.
  * Later: Google sign-in resolves the user's email to a role (assignments kept
    in a DB table / Settings page) and stores that role in the same session key.

Everything downstream — route guards, template checks — only ever talks to this
module, so the auth swap touches login alone. Build new features against
``can(request, perm)`` / ``require(perm)`` from the start.

Roles:
  owner     — full access, including settings/audit/admin.
  manager   — day-to-day finance + directory + leads, but not admin.
  marketing — leads, Meta ads, and client directory only; NO finance visibility
              (no dashboard money, expenses, receivables/payables, reports).
  guest     — read-only: can view, can never create/edit/delete.

Permissions are coarse "area.verb" strings; keep them few and meaningful.
"""
from __future__ import annotations

from enum import Enum

from fastapi import HTTPException, Request


class Role(str, Enum):
    owner = "owner"
    manager = "manager"
    marketing = "marketing"
    guest = "guest"


# ─── Permission sets ─────────────────────────────────────────────────────────

_FINANCE_VIEW = "finance.view"
_FINANCE_EDIT = "finance.edit"
_LEADS_VIEW = "leads.view"
_LEADS_EDIT = "leads.edit"
_DIRECTORY_VIEW = "directory.view"
_DIRECTORY_EDIT = "directory.edit"
_ADMIN = "admin"

_ALL = {_FINANCE_VIEW, _FINANCE_EDIT, _LEADS_VIEW, _LEADS_EDIT,
        _DIRECTORY_VIEW, _DIRECTORY_EDIT, _ADMIN}

ROLE_PERMS: dict[Role, frozenset[str]] = {
    Role.owner:     frozenset(_ALL),
    Role.manager:   frozenset(_ALL - {_ADMIN}),
    Role.marketing: frozenset({_LEADS_VIEW, _LEADS_EDIT, _DIRECTORY_VIEW}),
    Role.guest:     frozenset({_FINANCE_VIEW, _LEADS_VIEW, _DIRECTORY_VIEW}),
}


# ─── Checks ──────────────────────────────────────────────────────────────────

def current_role(request: Request) -> Role:
    """Resolve the session's role. Pre-RBAC sessions (password login before the
    role key existed) are treated as owner; anything unrecognised is guest."""
    try:
        raw = request.session.get("role")
    except (AssertionError, AttributeError):   # no session middleware (tests)
        raw = None
    if raw is None:
        return Role.owner
    try:
        return Role(raw)
    except ValueError:
        return Role.guest


def has_perm(role: Role, perm: str) -> bool:
    return perm in ROLE_PERMS.get(role, frozenset())


def can(request: Request, perm: str) -> bool:
    """Template/route helper: does the current session's role hold ``perm``?

    Registered as a Jinja global, so templates can write
    ``{% if can(request, 'finance.edit') %}…{% endif %}``.
    """
    return has_perm(current_role(request), perm)


def require(perm: str):
    """FastAPI dependency: 403 unless the session's role holds ``perm``.

    Usage:  @router.post(..., dependencies=[Depends(require("finance.edit"))])
    """
    def _dep(request: Request) -> None:
        if not can(request, perm):
            raise HTTPException(status_code=403, detail=f"Requires {perm}")
    return _dep


# ─── Email → role mapping (Google sign-in) ──────────────────────────────────

# Settings keys holding comma-separated email lists, checked in this order so
# a mis-listed address resolves to its MOST privileged assignment.
_ROLE_SETTING_KEYS: list[tuple[str, Role]] = [
    ("role_owners",    Role.owner),
    ("role_managers",  Role.manager),
    ("role_marketing", Role.marketing),
    ("role_guests",    Role.guest),
]


def role_for_email(email: str | None, settings_dict: dict) -> Role | None:
    """Resolve a Google-account email to its assigned role.

    Case-insensitive. Returns None when the address appears in no list — the
    caller must reject the sign-in (access is invitation-only by design).
    """
    if not email:
        return None
    needle = email.strip().lower()
    if not needle:
        return None
    for key, role in _ROLE_SETTING_KEYS:
        emails = {e.strip().lower() for e in (settings_dict.get(key) or "").split(",")}
        if needle in emails:
            return role
    return None
