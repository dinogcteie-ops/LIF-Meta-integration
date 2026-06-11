"""Instagram lead report — a story-first, exhibit-backed email.

Structure follows the pyramid principle: the answer comes first.
  1. An assessment banner with the single most important "so what".
  2. Key findings — quantified insights, not raw numbers.
  3. Recommended actions — concrete next steps tied to the findings.
  4. Supporting exhibits — five matplotlib charts, each captioned with its
     takeaway rather than a bare title.

Scheduled on the 1st and 16th of each month via POST /jobs/lead-report (Netlify
cron). Can also be triggered from the Settings page with a custom date range.
Only leads whose source == "Instagram" are included.
"""
from __future__ import annotations

import io
import textwrap
from dataclasses import dataclass, field
from datetime import date, timedelta

import matplotlib
matplotlib.use("Agg")   # non-interactive backend — must precede pyplot import
import matplotlib.pyplot as plt

from app.domain import Lead
from app.enums import LostReason
from app.services.reports import (
    LeadFunnel,
    LostReasonBreakdown,
    lead_funnel,
    lost_reason_breakdown,
)

# ─── Constants ────────────────────────────────────────────────────────────────

SOURCE      = "Instagram"
SPAM_REASON = LostReason.spam.value   # "Invalid / Spam Inquiry"

_STATUS_ORDER  = ["new", "quoted", "won", "lost", "cold"]
_STATUS_LABELS = ["New", "Quoted", "Won", "Lost", "Cold"]

# Restrained, consistent palette — one alert hue reserved for the problem.
_INK      = "#111827"   # near-black headings
_BODY     = "#374151"   # body text
_MUTED    = "#9ca3af"   # axis / secondary
_GRID     = "#e5e7eb"
_BLUE     = "#2563eb"   # current period / neutral series
_LIGHT    = "#cbd5e1"   # previous period
_ALERT    = "#dc2626"   # the problem (spam, losses)
_GOOD     = "#16a34a"
_AMBER    = "#d97706"

_STATUS_COLORS = {
    "new":    "#3b82f6",
    "quoted": _AMBER,
    "won":    _GOOD,
    "lost":   _ALERT,
    "cold":   "#94a3b8",
}

# Verdict → (label, accent colour, tint background) for the assessment banner.
_VERDICT_STYLE = {
    "critical": ("CRITICAL",  "#b91c1c", "#fef2f2"),
    "warning":  ("NEEDS ATTENTION", "#b45309", "#fffbeb"),
    "positive": ("HEALTHY",   "#15803d", "#f0fdf4"),
    "neutral":  ("STEADY",    "#1d4ed8", "#eff6ff"),
}


# ─── Date helpers ─────────────────────────────────────────────────────────────

def default_period(today: date | None = None) -> tuple[date, date]:
    """Standard 15-day window ending yesterday."""
    if today is None:
        today = date.today()
    end   = today - timedelta(days=1)
    start = end   - timedelta(days=14)
    return start, end


def previous_period(start: date, end: date) -> tuple[date, date]:
    """The 15-day window immediately before (start, end)."""
    prev_end   = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=14)
    return prev_start, prev_end


def fmt_date(d: date) -> str:
    return f"{d.day} {d.strftime('%b')}"


# ─── Insight engine ───────────────────────────────────────────────────────────

@dataclass
class ReportInsights:
    headline: str
    verdict:  str                      # critical | warning | positive | neutral
    findings: list[str] = field(default_factory=list)
    actions:  list[str] = field(default_factory=list)


def _pct(n: float, d: float) -> float:
    return (n / d * 100) if d else 0.0


def _spam_count(bd: LostReasonBreakdown) -> int:
    return next((r.count for r in bd.rows if r.reason == SPAM_REASON), 0)


def _arrow(delta: float) -> str:
    return "up" if delta > 0 else ("down" if delta < 0 else "flat")


