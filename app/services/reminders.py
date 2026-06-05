"""Follow-up reminder logic — find New/Quoted leads due for follow-up today and
render an email digest.
"""
from __future__ import annotations

from datetime import date

from app.database import SheetDB
from app.domain import Lead
from app.templating import _format_money

# Statuses that are still "in play" and worth chasing.
_ACTIVE_STATUSES = {"new", "quoted"}


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


def _esc(value) -> str:
    """Minimal HTML escape for interpolated text."""
    if value is None:
        return ""
    return (
        str(value).replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;")
    )
