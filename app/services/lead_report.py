"""Instagram lead report — matplotlib charts embedded as CID images in HTML email.

Scheduled on the 1st and 16th of each month via POST /jobs/lead-report (Netlify
cron).  Can also be triggered from the Settings page with a custom date range.
Only leads whose source == "Instagram" are included in every chart.
"""
from __future__ import annotations

import io
import textwrap
from datetime import date, timedelta

import matplotlib
matplotlib.use("Agg")   # non-interactive backend — must precede pyplot import
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from app.domain import Lead
from app.services.reports import (
    LeadFunnel,
    filter_leads,
    lead_funnel,
    lost_reason_breakdown,
)

# ─── Constants ────────────────────────────────────────────────────────────────

SOURCE = "Instagram"

_STATUS_ORDER  = ["new", "quoted", "won", "lost", "cold"]
_STATUS_LABELS = ["New", "Quoted", "Won", "Lost", "Cold"]
_STATUS_COLORS = {
    "new":    "#3b82f6",
    "quoted": "#f59e0b",
    "won":    "#22c55e",
    "lost":   "#ef4444",
    "cold":   "#94a3b8",
}


# ─── Date helpers ─────────────────────────────────────────────────────────────

def default_period(today: date | None = None) -> tuple[date, date]:
    """Standard 15-day window ending yesterday.

    Called on the 1st  → end = May 31, start = May 17  (last half of prev month).
    Called on the 16th → end = Jun 15, start = Jun  1  (first half of this month).
    """
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


# ─── Chart generators ─────────────────────────────────────────────────────────

def _save_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=110, facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def _base_ax(figsize: tuple) -> tuple:
    fig, ax = plt.subplots(figsize=figsize)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#f9fafb")
    return fig, ax


def chart_status_funnel(leads: list[Lead], title: str) -> bytes:
    """Horizontal bar chart of lead counts by status."""
    f = lead_funnel(leads)
    counts = [f.new_count, f.quoted_count, f.won_count, f.lost_count, f.cold_count]
    colors = [_STATUS_COLORS[s] for s in _STATUS_ORDER]
    total  = sum(counts) or 1

    fig, ax = _base_ax((5.5, 2.8))
    ys = list(range(len(_STATUS_LABELS)))
    ax.barh(ys, counts, color=colors, height=0.55, edgecolor="none")

    for i, cnt in enumerate(counts):
        pct   = cnt / total * 100
        label = f"  {cnt}  ({pct:.0f}%)" if cnt > 0 else "  0"
        ax.text(cnt + 0.08, i, label, va="center", ha="left",
                fontsize=8.5, color="#374151")

    ax.set_yticks(ys)
    ax.set_yticklabels(_STATUS_LABELS, fontsize=9, color="#374151")
    ax.set_xlim(0, max(counts or [1]) * 1.45 + 1)
    ax.set_xlabel("Leads", fontsize=8.5, color="#9ca3af")
    ax.set_title(title, fontsize=10.5, fontweight="bold", pad=8, color="#111827")
    ax.tick_params(axis="x", labelsize=8, colors="#9ca3af")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#e5e7eb")
    ax.xaxis.grid(True, color="#e5e7eb", linewidth=0.5, linestyle="--")
    ax.set_axisbelow(True)
    fig.tight_layout(pad=0.8)
    return _save_png(fig)


def chart_lost_reasons(leads: list[Lead], title: str) -> bytes:
    """Horizontal bar chart of lost-reason counts."""
    bd = lost_reason_breakdown(leads)

    if not bd.has_data:
        fig, ax = _base_ax((5.5, 1.8))
        ax.axis("off")
        ax.set_title(title, fontsize=10.5, fontweight="bold", pad=8, color="#111827")
        ax.text(0.5, 0.4, "No lost leads in this period",
                ha="center", va="center", fontsize=10, color="#9ca3af",
                transform=ax.transAxes)
        fig.tight_layout(pad=0.8)
        return _save_png(fig)

    labels = [textwrap.shorten(r.reason, width=34, placeholder="…") for r in bd.rows]
    counts = [r.count for r in bd.rows]
    n      = len(labels)
    h      = max(2.2, n * 0.43 + 0.9)

    fig, ax = _base_ax((5.5, h))
    ys   = list(range(n))
    ax.barh(ys, counts, color="#ef4444", height=0.55, edgecolor="none", alpha=0.82)

    for i, cnt in enumerate(counts):
        ax.text(cnt + 0.05, i, f"  {cnt}", va="center", ha="left",
                fontsize=8.5, color="#374151")

    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontsize=8.5, color="#374151")
    ax.set_xlim(0, max(counts or [1]) * 1.38 + 0.5)
    ax.set_xlabel("Lost leads", fontsize=8.5, color="#9ca3af")
    ax.set_title(title, fontsize=10.5, fontweight="bold", pad=8, color="#111827")
    ax.tick_params(axis="x", labelsize=8, colors="#9ca3af")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#e5e7eb")
    ax.xaxis.grid(True, color="#e5e7eb", linewidth=0.5, linestyle="--")
    ax.set_axisbelow(True)
    fig.tight_layout(pad=0.8)
    return _save_png(fig)