def derive_insights(
    fc: LeadFunnel,
    fp: LeadFunnel,
    bd_curr: LostReasonBreakdown,
    label_curr: str,
    label_prev: str,
) -> ReportInsights:
    """Turn the period's numbers into a narrative: headline, findings, actions.

    Rules are ordered so the most consequential signal becomes the headline.
    Everything is quantified — every statement carries the number behind it.
    """
    total_c, total_p = fc.total_all, fp.total_all
    vol_delta = total_c - total_p
    vol_pct   = _pct(vol_delta, total_p)

    spam_c          = _spam_count(bd_curr)
    spam_of_lost    = _pct(spam_c, bd_curr.total_lost)
    conv_c, conv_p  = fc.conversion_rate, fp.conversion_rate

    quality_crisis = spam_of_lost >= 50 and bd_curr.total_lost >= 3
    conv_collapse  = conv_p > 0 and conv_c <= conv_p / 2
    won_up         = fc.won_count > fp.won_count

    # ── Headline + verdict (pick the strongest story) ─────────────────────────
    if vol_pct >= 20 and quality_crisis:
        headline = (
            f"Instagram volume rose {vol_pct:.0f}%, but it's the wrong volume — "
            f"{spam_of_lost:.0f}% of lost leads were spam and conversion fell to "
            f"{conv_c:.0f}%. The channel is generating noise, not customers."
        )
        verdict = "critical"
    elif quality_crisis:
        headline = (
            f"Lead quality is the constraint: {spam_of_lost:.0f}% of lost Instagram "
            f"leads ({spam_c} of {bd_curr.total_lost}) were invalid or spam inquiries."
        )
        verdict = "critical"
    elif conv_collapse:
        headline = (
            f"Instagram conversion fell sharply — from {conv_p:.0f}% to {conv_c:.0f}% — "
            f"with {fc.won_count} win(s) from {total_c} leads this period."
        )
        verdict = "warning"
    elif won_up and conv_c >= conv_p:
        headline = (
            f"Instagram is converting better — {fc.won_count} wins at {conv_c:.0f}% "
            f"conversion, up from {fp.won_count} wins at {conv_p:.0f}%."
        )
        verdict = "positive"
    else:
        direction = _arrow(vol_delta)
        headline = (
            f"Instagram volume {direction} {abs(vol_pct):.0f}% ({total_p} → {total_c} leads); "
            f"conversion holding at {conv_c:.0f}%."
        )
        verdict = "neutral"

    # ── Key findings (quantified; keep the 4 most relevant) ───────────────────
    findings: list[str] = []
    findings.append(
        f"Volume {_arrow(vol_delta)} {abs(vol_pct):.0f}% — {total_p} → {total_c} "
        f"Instagram leads versus the prior 15 days."
    )
    win_delta = fc.won_count - fp.won_count
    findings.append(
        f"{fc.won_count} win(s) this period ({win_delta:+d} vs prior), "
        f"₹{fc.total_won_value:.2f}L booked."
    )
    findings.append(
        f"Conversion {conv_c:.0f}% (was {conv_p:.0f}%) — share of decided leads that closed."
    )
    if spam_c:
        findings.append(
            f"Spam is the biggest leak: {spam_c} of {bd_curr.total_lost} losses "
            f"({spam_of_lost:.0f}%) were invalid inquiries."
        )
    # Top genuine (non-spam) loss reason, if any.
    genuine = [r for r in bd_curr.rows if r.reason != SPAM_REASON]
    if genuine:
        top = genuine[0]
        findings.append(
            f"Top genuine loss reason: “{top.reason}” ({top.count} lead(s), "
            f"₹{top.value:.2f}L at stake)."
        )
    if fc.quoted_count:
        findings.append(
            f"{fc.quoted_count} lead(s) sitting at the quoted stage — "
            f"₹{fc.total_pipeline_value:.2f}L of open pipeline to chase."
        )

    # ── Recommended actions (tied to the findings) ────────────────────────────
    actions: list[str] = []
    if quality_crisis:
        actions.append(
            "Tighten the Instagram lead form — add qualifying questions (budget, "
            "event date, event type) so spam is filtered before it enters the pipeline."
        )
        actions.append(
            "Review the Instagram ad targeting/placements with Meta — a spam spike "
            "usually points to over-broad audiences or bot-heavy placements."
        )
    if fc.quoted_count > 0 and fc.won_count == 0:
        actions.append(
            f"Chase the {fc.quoted_count} quoted lead(s) now — none have closed; "
            "speed-to-follow-up is the gap between a quote and a booking."
        )
    if conv_collapse and not quality_crisis:
        actions.append(
            "Audit response time on genuine enquiries — 'delayed follow-up / lost "
            "momentum' is a recurring, preventable loss reason."
        )
    if not actions:
        actions.append(
            "Hold the current follow-up cadence — the channel is healthy; protect "
            "speed-to-response as volume grows."
        )

    return ReportInsights(
        headline=headline, verdict=verdict,
        findings=findings[:4], actions=actions[:3],
    )


