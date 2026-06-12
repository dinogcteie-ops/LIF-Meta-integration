"""Ad-performance LLM analysis tests — fake transport, aggregate-only prompts."""
from datetime import date, timedelta

from app.services import ad_analysis, llm
from app.services.lead_report import build_report_email, default_period, previous_period


def _seed_metrics(db):
    today = date.today()
    rows = []
    for offset in range(1, 31):                      # 30 days back from yesterday
        d = today - timedelta(days=offset)
        rows.append({"campaign_id": "c1", "campaign_name": "Wedding Leads",
                     "date": d, "spend": 100.0, "impressions": 1000,
                     "reach": 800, "clicks": 50, "leads": 2, "cpl": 50.0,
                     "currency": "INR"})
    db.replace_meta_metrics(rows)


def test_window_math():
    start, end = default_period(date(2026, 6, 16))
    assert (end - start).days == 14 and end == date(2026, 6, 15)
    p_start, p_end = previous_period(start, end)
    assert p_end == start - timedelta(days=1)
    assert (p_end - p_start).days == 14


def test_gather_aggregates_campaign_sums_and_cpl(db):
    _seed_metrics(db)
    start, end = default_period()
    p_start, p_end = previous_period(start, end)
    agg = ad_analysis.gather_ad_aggregates(db, start, end, p_start, p_end)
    cur = agg["campaigns_current"]["Wedding Leads"]
    assert cur["spend"] == 1500.0           # 15 days × 100
    assert cur["leads"] == 30
    assert cur["cpl"] == 50.0
    assert "financials_inr" in agg and "funnel_current" in agg


def test_cpl_zero_guard(db):
    db.replace_meta_metrics([{
        "campaign_id": "c2", "campaign_name": "Zero Leads",
        "date": date.today() - timedelta(days=2), "spend": 500.0,
        "impressions": 10, "reach": 5, "clicks": 1, "leads": 0,
        "cpl": 0, "currency": "INR"}])
    start, end = default_period()
    agg = ad_analysis.gather_ad_aggregates(db, start, end,
                                           *previous_period(start, end))
    assert agg["campaigns_current"]["Zero Leads"]["cpl"] == 0.0


def test_prompt_contains_no_pii(db, monkeypatch):
    lead = db.create_lead(client_name="Very Private Person",
                          contact="+91 90000 11111")
    _seed_metrics(db)
    captured = {}

    def _fake_complete(db_, prompt, system="", max_tokens=0):
        captured["prompt"] = prompt
        return "analysis text"
    monkeypatch.setattr(llm, "complete", _fake_complete)
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    start, end = default_period()
    text = ad_analysis.analysis_or_fallback(
        db, start, end, *previous_period(start, end), "cur", "prev")
    assert text == "analysis text"
    assert "Very Private Person" not in captured["prompt"]
    assert "90000" not in captured["prompt"]


def test_fallback_on_llm_error(db, monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: True)

    def _boom(*a, **k):
        raise llm.LLMError("down")
    monkeypatch.setattr(llm, "complete", _boom)
    start, end = default_period()
    out = ad_analysis.analysis_or_fallback(
        db, start, end, *previous_period(start, end), "cur", "prev")
    assert out is None


def test_unconfigured_returns_none(db, monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: False)
    start, end = default_period()
    assert ad_analysis.analysis_or_fallback(
        db, start, end, *previous_period(start, end), "cur", "prev") is None


def test_latest_analysis_stored_and_read(db, monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "stored narrative")
    start, end = default_period()
    ad_analysis.analysis_or_fallback(
        db, start, end, *previous_period(start, end), "1–15 Jun", "prev")
    latest = ad_analysis.latest_analysis(db)
    assert latest and latest["text"] == "stored narrative"
    assert latest["period"] == "1–15 Jun"


def test_report_email_with_and_without_ai_text(db):
    leads = db.list_leads()
    args = (leads, leads, leads, "cur", "prev", "cur 2026")
    _, html_plain, _, _ = build_report_email(*args)
    assert "AI analysis" not in html_plain
    _, html_ai, _, _ = build_report_email(*args, ai_analysis="Spend <up> & fine")
    assert "AI analysis" in html_ai
    assert "&lt;up&gt; &amp;" in html_ai     # escaped


def test_meta_page_renders_with_analysis_card(client, db):
    r = client.get("/meta")
    assert r.status_code == 200
    assert "AI analysis" in r.text


def test_report_preview_renders_in_browser(client, db, monkeypatch):
    import app.routes.jobs as jobs
    monkeypatch.setattr(jobs, "analysis_or_fallback",
                        lambda *a, **k: "AI says: campaign B's CPL improved.")
    r = client.get("/jobs/lead-report/preview")
    assert r.status_code == 200
    assert "Instagram Lead Report" in r.text
    assert "data:image/png;base64" in r.text          # charts inlined for browser
    assert "cid:" not in r.text                         # no email-only refs leak
    assert "campaign B's CPL improved" in r.text        # AI section rendered


def test_report_preview_requires_login(db):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as anon:                       # not logged in
        r = anon.get("/jobs/lead-report/preview", follow_redirects=False)
    assert r.status_code == 403