def chart_period_comparison(
    leads_curr: list[Lead],
    leads_prev: list[Lead],
    label_curr: str,
    label_prev: str,
) -> bytes:
    """Grouped bar chart — this 15-day period vs the previous one."""
    fc = lead_funnel(leads_curr)
    fp = lead_funnel(leads_prev)
    curr = [fc.new_count, fc.quoted_count, fc.won_count, fc.lost_count, fc.cold_count]
    prev = [fp.new_count, fp.quoted_count, fp.won_count, fp.lost_count, fp.cold_count]

    x = list(range(len(_STATUS_ORDER)))
    w = 0.36
    colors = [_STATUS_COLORS[s] for s in _STATUS_ORDER]

    fig, ax = _base_ax((6.2, 3.6))

    # Previous period — faded (same hue, low alpha)
    ax.bar([xi - w / 2 for xi in x], prev, w,
           color=colors, alpha=0.30, edgecolor="none")
    # Current period — full color
    ax.bar([xi + w / 2 for xi in x], curr, w,
           color=colors, alpha=0.88, edgecolor="none")

    # Delta annotation above each current bar
    for xi, (c, p) in enumerate(zip(curr, prev)):
        d = c - p
        if d == 0:
            txt, clr = "=", "#9ca3af"
        else:
            txt, clr = f"{d:+d}", ("#16a34a" if d > 0 else "#dc2626")
        ax.text(xi + w / 2, c + 0.06, txt, ha="center", va="bottom",
                fontsize=8, fontweight="bold", color=clr)

    ax.set_xticks(x)
    ax.set_xticklabels(_STATUS_LABELS, fontsize=9)
    ax.set_ylabel("Leads", fontsize=8.5, color="#9ca3af")
    ax.set_title("Period Comparison — Instagram Leads",
                 fontsize=10.5, fontweight="bold", pad=8, color="#111827")
    ax.tick_params(axis="both", labelsize=8.5, colors="#6b7280")
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["bottom", "left"]].set_color("#e5e7eb")
    ax.yaxis.grid(True, color="#e5e7eb", linewidth=0.5, linestyle="--")
    ax.set_axisbelow(True)
    peak = max(max(curr or [0]), max(prev or [0]), 1)
    ax.set_ylim(0, peak * 1.45)

    legend_patches = [
        mpatches.Patch(color="#94a3b8", alpha=0.30, label=label_prev),
        mpatches.Patch(color="#94a3b8", alpha=0.88, label=label_curr),
    ]
    ax.legend(handles=legend_patches, fontsize=8, frameon=False, loc="upper right")

    fig.tight_layout(pad=0.8)
    return _save_png(fig)


# ─── Text comparison summary ──────────────────────────────────────────────────

def _delta(curr: int, prev: int) -> str:
    d = curr - prev
    if d == 0:
        return "(no change)"
    pct = f", {d / prev * 100:+.0f}%" if prev else ""
    return f"({d:+d}{pct})"


def build_text_summary(
    fc: LeadFunnel, fp: LeadFunnel, label_curr: str, label_prev: str
) -> str:
    lines = [
        f"Instagram Lead Report: {label_curr}  vs  {label_prev}",
        "",
        f"Total leads    {fc.total_all:>3}   {_delta(fc.total_all,    fp.total_all)}",
        f"  New          {fc.new_count:>3}   {_delta(fc.new_count,    fp.new_count)}",
        f"  Quoted       {fc.quoted_count:>3}   {_delta(fc.quoted_count, fp.quoted_count)}",
        f"  Won          {fc.won_count:>3}   {_delta(fc.won_count,    fp.won_count)}",
        f"  Lost         {fc.lost_count:>3}   {_delta(fc.lost_count,   fp.lost_count)}",
        f"  Cold         {fc.cold_count:>3}   {_delta(fc.cold_count,   fp.cold_count)}",
        "",
        f"Conversion     {fc.conversion_rate:.1f}%  (was {fp.conversion_rate:.1f}%)",
        f"Pipeline       ₹{fc.total_pipeline_value:.2f}L  (was ₹{fp.total_pipeline_value:.2f}L)",
    ]
    return "\n".join(lines)


# ─── HTML email builder ───────────────────────────────────────────────────────