# ─── Chart styling ────────────────────────────────────────────────────────────

def _save_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=130, facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def _new_ax(figsize: tuple):
    fig, ax = plt.subplots(figsize=figsize)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    return fig, ax


def _finish_ax(ax, title: str, xlabel: str = "", *, vertical: bool = False):
    ax.set_title(title, fontsize=12.5, fontweight="bold", pad=10,
                 color=_INK, loc="left")
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=9, color=_MUTED)
    ax.tick_params(labelsize=9.5, colors=_BODY, length=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    grid_axis = "y" if vertical else "x"
    ax.grid(axis=grid_axis, color=_GRID, linewidth=0.7, linestyle="-")
    ax.set_axisbelow(True)
    keep = "left" if vertical else "bottom"
    drop = "bottom" if vertical else "left"
    ax.spines[keep].set_color(_GRID)
    ax.spines[drop].set_visible(False)


# ─── Chart generators ─────────────────────────────────────────────────────────

def chart_status_funnel(leads: list[Lead], title: str) -> bytes:
    """Horizontal bars of lead counts by status (won/lost emphasised)."""
    f = lead_funnel(leads)
    counts = [f.new_count, f.quoted_count, f.won_count, f.lost_count, f.cold_count]
    colors = [_STATUS_COLORS[s] for s in _STATUS_ORDER]
    total  = sum(counts) or 1

    fig, ax = _new_ax((6.6, 3.0))
    ys = list(range(len(_STATUS_LABELS)))[::-1]   # New at top
    ax.barh(ys, counts, color=colors, height=0.62, edgecolor="none")

    for y, cnt in zip(ys, counts):
        share = cnt / total * 100
        txt = f"  {cnt}  ·  {share:.0f}%" if cnt else "  0"
        ax.text(cnt, y, txt, va="center", ha="left", fontsize=9.5,
                color=_BODY, fontweight="600")

    ax.set_yticks(ys)
    ax.set_yticklabels(_STATUS_LABELS, fontsize=10, color=_BODY)
    ax.set_xlim(0, max(counts or [1]) * 1.32 + 1)
    _finish_ax(ax, title, "Leads")
    fig.tight_layout(pad=0.6)
    return _save_png(fig)


def chart_lost_reasons(leads: list[Lead], title: str) -> bytes:
    """Horizontal bars of lost-reason counts; spam highlighted in alert red."""
    bd = lost_reason_breakdown(leads)

    if not bd.has_data:
        fig, ax = _new_ax((6.6, 1.7))
        ax.axis("off")
        ax.set_title(title, fontsize=12.5, fontweight="bold", pad=10,
                     color=_INK, loc="left")
        ax.text(0.5, 0.4, "No lost leads in this period",
                ha="center", va="center", fontsize=11, color=_MUTED,
                transform=ax.transAxes)
        return _save_png(fig)

    rows   = bd.rows[::-1]    # biggest at top after barh
    labels = [textwrap.shorten(r.reason, width=32, placeholder="…") for r in rows]
    counts = [r.count for r in rows]
    colors = [_ALERT if r.reason == SPAM_REASON else "#fca5a5" for r in rows]
    n = len(labels)
    h = max(2.2, n * 0.46 + 1.0)

    fig, ax = _new_ax((6.6, h))
    ys = list(range(n))
    ax.barh(ys, counts, color=colors, height=0.62, edgecolor="none")

    for i, (cnt, r) in enumerate(zip(counts, rows)):
        share = _pct(r.count, bd.total_lost)
        ax.text(cnt, i, f"  {cnt}  ·  {share:.0f}%", va="center", ha="left",
                fontsize=9.5, color=_BODY, fontweight="600")

    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontsize=9.5, color=_BODY)
    ax.set_xlim(0, max(counts or [1]) * 1.34 + 0.5)
    _finish_ax(ax, title, "Lost leads")
    fig.tight_layout(pad=0.6)
    return _save_png(fig)


