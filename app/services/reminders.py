"""Follow-up reminder logic — find New/Quoted leads due for follow-up today and
render an email digest.
"""
from __future__ import annotations

import logging
from datetime import date

from app.config import get_settings
from app.database import SheetDB
from app.domain import Lead
from app.services.email import email_configured, send_email
from app.templating import _format_money

log = logging.getLogger(__name__)

# Statuses that are still "in play" and worth chasing.
_ACTIVE_STATUSES = {"new", "quoted"}

# Settings key: highest lead id already emailed to owners (new-lead notifications).
_NOTIFY_CURSOR_KEY = "new_lead_notify_cursor"

# Sources that count as inbound/ad-captured leads worth notifying owners about.
# Manually entered referrals/walk-ins are excluded (the user already knows).
_INBOUND_SOURCES = {"Instagram", "Meta"}


def due_followups(db: SheetDB, today: date | None = None) -> list[Lead]:
    """New/Quoted leads whose follow-up date is today and aren't marked done.

    Exact-date match (reminder fires *on* the due date). Sorted by client name
    for a stable digest order.
    """
    today = today or date.today()
    out = [
        lead for lead in db.list_leads()
        if lead.status in _ACTIVE_STATUSES
        and lead.followup_status != "done"
        and lead.followup_date == today
    ]
    out.sort(key=lambda l: (l.client_name or "").lower())
    return out


def build_followup_digest(leads: list[Lead], base_url: str,
                          today: date | None = None) -> tuple[str, str]:
    """Return (subject, html) for the daily follow-up digest."""
    today = today or date.today()
    n = len(leads)
    subject = f"[LIF CRM] {n} lead{'s' if n != 1 else ''} to follow up — {today:%d %b %Y}"

    base = base_url.rstrip("/")
    rows = "".join(
        f"""
        <tr>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;font-weight:600;">
            <a href="{base}/leads/{l.id}" style="color:#2563eb;text-decoration:none;">
              {_esc(l.client_name) or '—'}</a>
          </td>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;">{_esc(l.contact) or '—'}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;text-transform:capitalize;">{_esc(l.status)}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;">{_esc(l.event_type) or '—'}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;">{l.tentative_date or '—'}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;text-align:right;">{_format_money(l.quoted_amount) if l.quoted_amount else '—'}</td>
        </tr>"""
        for l in leads
    )

    html = f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#1f2937;max-width:680px;">
  <h2 style="margin:0 0 4px;">Follow-ups due today</h2>
  <p style="margin:0 0 16px;color:#6b7280;">{today:%A, %d %B %Y} — {n} lead{'s' if n != 1 else ''} in New/Quoted stage.</p>
  <table style="border-collapse:collapse;width:100%;font-size:14px;">
    <thead>
      <tr style="background:#f9fafb;text-align:left;">
        <th style="padding:8px 10px;border-bottom:2px solid #e5e7eb;">Client</th>
        <th style="padding:8px 10px;border-bottom:2px solid #e5e7eb;">Contact</th>
        <th style="padding:8px 10px;border-bottom:2px solid #e5e7eb;">Status</th>
        <th style="padding:8px 10px;border-bottom:2px solid #e5e7eb;">Event</th>
        <th style="padding:8px 10px;border-bottom:2px solid #e5e7eb;">Tentative date</th>
        <th style="padding:8px 10px;border-bottom:2px solid #e5e7eb;text-align:right;">Quote</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  <p style="margin:18px 0 0;font-size:13px;color:#9ca3af;">
    Sent by Life in Frame CRM. Open the pipeline: <a href="{base}/leads" style="color:#2563eb;">{base}/leads</a>
  </p>
