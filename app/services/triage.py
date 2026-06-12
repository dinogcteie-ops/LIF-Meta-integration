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
from datetime import date

from app.domain import Lead
from app.services import llm
from app.validators import normalize_phone

log = logging.getLogger("uvicorn.error")

TRIAGE_CLASSES = ("hot", "warm", "low_intent", "spam")

# Triage hinges on two signals: a real LOCATION (city/venue) and a usable EVENT
# DATE. The studio's rule of thumb:
#   • both present & plausible            → hot   (worth calling today)
#   • exactly one present, the other blank → warm  (genuine, needs a follow-up)
#   • neither present, but still a real    → low_intent (browsing / price-shopping)
#     human enquiry
#   • the location or date field holds a    → spam
#     name, gibberish, or junk
_SYSTEM = (
    "You triage incoming leads for an Indian wedding/event photography studio. "
    "Two fields decide the verdict: the event LOCATION (a real city, town, or "
    "venue) and the event DATE (a specific or clearly-described date — an exact "
    "day, or a month/season/year the client actually named). Classify each lead "
    "as exactly one of:\n"
    "  hot  — BOTH a real location AND a properly described event date are "
    "present and plausible.\n"
    "  warm — exactly ONE of location or date is present; the other is blank or "
    "too vague to act on.\n"
    "  low_intent — NEITHER location nor date is given, but the enquiry still "
    "reads like a genuine human (price-shopping, minimal info, or wants "
    "something the studio doesn't do).\n"
    "  spam — the location or date field (or the name) contains a person's name "
    "where a place should be, gibberish, random characters, bot/promotional "
    "text, or obviously fake contact details.\n"
    "A real location or date mentioned anywhere in the message counts even if "
    "the dedicated field is blank. A missing budget is normal — never spam on "
    "that alone. "
    'Reply with ONLY a JSON object: {"triage": "<class>", "reason": "<one short sentence>"}'
)


def _lead_prompt(lead: Lead) -> str:
    phone = normalize_phone(lead.contact)
    phone_note = ("valid 10-digit mobile" if len(phone) == 10
                  else ("missing" if not lead.contact.strip() else "malformed/short"))
    parts = [
        f"Today is {date.today().isoformat()} (judge whether the date is plausible).",
        f"Name: {lead.client_name or '(none)'}",
        f"Event type: {lead.event_type or '(not stated)'}",
        f"Event date: {lead.tentative_date.isoformat() if lead.tentative_date else '(not stated)'}",
        f"Location / City: {lead.city or '(not stated)'}",
        f"Budget: {lead.budget_range or '(not stated)'}",
        f"Source: {lead.source or '(unknown)'}",
        f"Phone check: {phone_note}",
        f"Message/notes: {(lead.notes or '(none)')[:500]}",
    ]
    return "Triage this lead:\n" + "\n".join(parts)


def triage_lead(db, lead: Lead) -> str | None:
    """Classify one lead and persist the verdict. Returns the class or None."""
    out = llm.complete_json(db, _lead_prompt(lead), system=_SYSTEM, max_tokens=400)
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