def chart_period_comparison(
    fc: LeadFunnel, fp: LeadFunnel, label_curr: str, label_prev: str,
) -> bytes:
    """Grouped bars — this period vs previous. Current 'Lost' flagged red."""
    curr = [fc.new_count, fc.quoted_count, fc.won_count, fc.lost_count, fc.cold_count]
    prev = [fp.new_count, fp.quoted_count, fp.won_count, fp.lost_count, fp.cold_count]

    x = list(range(len(_STATUS_ORDER)))
    w = 0.38
    # Current bars: blue, except Lost in alert red so the eye lands on the problem.
    curr_colors = [_ALERT if s == "lost" else _BLUE for s in _STATUS_ORDER]

    fig, ax = _new_ax((6.8, 3.6))
    ax.bar([xi - w / 2 for xi in x], prev, w, color=_LIGHT, edgecolor="none",
           label=f"Previous · {label_prev}")
    ax.bar([xi + w / 2 for xi in x], curr, w, color=curr_colors, edgecolor="none")

    # Good/bad direction is status-specific: more New/Quoted/Won is good, but
    # more Lost/Cold is bad — colour the deltas accordingly so the eye reads
    # the story correctly (a +25 in Lost must not look like a win).
    _up_is_good = {"new": True, "quoted": True, "won": True,
                   "lost": False, "cold": False}
    for xi, (status, c, p) in enumerate(zip(_STATUS_ORDER, curr, prev)):
        d = c - p
        if d == 0:
            txt, clr = "0", _MUTED
        else:
            improved = (d > 0) == _up_is_good[status]
            txt, clr = f"{d:+d}", (_GOOD if improved else _ALERT)
        ax.text(xi + w / 2, c, f"\n{txt}" if c == 0 else txt,
                ha="center", va="bottom", fontsize=8.5, fontweight="bold", color=clr)

    ax.set_xticks(x)
    ax.set_xticklabels(_STATUS_LABELS, fontsize=10, color=_BODY)
    peak = max(max(curr or [0]), max(prev or [0]), 1)
    ax.set_ylim(0, peak * 1.30)
    _finish_ax(ax, "This period vs previous — Instagram", "", vertical=True)

    # Hand-built legend so the multi-coloured current series reads clearly.
    from matplotlib.patches import Patch
    handles = [
        Patch(color=_LIGHT, label=f"Previous · {label_prev}"),
        Patch(color=_BLUE,  label=f"Current · {label_curr}"),
        Patch(color=_ALERT, label="Current · lost leads"),
    ]
    ax.legend(handles=handles, fontsize=8.5, frameon=False, loc="upper left",
              ncol=1, handlelength=1.2)
    fig.tight_layout(pad=0.6)
    return _save_png(fig)


# ─── Plain-text fallback ──────────────────────────────────────────────────────