</div>"""
    return subject, html


def _is_inbound_lead(lead: Lead) -> bool:
    """True for ad-/inbound-captured leads (the ones owners want pinged about)."""
    return bool(lead.meta_campaign) or (lead.source or "").strip() in _INBOUND_SOURCES


def build_new_leads_email(leads: list[Lead], base_url: str) -> tuple[str, str]:
    """Return (subject, html) for a new-lead notification.

    ``leads`` is a list of freshly captured ``Lead`` objects (Instagram/Meta
    inbound). Each row links straight to that lead in the CRM.
    """
    from datetime import datetime
    n = len(leads)
    noun = "lead" if n == 1 else "leads"
    hot = sum(1 for l in leads if getattr(l, "triage", "") == "hot")
    fire = f"🔥 {hot} hot — " if hot else ""
    subject = f"[LIF CRM] {fire}{n} new {noun} from Meta / Instagram"
    base = base_url.rstrip("/")

    _triage_style = {
        "hot":        ("🔥 Hot", "#dc2626"),
        "warm":       ("Warm", "#d97706"),
        "low_intent": ("Low intent", "#6b7280"),
        "spam":       ("Spam?", "#9ca3af"),
    }

    def _triage_cell(l) -> str:
        label, color = _triage_style.get(getattr(l, "triage", "") or "", ("—", "#9ca3af"))
        return (f'<td style="padding:8px 10px;border-bottom:1px solid #eee;'
                f'color:{color};font-weight:600;font-size:13px;">{label}</td>')

    rows = "".join(
        f"""
        <tr>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;font-weight:600;">
            <a href="{base}/leads/{l.id}" style="color:#2563eb;text-decoration:none;">
              {_esc(l.client_name) or '—'}</a>
          </td>
          {_triage_cell(l)}
          <td style="padding:8px 10px;border-bottom:1px solid #eee;">{_esc(l.contact) or '—'}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;">{_esc(l.event_type) or '—'}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;">{_esc(l.source) or '—'}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;color:#6b7280;font-size:13px;">{_esc(l.meta_campaign_name) or '—'}</td>
        </tr>"""
        for l in leads
    )

    html = f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#1f2937;max-width:680px;">
  <h2 style="margin:0 0 4px;">{n} new {noun} captured</h2>
  <p style="margin:0 0 16px;color:#6b7280;">
    {datetime.now().strftime('%d %b %Y, %I:%M %p')}
  </p>
  <table style="border-collapse:collapse;width:100%;font-size:14px;">
    <thead>
      <tr style="background:#f9fafb;text-align:left;">
        <th style="padding:8px 10px;border-bottom:2px solid #e5e7eb;">Name</th>
        <th style="padding:8px 10px;border-bottom:2px solid #e5e7eb;">Triage</th>
        <th style="padding:8px 10px;border-bottom:2px solid #e5e7eb;">Phone</th>
        <th style="padding:8px 10px;border-bottom:2px solid #e5e7eb;">Looking for</th>
        <th style="padding:8px 10px;border-bottom:2px solid #e5e7eb;">Source</th>
        <th style="padding:8px 10px;border-bottom:2px solid #e5e7eb;">Campaign</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  <p style="margin:18px 0 0;font-size:13px;color:#9ca3af;">
    <a href="{base}/leads" style="color:#2563eb;">Open Leads in CRM →</a>
  </p>
</div>"""
    return subject, html


def notify_new_leads(db: SheetDB) -> dict:
    """Email owners about inbound leads captured since the last successful send.

    Resilient + idempotent by design, and deliberately decoupled from any single
    import run:

    * A high-water-mark lead id is kept in settings (``new_lead_notify_cursor``).
      Only leads with a higher id are emailed, so no lead is ever notified twice
      however often this runs.
    * The cursor advances ONLY after a successful send. A transient SMTP failure
      leaves it untouched, so the lead is retried on the next run rather than lost
      forever — the old per-import-run approach permanently missed a lead if that
      one run's send hiccuped, because the next run deduped it (imported == 0) and
      never looked again.
    * Meant to run on every import-leads cron tick regardless of whether that tick
      imported anything, so it also catches leads created by the Meta webhook.

    The first-ever run seeds the cursor to the current max id WITHOUT sending, so
    historical leads are never blasted. Never raises — a notification problem must
    not crash or roll back the import.
    """
    studio  = db.get_settings_dict()
    leads   = db.list_leads()
    max_id  = max((l.id for l in leads), default=0)

    raw_cursor = studio.get(_NOTIFY_CURSOR_KEY)
    if raw_cursor in (None, ""):
        db.set_settings({_NOTIFY_CURSOR_KEY: str(max_id)})
        log.info("New-lead notify cursor initialised at id=%d (no email sent).", max_id)
        return {"notified": False, "reason": "cursor_initialised", "cursor": max_id}
    try:
        cursor = int(raw_cursor)
    except (TypeError, ValueError):
        cursor = 0

    pending = sorted((l for l in leads if l.id > cursor and _is_inbound_lead(l)),
                     key=lambda l: l.id)
    if not pending:
        return {"notified": False, "reason": "no_new_leads", "cursor": cursor}

    if not email_configured():
        log.warning("New-lead notification deferred: SMTP not configured (%d pending).",
                    len(pending))
        return {"notified": False, "reason": "smtp_not_configured", "pending": len(pending)}
    recipients = [e.strip() for e in (studio.get("role_owners") or "").split(",")
                  if e.strip()]
    if not recipients:
        log.warning("New-lead notification deferred: no owners in role_owners (%d pending).",
                    len(pending))
        return {"notified": False, "reason": "no_recipients", "pending": len(pending)}

    base_url = get_settings().public_base_url or "https://lifcrm.netlify.app"
    try:
        subject, html = build_new_leads_email(pending, base_url)
        send_email(subject, html, recipients)
    except Exception as exc:  # noqa: BLE001 — never advance the cursor on failure; retry next run
        log.warning("New-lead notification FAILED (%s): %s — keeping cursor at %d for retry.",
                    type(exc).__name__, exc, cursor)
        return {"notified": False, "reason": f"{type(exc).__name__}: {exc}",
                "pending": len(pending), "cursor": cursor}

    db.set_settings({_NOTIFY_CURSOR_KEY: str(max_id)})
    log.info("New-lead notification sent: %d lead(s) to %d owner(s); cursor %d→%d.",
             len(pending), len(recipients), cursor, max_id)
    return {"notified": True, "leads": len(pending),
            "recipients": len(recipients), "cursor": max_id}


def _esc(value) -> str:
    """Minimal HTML escape for interpolated text."""
    if value is None:
        return ""
    return (
        str(value).replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;")
    )
