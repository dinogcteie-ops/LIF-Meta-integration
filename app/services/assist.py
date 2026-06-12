"""LLM assists — reply drafts and lead briefs. Human always in the loop.

Both entry points are triggered by explicit button clicks (never cron, never
page render), so LLM latency is acceptable and each call is a deliberate
spend against the daily budget in :mod:`app.services.llm`. Errors surface as
flash messages; nothing here ever writes to a lead.
"""
from __future__ import annotations

from app.domain import CommLog, Lead
from app.services import llm

_REPLY_SYSTEM = (
    "You write the FIRST reply to an enquiry for an Indian wedding/event "
    "photography studio, to be sent over WhatsApp. Warm, professional, concise "
    "(under 120 words). Structure: greet by name if known; acknowledge their "
    "specific event/date/city; one line on the studio's fit; ask one or two "
    "qualifying questions (date confirmed? venue/city? budget range if not "
    "stated); close with a low-pressure next step (quick call). NEVER invent "
    "prices, packages or availability. No emojis beyond at most one. "
    "Sign off with the studio name. Reply with the message text only."
)

_SUMMARY_SYSTEM = (
    "You summarize a sales lead's history for a busy studio owner. Exactly "
    "three short lines: (1) who/what they want, (2) where the conversation "
    "stands, (3) the next action. Plain text, no headings, no invented facts."
)


def draft_reply(db, lead: Lead, studio_name: str) -> str:
    """An editable first-reply draft for the given lead. Raises LLMError."""
    parts = [
        f"Studio name: {studio_name}",
        f"Lead name: {lead.client_name or '(unknown)'}",
        f"Event type: {lead.event_type or '(not stated)'}",
        f"Tentative date: {lead.tentative_date.isoformat() if lead.tentative_date else '(not stated)'}",
        f"City: {lead.city or '(not stated)'}",
        f"Stated budget: {lead.budget_range or '(not stated)'}",
        f"Their message/notes: {(lead.notes or '(none)')[:400]}",
    ]
    return llm.complete(db, "Write the reply.\n" + "\n".join(parts),
                        system=_REPLY_SYSTEM, max_tokens=300).strip()


def summarize_lead(db, lead: Lead, comm_logs: list[CommLog]) -> str:
    """Three-line brief from the lead's follow-ups + communication log."""
    touches = "\n".join(
        f"- {c.created_at[:16]} {c.direction} via {c.channel}: {c.summary[:160]}"
        for c in comm_logs[:15]
    ) or "(no logged touches)"
    parts = [
        f"Lead: {lead.client_name or '(unknown)'} — {lead.event_type or '?'} "
        f"— status {lead.status} — quote ₹{lead.quoted_amount}L",
        f"Tentative date: {lead.tentative_date or '?'} | City: {lead.city or '?'} "
        f"| Budget: {lead.budget_range or '?'}",
        f"Notes: {(lead.notes or '')[:300]}",
        f"Follow-up notes: {(lead.follow_ups or '')[:300]}",
        f"Touch history:\n{touches}",
    ]
    return llm.complete(db, "Summarize this lead.\n" + "\n".join(parts),
                        system=_SUMMARY_SYSTEM, max_tokens=200).strip()
