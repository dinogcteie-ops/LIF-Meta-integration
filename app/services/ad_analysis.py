"""LLM ad-performance & financials analysis — 15-day comparison.

Feeds the bi-monthly Instagram lead report (1st & 16th, already a 15-day
cadence) with an AI-written narrative on top of the existing rule-based
insights. Strictly aggregate-driven: prompts contain campaign sums, funnel
counts, triage mix and financial totals — never a client name, phone or any
other PII.

Fail-soft contract: ``analysis_or_fallback`` returns None on ANY problem
(no API key, over budget, provider down, weird output) and callers keep the
rule-based ``derive_insights`` content — the report email must render
byte-identical to today's when the LLM is unavailable.

The latest narrative is cached in Settings (``ad_analysis_latest`` as JSON)
so the /meta page can show it without spending an LLM call per page view.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime

from app.services import llm
from app.services.reports import bank_summary, filter_leads, monthly_history

log = logging.getLogger("uvicorn.error")

LATEST_KEY = "ad_analysis_latest"

_SYSTEM = (
    "You are a sharp, numbers-first marketing analyst writing for the owner of "
    "an Indian wedding-photography studio. You receive aggregate metrics for "
    "the current 15-day window vs the previous one. Write a tight analysis "
    "(<300 words, short paragraphs and dashes, no headings): (1) what improved "
    "or declined and why it matters; (2) per-campaign cost-per-lead trend and "
    "one budget reallocation suggestion; (3) a lead-quality note from the "
    "triage mix; (4) one financial-health observation; (5) exactly three "
    "concrete actions, numbered. Ad spend figures are INR actuals; CRM revenue/"
    "expense figures are in ₹ lakhs (1 lakh = 100,000). Never invent numbers "
    "that are not in the data."
)


# ─── Aggregation ──────────────────────────────────────────────────────────────

def _window_campaigns(metrics, start: date, end: date) -> dict[str, dict]:
    """Per-campaign sums for metric rows whose date falls in [start, end]."""
    out: dict[str, dict] = {}
    for m in metrics:
        if m.date is None or not (start <= m.date <= end):
            continue
        row = out.setdefault(m.campaign_name or m.campaign_id, {
            "spend": 0.0, "impressions": 0, "clicks": 0, "leads": 0,
        })
        row["spend"] += m.spend or 0.0
        row["impressions"] += m.impressions or 0
        row["clicks"] += m.clicks or 0
        row["leads"] += m.leads or 0
    for row in out.values():
        row["spend"] = round(row["spend"], 2)
        row["cpl"] = round(row["spend"] / row["leads"], 2) if row["leads"] else 0.0
    return out


def _funnel_counts(leads) -> dict[str, int]:
    counts = {"total": len(leads)}
    for status in ("new", "quoted", "won", "lost", "cold"):
        counts[status] = sum(1 for l in leads if l.status == status)
    return counts


def _triage_mix(leads) -> dict[str, int]:
    mix: dict[str, int] = {}
    for l in leads:
        key = l.triage or "untriaged"
        mix[key] = mix.get(key, 0) + 1
    return mix


def gather_ad_aggregates(db, period_start: date, period_end: date,
                         prev_start: date, prev_end: date) -> dict:
    """Aggregates only — safe to put in a prompt verbatim."""
    metrics = db.list_meta_metrics()
    all_leads = db.list_leads()
    leads_curr = filter_leads(all_leads, start=period_start, end=period_end)
    leads_prev = filter_leads(all_leads, start=prev_start, end=prev_end)
    bank = bank_summary(db)
    months = monthly_history(db, months=3)

    return {
        "window_current": f"{period_start.isoformat()}..{period_end.isoformat()}",
        "window_previous": f"{prev_start.isoformat()}..{prev_end.isoformat()}",
        "campaigns_current": _window_campaigns(metrics, period_start, period_end),
        "campaigns_previous": _window_campaigns(metrics, prev_start, prev_end),
        "funnel_current": _funnel_counts(leads_curr),
        "funnel_previous": _funnel_counts(leads_prev),
        "triage_mix_current": _triage_mix(leads_curr),
        "financials_lakhs": {
            "total_income": round(bank.total_income, 2),
            "total_paid_expense": round(bank.total_paid_expense, 2),
            "pending_from_clients": round(bank.total_pending_from_clients, 2),
            "outstanding_payables": round(bank.outstanding_payable, 2),
            "recent_months": [
                {"period": r.period, "income": round(r.income, 2),
                 "expense": round(r.expense, 2), "profit": r.profit}
                for r in months
            ],
        },
    }


# ─── Narrative ────────────────────────────────────────────────────────────────

def build_llm_narrative(db, aggregates: dict,
                        label_curr: str, label_prev: str) -> str:
    prompt = (
        f"Current window: {label_curr}. Previous window: {label_prev}.\n"
        f"Data (JSON):\n{json.dumps(aggregates, indent=1, default=str)}"
    )
    return llm.complete(db, prompt, system=_SYSTEM, max_tokens=700).strip()


def analysis_or_fallback(db, period_start: date, period_end: date,
                         prev_start: date, prev_end: date,
                         label_curr: str, label_prev: str) -> str | None:
    """The narrative, or None when the LLM can't deliver. Caches the latest."""
    if not llm.is_configured():
        return None
    try:
        aggregates = gather_ad_aggregates(db, period_start, period_end,
                                          prev_start, prev_end)
        text = build_llm_narrative(db, aggregates, label_curr, label_prev)
        if not text:
            return None
        db.set_settings({LATEST_KEY: json.dumps({
            "generated_at": datetime.now().isoformat(timespec="minutes"),
            "period": label_curr,
            "text": text,
        }, ensure_ascii=False)})
        return text
    except llm.LLMError as e:
        log.warning("ad analysis skipped (LLM): %s", e)
        return None
    except Exception:                                       # noqa: BLE001
        log.exception("ad analysis failed unexpectedly — using rule-based insights")
        return None


def latest_analysis(db) -> dict | None:
    """The last stored narrative for the /meta page, or None."""
    raw = db.get_settings_dict().get(LATEST_KEY) or ""
    if not raw:
        return None
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) and out.get("text") else None
    except ValueError:
        return None
