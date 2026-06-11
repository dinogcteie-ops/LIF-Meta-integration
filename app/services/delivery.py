"""Delivery workflow helpers — phase templates + status mapping.

Phase templates are keyed by event_type (lower-case). The fallback ``default``
list is used for all types not listed explicitly.

Status mapping (highest completed phase wins):
  Shoot         → shooting_done
  Culling       → editing
  Editing       → editing
  Client review → review
  Album design  → review
  Final delivery → delivered
  (all complete) → delivered
  (none complete / no milestones) → None (caller decides)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

# ─── Phase templates ──────────────────────────────────────────────────────────

_DEFAULT_PHASES = [
    "Shoot",
    "Culling",
    "Editing",
    "Client review",
    "Album design",
    "Final delivery",
]

_SHORT_PHASES = [
    "Shoot",
    "Culling",
    "Editing",
    "Final delivery",
]

PHASE_TEMPLATES: dict[str, list[str]] = {
    "default":    _DEFAULT_PHASES,
    "wedding":    _DEFAULT_PHASES,
    "engagement": _DEFAULT_PHASES,
    "reception":  _DEFAULT_PHASES,
    "maternity":  _DEFAULT_PHASES,
    "portrait":   _SHORT_PHASES,
    "corporate":  _SHORT_PHASES,
    "other":      _DEFAULT_PHASES,
}


def phases_for_event_type(event_type: Optional[str]) -> list[str]:
    """Return the ordered phase list for an event type string."""
    key = (event_type or "default").lower()
    return PHASE_TEMPLATES.get(key, _DEFAULT_PHASES)


# ─── Status mapping ──────────────────────────────────────────────────────────

# Map phase name → delivery_status value it contributes when completed.
# The *highest* completed phase (by position) wins.
_PHASE_TO_STATUS: dict[str, str] = {
    "shoot":           "shooting_done",
    "culling":         "editing",
    "editing":         "editing",
    "client review":   "review",
    "album design":    "review",
    "final delivery":  "delivered",
}


def delivery_status_from_milestones(milestones: list) -> Optional[str]:
    """Derive the delivery_status value from a list of Milestone objects.

    Rules (spec-exact):
    - No milestones at all → return None (caller must not write)
    - All milestones complete → "delivered"
    - Otherwise: highest completed phase mapped through ``_PHASE_TO_STATUS``
    - No phase completed → None
    """
    if not milestones:
        return None

    # If every milestone is complete, return delivered.
    if all(m.completed_at is not None for m in milestones):
        return "delivered"

    # Walk milestones in reverse position order; first completed one wins.
    sorted_ms = sorted(milestones, key=lambda m: m.position, reverse=True)
    for m in sorted_ms:
        if m.completed_at is not None:
            key = m.phase.lower().strip()
            if key in _PHASE_TO_STATUS:
                return _PHASE_TO_STATUS[key]

    return None


# ─── Dashboard card helper ────────────────────────────────────────────────────

@dataclass
class DeliveryCard:
    """Aggregated data for one project card on the /delivery dashboard."""
    event_id: int
    event_name: str
    client_name: Optional[str]
    event_date: Optional[date]
    event_type: Optional[str]
    delivery_status: Optional[str]
    total_milestones: int
    completed_milestones: int
    current_phase: Optional[str]        # highest completed phase label
    next_due: Optional[object]          # next incomplete milestone with a due date
    overdue: list = field(default_factory=list)  # past-due incomplete milestones
    pending_from_client: float = 0.0

    @property
    def progress_pct(self) -> int:
        if self.total_milestones == 0:
            return 0
        return round(self.completed_milestones / self.total_milestones * 100)


def build_delivery_card(
    event_id: int,
    event_name: str,
    client_name: Optional[str],
    event_date: Optional[date],
    event_type: Optional[str],
    delivery_status: Optional[str],
    milestones: list,
    pending_from_client: float = 0.0,
    today: Optional[date] = None,
) -> DeliveryCard:
    today = today or date.today()
    completed = [m for m in milestones if m.completed_at is not None]
    incomplete = [m for m in milestones if m.completed_at is None]

    # Highest completed phase label
    current_phase: Optional[str] = None
    if completed:
        highest = max(completed, key=lambda m: m.position)
        current_phase = highest.phase

    # Next due: earliest incomplete milestone that has a due_date
    next_due = None
    with_due = [m for m in incomplete if m.due_date is not None]
    if with_due:
        next_due = min(with_due, key=lambda m: m.due_date)

    # Overdue: past-due and not complete
    overdue = [
        m for m in incomplete
        if m.due_date is not None and m.due_date < today
    ]

    return DeliveryCard(
        event_id=event_id,
        event_name=event_name,
        client_name=client_name,
        event_date=event_date,
        event_type=event_type,
        delivery_status=delivery_status,
        total_milestones=len(milestones),
        completed_milestones=len(completed),
        current_phase=current_phase,
        next_due=next_due,
        overdue=overdue,
        pending_from_client=pending_from_client,
    )
