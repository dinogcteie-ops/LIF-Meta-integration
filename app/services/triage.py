"""AI lead triage — classify incoming leads as hot / warm / low_intent / spam.

Runs from the every-5-minute lead-intake cron (`/jobs/import-leads`), so leads
from every channel (Sheet intake, Meta webhook, website inquiry, manual entry)
get a verdict within minutes without adding latency or an LLM dependency to
any request path. Best-effort by design: an LLM failure leaves the lead
untriaged and the next cron tick retries it.

Every verdict (plus the owner's manual overrides, ``triage_source="manual"``)
is a labeled example for the planned in-app ML model — the LLM is the
cold-start classifier, not the permanent one.
"""
from __future__ import annotations

import logging

from app.domain import Lead
from app.services import llm
from app.validators import normalize_phone

log = logging.getLogger("uvicorn.error")

TRIAGE_CLASSES = ("hot", "warm", "low_intent", "spam")

_SYSTEM = (
    "You triage incoming leads for an Indian wedding/event photography studio. "
    "Classify each lead as exactly one of: hot (clear event, near-term date or "
    "stated budget, reachable contact — worth calling today), warm (genuine "
    "interest but vague on date/budget), low_intent (price-shopping, no date, "
    "minimal info, or wants something the studio doesn't do), spam (gibberish, "
    "promotional/bot text, fake contact details). "
    "Budget context: amounts are in Indian rupees; serious wedding budgets "
    "start around ₹50,000+. A missing budget is normal, not spam. "
    'Reply with ONLY a JSON object: {"triage": "<class>", "reason": "<one short sentence>"}'
)


def _lead_prompt(lead: Lead) -> str:
    phone = normalize_phone(lead.contact)
    phone_note = ("valid 10-digit mobile" if len(phone) == 10
                  else ("missing" if not lead.contact.strip() else "malformed/short"))
    parts = [
        f"Name: {lead.client_name or '(none)'}",
        f"Event type: {lead.event_type or '(not stated)'}",
        f"Tentative date: {lead.tentative_date.isoformat() if lead.tentative_date else '(not stated)'}",
        f"Budget: {lead.budget_range or '(not stated)'}",
        f"City: {lead.city or '(not stated)'}",
        f"Source: {lead.source or '(unknown)'}",
        f"Phone check: {phone_note}",
        f"Message/notes: {(lead.notes or '(none)')[:500]}",
    ]
    return "Triage this lead:\n" + "\n".join(parts)


def triage_lead(db, lead: Lead) -> str | None:
    """Classify one lead and persist the verdict. Returns the class or None."""
    out = llm.complete_json(db, _lead_prompt(lead), system=_SYSTEM, max_tokens=200)
    verdict = str(out.get("triage", "")).strip().lower()
    if verdict not in TRIAGE_CLASSES:
        raise llm.LLMError(f"Unknown triage class from LLM: {verdict!r}")
    reason = str(out.get("reason", ""))[:300]
    db.set_lead_triage(lead.id, verdict, "llm", reason)
    return verdict


def triage_pending_leads(db, limit: int = 20) -> dict:
    """Classify up to ``limit`` untriaged leads. Never raises.

    Returns {"triaged": n, "failed": n, "skipped": reason?} for the job log.
    """
    if not llm.is_configured():
        return {"triaged": 0, "failed": 0, "skipped": "llm not configured"}
    pending = db.list_untriaged_leads(limit=limit)
    triaged = failed = 0
    for lead in pending:
        try:
            triage_lead(db, lead)
            triaged += 1
        except llm.LLMError as e:
            failed += 1
            log.warning("lead triage failed for lead #%s: %s", lead.id, e)
            # Budget exhausted / provider down — stop burning the rest of the
            # batch; the next cron tick retries.
            break
        except Exception:                                  # noqa: BLE001
            failed += 1
            log.exception("unexpected triage error for lead #%s", lead.id)
    return {"triaged": triaged, "failed": failed, "pending": len(pending)}