def _summary_html(fc: LeadFunnel, fp: LeadFunnel,
                  label_curr: str, label_prev: str) -> str:
    def _row(label: str, curr: int | float, prev: int | float,
             fmt: str = "d") -> str:
        if isinstance(curr, float):
            d = curr - prev
            sign = f"{d:+.1f}%" if d != 0 else "="
            cur_str  = f"{curr:.1f}%"
            prev_str = f"{prev:.1f}%"
        else:
            d = int(curr) - int(prev)
            sign = f"{d:+d}" if d != 0 else "="
            cur_str  = str(int(curr))
            prev_str = str(int(prev))
        clr = "#16a34a" if d > 0 else ("#dc2626" if d < 0 else "#9ca3af")
        return (
            f"<tr>"
            f"<td style='padding:3px 12px 3px 0;color:#6b7280;font-size:13px'>{label}</td>"
            f"<td style='padding:3px 8px;font-weight:600;text-align:right;font-size:13px'>{cur_str}</td>"
            f"<td style='padding:3px 8px;text-align:right;font-size:12px;color:{clr};font-weight:700'>{sign}</td>"
            f"<td style='padding:3px 0 3px 8px;color:#9ca3af;font-size:12px'>was {prev_str}</td>"
            f"</tr>"
        )

    rows = (
        _row("Total leads",  fc.total_all,       fp.total_all)
        + _row("→ New",      fc.new_count,        fp.new_count)
        + _row("→ Quoted",   fc.quoted_count,     fp.quoted_count)
        + _row("→ Won",      fc.won_count,        fp.won_count)
        + _row("→ Lost",     fc.lost_count,       fp.lost_count)
        + _row("→ Cold",     fc.cold_count,       fp.cold_count)
        + "<tr><td colspan='4' style='padding:5px 0 0;border-top:1px solid #e5e7eb'></td></tr>"
        + _row("Conversion rate", fc.conversion_rate, fp.conversion_rate)
    )

    return f"""
<div style="background:#f8fafc;border-left:4px solid #3b82f6;border-radius:4px;
            padding:14px 18px;margin:16px 0 20px 0">
  <p style="font-size:12px;color:#6b7280;margin:0 0 8px 0">
    <strong style="color:#111827">{label_curr}</strong>
    &nbsp;vs&nbsp;
    <strong style="color:#374151">{label_prev}</strong>
  </p>
  <table style="border-collapse:collapse;font-family:Arial,sans-serif">{rows}</table>
</div>
"""


def _section(heading: str, cid: str, alt: str) -> str:
    return (
        f'<h2 style="font-size:14px;font-weight:600;color:#374151;margin:22px 0 6px 0;'
        f'border-bottom:1px solid #f0f0f0;padding-bottom:5px">{heading}</h2>'
        f'<img src="cid:{cid}" alt="{alt}" width="550"'
        f' style="display:block;max-width:100%;height:auto;margin:0 0 4px 0">'
    )


def build_report_email(
    all_ig_leads: list[Lead],
    leads_curr: list[Lead],
    leads_prev: list[Lead],
    label_curr: str,
    label_prev: str,
    period_str: str,
) -> tuple[str, str, dict[str, bytes]]:
    """Generate charts and HTML. Returns (subject, html, {cid: png_bytes})."""

    images: dict[str, bytes] = {
        "chart_overall_status": chart_status_funnel(
            all_ig_leads, "Lead Status — All Instagram Leads"
        ),
        "chart_period_status": chart_status_funnel(
            leads_curr, f"Lead Status — {label_curr}"
        ),
        "chart_overall_lost": chart_lost_reasons(
            all_ig_leads, "Lost Reasons — All Time"
        ),
        "chart_period_lost": chart_lost_reasons(
            leads_curr, f"Lost Reasons — {label_curr}"
        ),
        "chart_comparison": chart_period_comparison(
            leads_curr, leads_prev, label_curr, label_prev
        ),
    }

    fc = lead_funnel(leads_curr)
    fp = lead_funnel(leads_prev)

    subject = f"[LIF CRM] Instagram Lead Report — {period_str}"

    html = f"""<!DOCTYPE html>
<html lang="en">
<body style="margin:0;padding:0;background:#f1f5f9">
<div style="font-family:Arial,Helvetica,sans-serif;max-width:620px;margin:0 auto;
            background:#ffffff;padding:28px 32px;color:#1f2937">

  <p style="font-size:11px;text-transform:uppercase;letter-spacing:.08em;
             color:#9ca3af;margin:0 0 4px 0">Life in Frame CRM</p>
  <h1 style="font-size:22px;font-weight:700;margin:0 0 3px 0;color:#111827">
    Instagram Lead Report
  </h1>
  <p style="font-size:13px;color:#6b7280;margin:0 0 18px 0">
    Report period: {period_str}
    &nbsp;·&nbsp; Source filter: Instagram only
  </p>

  {_summary_html(fc, fp, label_curr, label_prev)}

  {_section("1. Overall Lead Status — All Instagram Leads",
            "chart_overall_status", "Overall lead status")}
  {_section(f"2. Lead Status — {label_curr}",
            "chart_period_status",  "Period lead status")}
  {_section("3. Lost Reason Breakdown — All Time",
            "chart_overall_lost",   "Overall lost reasons")}
  {_section(f"4. Lost Reason Breakdown — {label_curr}",
            "chart_period_lost",    "Period lost reasons")}
  {_section("5. Period Comparison",
            "chart_comparison",     "Period comparison")}

  <p style="font-size:11px;color:#9ca3af;margin:24px 0 0 0;
            border-top:1px solid #f3f4f6;padding-top:12px">
    Sent automatically by Life in Frame CRM &mdash;
    <a href="https://lifcrm.netlify.app/leads" style="color:#3b82f6">
      open leads</a>
  </p>
</div>
</body>
</html>"""

    return subject, html, images
