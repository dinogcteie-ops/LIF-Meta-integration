"""Meta Ads integration routes.

  GET  /webhooks/meta/leads   — webhook verification handshake (public)
  POST /webhooks/meta/leads   — receive leadgen events, create Leads (public, signed)
  GET  /meta                  — Meta Ads metrics dashboard (auth required)
  POST /meta/refresh          — pull latest Insights into the metrics cache
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

from app.database import get_db, SheetDB
from app.services import meta as meta_api
from app.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


# ─── Webhook (public; signature-verified) ─────────────────────────────────────

@router.get("/webhooks/meta/leads")
def verify_webhook(request: Request):
    """Meta calls this once to verify the subscription."""
    params = request.query_params
    mode = params.get("hub.mode", "")
    token = params.get("hub.verify_token", "")
    challenge = params.get("hub.challenge", "")
    if meta_api.verify_subscription(mode, token):
        return PlainTextResponse(challenge)
    return PlainTextResponse("verification failed", status_code=403)


@router.post("/webhooks/meta/leads")
async def receive_webhook(request: Request, db: SheetDB = Depends(get_db)):
    raw = await request.body()
    sig = request.headers.get("x-hub-signature-256")
    if not meta_api.verify_signature(raw, sig):
        return JSONResponse({"error": "invalid signature"}, status_code=403)

    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return JSONResponse({"error": "bad payload"}, status_code=400)

    created = 0
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "leadgen":
                continue
            leadgen_id = (change.get("value") or {}).get("leadgen_id")
            if not leadgen_id:
                continue
            # Idempotency — Meta may redeliver the same event.
            if db.get_lead_by_meta_id(str(leadgen_id)):
                continue
            info = meta_api.fetch_lead(str(leadgen_id))
            if not info:
                continue
            db.create_lead(
                client_name=info["client_name"],
                contact=info["contact"],
                event_type=info["event_type"],
                tentative_date=info["tentative_date"],
                source="Meta",
                status="new",
                notes=info["notes"],
                meta_campaign=True,
                meta_lead_id=info["leadgen_id"],
                meta_campaign_name=info["meta_campaign_name"],
                meta_form_id=info["meta_form_id"],
            )
            created += 1
    return JSONResponse({"received": True, "created": created})


# ─── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/meta")
def meta_dashboard(request: Request, db: SheetDB = Depends(get_db)):
    metrics = db.list_meta_metrics()

    # Per-campaign rollups
    by_campaign: dict[str, dict] = defaultdict(
        lambda: {"campaign_name": "", "spend": 0.0, "impressions": 0,
                 "reach": 0, "clicks": 0, "leads": 0}
    )
    by_date: dict[str, dict] = defaultdict(lambda: {"spend": 0.0, "leads": 0})
    for m in metrics:
        c = by_campaign[m.campaign_id or m.campaign_name]
        c["campaign_name"] = m.campaign_name or m.campaign_id
        c["spend"] += m.spend
        c["impressions"] += m.impressions
        c["reach"] += m.reach
        c["clicks"] += m.clicks
        c["leads"] += m.leads
        d = by_date[m.date.isoformat()]
        d["spend"] += m.spend
        d["leads"] += m.leads

    campaigns = []
    for cid, c in by_campaign.items():
        c["cpl"] = round(c["spend"] / c["leads"], 2) if c["leads"] else 0.0
        c["campaign_id"] = cid
        campaigns.append(c)
    campaigns.sort(key=lambda x: x["spend"], reverse=True)

    totals = {
        "spend": round(sum(c["spend"] for c in campaigns), 2),
        "impressions": sum(c["impressions"] for c in campaigns),
        "reach": sum(c["reach"] for c in campaigns),
        "clicks": sum(c["clicks"] for c in campaigns),
        "leads": sum(c["leads"] for c in campaigns),
    }
    totals["cpl"] = round(totals["spend"] / totals["leads"], 2) if totals["leads"] else 0.0
    # Leads actually captured in the pipeline from Meta:
    meta_leads = [l for l in db.list_leads() if l.meta_campaign]

    timeseries = sorted(by_date.items())
    currency = metrics[0].currency if metrics else ""
    last_synced = max((m.fetched_at for m in metrics), default="")

    return templates.TemplateResponse(
        request, "meta/dashboard.html", {
            "campaigns": campaigns,
            "totals": totals,
            "meta_lead_count": len(meta_leads),
            "chart_labels": [d for d, _ in timeseries],
            "chart_spend": [round(v["spend"], 2) for _, v in timeseries],
            "chart_leads": [v["leads"] for _, v in timeseries],
            "currency": currency,
            "last_synced": last_synced,
            "configured": bool(metrics),
        }
    )


@router.post("/meta/refresh")
def refresh_metrics(request: Request, db: SheetDB = Depends(get_db)):
    # This path is public (so the scheduler can hit it) — gate it: either a
    # logged-in user clicked "Refresh", or the caller presents the verify token.
    from app.config import get_settings
    logged_in = bool(request.session.get("user")) if request.session is not None else False
    token = request.query_params.get("token", "")
    verify = get_settings().meta_verify_token
    if not logged_in and not (verify and token == verify):
        return JSONResponse({"error": "unauthorized"}, status_code=403)

    metrics = meta_api.fetch_insights()
    written = db.replace_meta_metrics(metrics)
    if logged_in:
        request.session["flash"] = f"Meta metrics refreshed — {written} rows."
        return RedirectResponse(url="/meta", status_code=303)
    return JSONResponse({"refreshed": True, "rows": written})