def build_text_summary(ins: ReportInsights, fc: LeadFunnel, fp: LeadFunnel,
                        label_curr: str, label_prev: str) -> str:
    lines = [
        f"INSTAGRAM LEAD REPORT — {label_curr} vs {label_prev}",
        "",
        f"ASSESSMENT [{_VERDICT_STYLE[ins.verdict][0]}]",
        ins.headline,
        "",
        "KEY FINDINGS",
    ]
    lines += [f"  - {f}" for f in ins.findings]
    lines += ["", "RECOMMENDED ACTIONS"]
    lines += [f"  {i}. {a}" for i, a in enumerate(ins.actions, 1)]
    lines += [
        "",
        "AT A GLANCE (current / previous)",
        f"  Total leads  {fc.total_all} / {fp.total_all}",
        f"  New          {fc.new_count} / {fp.new_count}",
        f"  Quoted       {fc.quoted_count} / {fp.quoted_count}",
        f"  Won          {fc.won_count} / {fp.won_count}",
        f"  Lost         {fc.lost_count} / {fp.lost_count}",
        f"  Cold         {fc.cold_count} / {fp.cold_count}",
        f"  Conversion   {fc.conversion_rate:.1f}% / {fp.conversion_rate:.1f}%",
    ]
    return "\n".join(lines)


# ─── HTML email builder ───────────────────────────────────────────────────────

def _glance_table(fc: LeadFunnel, fp: LeadFunnel) -> str:
    def _row(label: str, curr, prev, is_pct: bool = False) -> str:
        if is_pct:
            d = curr - prev
            cur_s, prev_s = f"{curr:.1f}%", f"{prev:.1f}%"
            sign = f"{d:+.1f} pts" if abs(d) >= 0.05 else "—"
        else:
            d = int(curr) - int(prev)
            cur_s, prev_s = str(int(curr)), str(int(prev))
            sign = f"{d:+d}" if d else "—"
        clr = _GOOD if d > 0 else (_ALERT if d < 0 else _MUTED)
        # For 'lost', growth is bad — flip the colour semantics.
        if label.strip().lower().startswith("lost") and d != 0:
            clr = _ALERT if d > 0 else _GOOD
        return (
            "<tr>"
            f"<td style='padding:5px 14px 5px 0;color:{_BODY};font-size:13px'>{label}</td>"
            f"<td style='padding:5px 10px;font-weight:700;text-align:right;font-size:13px;color:{_INK}'>{cur_s}</td>"
            f"<td style='padding:5px 10px;text-align:right;font-size:12px;color:{clr};font-weight:700'>{sign}</td>"
            f"<td style='padding:5px 0 5px 10px;color:{_MUTED};font-size:12px'>was {prev_s}</td>"
            "</tr>"
        )

    head = (
        "<tr>"
        f"<td style='padding:0 14px 6px 0;font-size:11px;color:{_MUTED};text-transform:uppercase;letter-spacing:.05em'>Metric</td>"
        f"<td style='padding:0 10px 6px;font-size:11px;color:{_MUTED};text-align:right;text-transform:uppercase;letter-spacing:.05em'>Now</td>"
        f"<td style='padding:0 10px 6px;font-size:11px;color:{_MUTED};text-align:right;text-transform:uppercase;letter-spacing:.05em'>Δ</td>"
        f"<td style='padding:0 0 6px 10px;font-size:11px;color:{_MUTED};text-transform:uppercase;letter-spacing:.05em'>Prior</td>"
        "</tr>"
    )
    rows = (
        _row("Total leads", fc.total_all,   fp.total_all)
        + _row("New",        fc.new_count,    fp.new_count)
        + _row("Quoted",     fc.quoted_count, fp.quoted_count)
        + _row("Won",        fc.won_count,    fp.won_count)
        + _row("Lost",       fc.lost_count,   fp.lost_count)
        + _row("Cold",       fc.cold_count,   fp.cold_count)
        + _row("Conversion", fc.conversion_rate, fp.conversion_rate, is_pct=True)
    )
    return (
        f"<table style='border-collapse:collapse;font-family:Arial,sans-serif'>"
        f"{head}{rows}</table>"
    )


