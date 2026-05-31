"""Meta (Facebook/Instagram) Graph API client.

Two responsibilities:
  1. Lead Ads — verify webhook signatures and retrieve submitted lead form data.
  2. Insights — pull campaign-level ad metrics (spend, impressions, leads, CPL).

All calls are best-effort and degrade gracefully when credentials are missing,
so the app still boots without Meta configured.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import date, datetime
from typing import Optional

import requests

from app.config import get_settings

log = logging.getLogger(__name__)

_GRAPH = "https://graph.facebook.com"
_TIMEOUT = 20


def _base() -> str:
    return f"{_GRAPH}/{get_settings().meta_graph_version}"


# ─── Webhook signature verification ───────────────────────────────────────────

def verify_signature(payload: bytes, header: Optional[str]) -> bool:
    """Validate the X-Hub-Signature-256 header against the app secret.

    Returns True when no app secret is configured (dev/test convenience) so the
    webhook can be exercised locally; in production set META_APP_SECRET.
    """
    secret = get_settings().meta_app_secret
    if not secret:
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    received = header.split("=", 1)[1]
    return hmac.compare_digest(expected, received)


def verify_subscription(mode: str, token: str) -> bool:
    """GET webhook verification handshake (hub.mode / hub.verify_token)."""
    cfg = get_settings()
    return mode == "subscribe" and bool(cfg.meta_verify_token) and token == cfg.meta_verify_token


# ─── Lead retrieval ────────────────────────────────────────────────────────────

# Common Meta lead-form field names → our Lead model.
_NAME_FIELDS = ("full_name", "name", "first_name")
_PHONE_FIELDS = ("phone_number", "phone", "mobile_number")
_EMAIL_FIELDS = ("email", "email_address")
_EVENT_FIELDS = ("event_type", "what_type_of_event", "type_of_event", "service")
_DATE_FIELDS = ("event_date", "date_of_event", "tentative_date")


def fetch_lead(leadgen_id: str) -> Optional[dict]:
    """Fetch a submitted lead's field/value pairs plus campaign/form context.

    Returns a dict suitable for spreading into ``Database.create_lead`` kwargs,
    or None if the lead can't be retrieved.
    """
    cfg = get_settings()
    if not cfg.meta_page_access_token:
        log.warning("META_PAGE_ACCESS_TOKEN not set; cannot retrieve lead %s", leadgen_id)
        return None
    try:
        resp = requests.get(
            f"{_base()}/{leadgen_id}",
            params={
                "access_token": cfg.meta_page_access_token,
                "fields": "id,created_time,field_data,campaign_name,form_id,ad_id",
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("Failed to fetch Meta lead %s: %s", leadgen_id, exc)
        return None

    fields: dict[str, str] = {}
    for item in data.get("field_data", []):
        name = (item.get("name") or "").lower()
        values = item.get("values") or []
        fields[name] = values[0] if values else ""

    def pick(keys, default=""):
        for k in keys:
            if fields.get(k):
                return fields[k]
        return default

    tentative = None
    raw_date = pick(_DATE_FIELDS)
    if raw_date:
        try:
            tentative = date.fromisoformat(raw_date[:10])
        except ValueError:
            tentative = None

    contact = pick(_PHONE_FIELDS) or pick(_EMAIL_FIELDS)
    # Keep any unmapped fields in notes for the studio's reference.
    mapped = set(_NAME_FIELDS + _PHONE_FIELDS + _EMAIL_FIELDS + _EVENT_FIELDS + _DATE_FIELDS)
    extras = {k: v for k, v in fields.items() if k not in mapped and v}
    notes_parts = [f"{k}: {v}" for k, v in extras.items()]
    if pick(_EMAIL_FIELDS) and pick(_PHONE_FIELDS):
        notes_parts.insert(0, f"email: {pick(_EMAIL_FIELDS)}")
    notes = "\n".join(notes_parts)

    return {
        "leadgen_id": str(data.get("id", leadgen_id)),
        "client_name": pick(_NAME_FIELDS, "Meta Lead"),
        "contact": contact,
        "event_type": pick(_EVENT_FIELDS),
        "tentative_date": tentative,
        "source": "Meta",
        "notes": notes,
        "meta_campaign_name": data.get("campaign_name") or None,
        "meta_form_id": str(data.get("form_id")) if data.get("form_id") else None,
    }


# ─── Insights / metrics ────────────────────────────────────────────────────────

def fetch_insights(date_preset: str = "last_30d") -> list[dict]:
    """Pull campaign-level daily insights for the configured ad account.

    Returns a list of metric dicts ready for ``Database.replace_meta_metrics``.
    Empty list if not configured or on error.
    """
    cfg = get_settings()
    if not (cfg.meta_ad_account_id and cfg.meta_page_access_token):
        log.info("Meta ad account / token not configured; skipping insights pull.")
        return []

    act = cfg.meta_ad_account_id
    if act.startswith("act_"):
        act = act[4:]

    try:
        resp = requests.get(
            f"{_base()}/act_{act}/insights",
            params={
                "access_token": cfg.meta_page_access_token,
                "level": "campaign",
                "time_increment": 1,  # one row per day
                "date_preset": date_preset,
                "fields": "campaign_id,campaign_name,spend,impressions,reach,clicks,actions,account_currency",
                "limit": 500,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        log.warning("Failed to fetch Meta insights: %s", exc)
        return []

    metrics: list[dict] = []
    for row in payload.get("data", []):
        # "leads" come through the actions array (action_type 'leadgen.other'
        # or 'leadgen_grouped' / 'lead'); sum any leadgen-style action.
        leads = 0
        for a in row.get("actions", []) or []:
            atype = a.get("action_type", "")
            if "lead" in atype:
                try:
                    leads += int(float(a.get("value", 0)))
                except (ValueError, TypeError):
                    pass
        spend = float(row.get("spend", 0) or 0)
        cpl = (spend / leads) if leads else 0.0
        row_date = row.get("date_start")
        try:
            d = date.fromisoformat(row_date) if row_date else datetime.utcnow().date()
        except ValueError:
            d = datetime.utcnow().date()
        metrics.append({
            "campaign_id": row.get("campaign_id", ""),
            "campaign_name": row.get("campaign_name", ""),
            "date": d,
            "spend": spend,
            "impressions": int(float(row.get("impressions", 0) or 0)),
            "reach": int(float(row.get("reach", 0) or 0)),
            "clicks": int(float(row.get("clicks", 0) or 0)),
            "leads": leads,
            "cpl": round(cpl, 2),
            "currency": row.get("account_currency", ""),
        })
    return metrics