def _banner(ins: ReportInsights) -> str:
    label, accent, tint = _VERDICT_STYLE[ins.verdict]
    return f"""
<div style="background:{tint};border-left:5px solid {accent};border-radius:6px;
            padding:16px 20px;margin:6px 0 22px 0">
  <div style="font-size:11px;font-weight:800;letter-spacing:.09em;color:{accent};
              text-transform:uppercase;margin-bottom:7px">Assessment · {label}</div>
  <div style="font-size:17px;line-height:1.45;font-weight:700;color:{_INK}">
    {ins.headline}
  </div>
</div>
"""


def _findings_block(ins: ReportInsights) -> str:
    items = "".join(
        f"<li style='margin:0 0 9px 0;font-size:13.5px;line-height:1.5;color:{_BODY}'>{f}</li>"
        for f in ins.findings
    )
    return f"""
<h2 style="font-size:13px;font-weight:800;letter-spacing:.06em;color:{_MUTED};
           text-transform:uppercase;margin:22px 0 10px 0">Key findings</h2>
<ul style="margin:0 0 4px 0;padding-left:20px">{items}</ul>
"""


def _actions_block(ins: ReportInsights) -> str:
    items = "".join(
        f"<tr><td style='vertical-align:top;padding:0 10px 10px 0'>"
        f"<span style='display:inline-block;width:22px;height:22px;border-radius:50%;"
        f"background:{_BLUE};color:#fff;font-size:12px;font-weight:700;text-align:center;"
        f"line-height:22px'>{i}</span></td>"
        f"<td style='padding:0 0 10px 0;font-size:13.5px;line-height:1.5;color:{_BODY}'>{a}</td></tr>"
        for i, a in enumerate(ins.actions, 1)
    )
    return f"""
<h2 style="font-size:13px;font-weight:800;letter-spacing:.06em;color:{_MUTED};
           text-transform:uppercase;margin:22px 0 10px 0">Recommended actions</h2>
<table style="border-collapse:collapse">{items}</table>
"""


def _exhibit(num: int, takeaway: str, cid: str, alt: str) -> str:
    return (
        f'<div style="margin:24px 0 6px 0">'
        f'<div style="font-size:11px;font-weight:800;letter-spacing:.06em;color:{_BLUE};'
        f'text-transform:uppercase">Exhibit {num}</div>'
        f'<div style="font-size:14px;font-weight:700;color:{_INK};margin:2px 0 8px 0;'
        f'line-height:1.4">{takeaway}</div>'
        f'<img src="cid:{cid}" alt="{alt}" width="560"'
        f' style="display:block;max-width:100%;height:auto;border:1px solid {_GRID};'
        f'border-radius:6px">'
        f'</div>'
    )


def build_report_email(
    all_ig_leads: list[Lead],
    leads_curr: list[Lead],
    leads_prev: list[Lead],
    label_curr: str,
    label_prev: str,
    period_str: str,
) -> tuple[str, str, dict[str, bytes], str]:
    """Generate insights + charts + HTML.

    Returns (subject, html, {cid: png_bytes}, plain_text_fallback).
    """
    fc = lead_funnel(leads_curr)
    fp = lead_funnel(leads_prev)
    bd_curr = lost_reason_breakdown(leads_curr)
    ins = derive_insights(fc, fp, bd_curr, label_curr, label_prev)

    # ── Charts ────────────────────────────────────────────────────────────────
    images: dict[str, bytes] = {
        "chart_comparison": chart_period_comparison(fc, fp, label_curr, label_prev),
        "chart_period_status":  chart_status_funnel(leads_curr, f"Lead status — {label_curr}"),
        "chart_period_lost":    chart_lost_reasons(leads_curr, f"Lost reasons — {label_curr}"),
        "chart_overall_status": chart_status_funnel(all_ig_leads, "Lead status — all-time"),
        "chart_overall_lost":   chart_lost_reasons(all_ig_leads, "Lost reasons — all-time"),
    }

    # ── Per-exhibit takeaways (data-driven captions) ──────────────────────────
    spam_c       = _spam_count(bd_curr)
    spam_of_lost = _pct(spam_c, bd_curr.total_lost)
    lost_delta   = fc.lost_count - fp.lost_count
    tk_comparison = (
        f"Losses {'rose' if lost_delta > 0 else 'fell'} by {abs(lost_delta)} while wins "
        f"moved {fc.won_count - fp.won_count:+d} — the period swing at a glance."
    )
    tk_period_status = (
        f"{fc.won_count} won and {fc.lost_count} lost of {fc.total_all} leads — "
        f"{fc.conversion_rate:.0f}% conversion this period."
    )
    tk_period_lost = (
        f"{spam_c} of {bd_curr.total_lost} losses ({spam_of_lost:.0f}%) were spam/invalid."
        if bd_curr.has_data else "No lost leads recorded this period."
    )
    all_bd = lost_reason_breakdown(all_ig_leads)
    all_spam = _spam_count(all_bd)
    tk_overall_status = (
        f"Across all time, {lead_funnel(all_ig_leads).won_count} wins from "
        f"{len(all_ig_leads)} Instagram leads."
    )
    tk_overall_lost = (
        f"Spam is a structural problem: {all_spam} of {all_bd.total_lost} all-time "
        f"losses ({_pct(all_spam, all_bd.total_lost):.0f}%) were invalid."
        if all_bd.has_data else "No lost leads on record."
    )

    subject = f"[LIF CRM] Instagram Lead Report — {period_str}"

    html = f"""<!DOCTYPE html>
<html lang="en">
<body style="margin:0;padding:0;background:#f1f5f9">
<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;margin:0 auto;
            background:#ffffff;padding:30px 34px;color:{_BODY}">

  <p style="font-size:11px;text-transform:uppercase;letter-spacing:.09em;
             color:{_MUTED};margin:0 0 4px 0">Life in Frame CRM · Channel review</p>
  <h1 style="font-size:23px;font-weight:800;margin:0 0 3px 0;color:{_INK}">
    Instagram Lead Report
  </h1>
  <p style="font-size:13px;color:{_MUTED};margin:0 0 14px 0">
    {period_str} &nbsp;·&nbsp; vs {label_prev} &nbsp;·&nbsp; source: Instagram only
  </p>

  {_banner(ins)}
  {_findings_block(ins)}
  {_actions_block(ins)}

  <h2 style="font-size:13px;font-weight:800;letter-spacing:.06em;color:{_MUTED};
             text-transform:uppercase;margin:24px 0 10px 0">At a glance</h2>
  {_glance_table(fc, fp)}

  <div style="border-top:2px solid {_GRID};margin:26px 0 4px 0;padding-top:6px">
    <span style="font-size:11px;font-weight:800;letter-spacing:.06em;color:{_MUTED};
                 text-transform:uppercase">Supporting exhibits</span>
  </div>

  {_exhibit(1, tk_comparison,     "chart_comparison",     "Period comparison")}
  {_exhibit(2, tk_period_status,  "chart_period_status",  "Lead status this period")}
  {_exhibit(3, tk_period_lost,    "chart_period_lost",    "Lost reasons this period")}
  {_exhibit(4, tk_overall_status, "chart_overall_status", "Lead status all-time")}
  {_exhibit(5, tk_overall_lost,   "chart_overall_lost",   "Lost reasons all-time")}

  <p style="font-size:11px;color:{_MUTED};margin:28px 0 0 0;
            border-top:1px solid #f3f4f6;padding-top:13px">
    Generated automatically by Life in Frame CRM &mdash;
    <a href="https://lifcrm.netlify.app/leads" style="color:{_BLUE}">open the leads pipeline</a>.
    Figures cover leads whose source is Instagram, by enquiry date.
  </p>
</div>
</body>
</html>"""

    text = build_text_summary(ins, fc, fp, label_curr, label_prev)
    return subject, html, images, text
