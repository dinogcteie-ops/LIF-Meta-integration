"""Tests for Google Sheet lead intake: header mapping, phone dedup, dry-run.

Each test uses a distinct phone-number range to avoid cross-test contamination
(the test DB is session-scoped, so rows created by one test persist for others).
"""
from app.services import lead_intake


class _FakeWS:
    def __init__(self, records, title="Sheet1"):
        self._records = records
        self.title = title

    def get_all_records(self):
        return list(self._records)


class _FakeSpreadsheet:
    def __init__(self, *worksheets):
        self._sheets = list(worksheets)

    def worksheets(self):
        return list(self._sheets)


def _patch_sheet(monkeypatch, *worksheets):
    monkeypatch.setattr(lead_intake, "_open_spreadsheet",
                        lambda: _FakeSpreadsheet(*worksheets))


# ── Generic Google-Form format tests (phone prefix 901x) ─────────────────────

def _form_records(prefix="9010"):
    return [
        {"Timestamp": "6/5/2026 10:00:00", "Name": "Asha R",
         "Phone": f"{prefix}00001", "Event Type": "Wedding",
         "Event Date": "2026-12-01", "Message": "Looking for a package"},
        {"Timestamp": "6/5/2026 11:00:00", "Name": "",
         "Phone": f"{prefix}00002"},                  # skipped: no name
        {"Timestamp": "6/5/2026 12:00:00", "Name": "Bharat K",
         "Mobile": f"{prefix}00003", "Event": "Engagement"},
    ]


def test_intake_imports_maps_and_skips(db, monkeypatch):
    records = _form_records("9011")
    _patch_sheet(monkeypatch, _FakeWS(records, "Sheet1"))

    summary = lead_intake.run_intake(db, dry_run=False)
    assert summary["total_rows"] == 3
    assert summary["imported"] == 2          # Asha + Bharat
    assert summary["skipped"] == 1           # blank name

    by_name = {l.client_name: l for l in db.list_leads()}
    assert "Asha R" in by_name and "Bharat K" in by_name
    asha = by_name["Asha R"]
    assert "901100001" in asha.contact
    assert asha.event_type == "Wedding"
    assert str(asha.tentative_date) == "2026-12-01"
    assert "901100003" in by_name["Bharat K"].contact
    assert by_name["Bharat K"].event_type == "Engagement"


def test_intake_phone_dedup(db, monkeypatch):
    records = _form_records("9012")
    _patch_sheet(monkeypatch, _FakeWS(records, "Sheet1"))

    first = lead_intake.run_intake(db, dry_run=False)
    assert first["imported"] == 2

    # Second run: same phones already in DB → skipped.
    second = lead_intake.run_intake(db, dry_run=False)
    assert second["imported"] == 0
    assert second["skipped"] == 3   # blank-name + 2 duplicate phones


def test_intake_dry_run_writes_nothing(db, monkeypatch):
    records = _form_records("9013")
    _patch_sheet(monkeypatch, _FakeWS(records, "Sheet1"))
    before = len(db.list_leads())

    summary = lead_intake.run_intake(db, dry_run=True)
    assert summary["dry_run"] is True
    assert summary["imported"] == 2
    assert len(db.list_leads()) == before    # no leads created


# ── Meta Lead Ads export format tests (phone prefix 902x) ────────────────────

def _meta_records(prefix="9021"):
    return [
        {
            "id": f"l:{prefix}111", "full_name": "Priya S",
            "phone_number": f"p:+91{prefix}0010",
            "what's_your_wedding_date?": "15/12/2026",
            "_what_are_you_looking_for?": "bridal_photography",
            "what's_your_approximate_wedding_photography_budget?": "₹1,00,000_–_₹1,50,000",
            "city": "Hyderabad", "platform": "ig", "is_organic": "false",
            "campaign_name": "Bridal Campaign",
        },
        {
            "id": f"l:{prefix}222", "full_name": "Ravi T",
            "phone_number": f"p:+91{prefix}0011",
            "what's_your_wedding_date?": "",
            "_what_are_you_looking_for?": "couple_shoot",
            "what's_your_approximate_wedding_photography_budget?": "₹1,50,000_&_above",
            "city": "Chennai", "platform": "fb", "is_organic": "false",
            "campaign_name": "Bridal Campaign",
        },
    ]


def test_intake_meta_export_format(db, monkeypatch):
    """Meta Lead Ads export columns are correctly mapped."""
    records = _meta_records("9021")
    _patch_sheet(monkeypatch, _FakeWS(records, "Sheet1"))

    summary = lead_intake.run_intake(db, dry_run=False)
    assert summary["imported"] == 2

    by_name = {l.client_name: l for l in db.list_leads()}
    priya = by_name["Priya S"]
    assert "90210010" in priya.contact
    assert priya.event_type == "Wedding"       # bridal_photography → Wedding
    assert str(priya.tentative_date) == "2026-12-15"
    assert "Hyderabad" in (priya.notes or "")
    assert priya.meta_campaign is True
    assert priya.source == "Instagram"         # platform=ig

    ravi = by_name["Ravi T"]
    assert ravi.event_type == "Portrait"       # couple_shoot → Portrait
    assert ravi.tentative_date is None
    assert "Chennai" in (ravi.notes or "")
    assert ravi.source == "Meta"               # platform=fb


def test_intake_meta_lead_id_dedup(db, monkeypatch):
    """Same Meta lead ID on second run is skipped."""
    records = _meta_records("9022")
    _patch_sheet(monkeypatch, _FakeWS(records, "Sheet1"))

    lead_intake.run_intake(db, dry_run=False)
    second = lead_intake.run_intake(db, dry_run=False)
    assert second["imported"] == 0
    assert second["skipped"] == 2


# ── Multi-tab tests (phone prefix 903x) ──────────────────────────────────────

def _tab_records(prefix, name_prefix):
    return [
        {"full_name": f"{name_prefix} A", "phone_number": f"p:+91{prefix}0001",
         "city": "Pune", "platform": "ig", "is_organic": "false"},
        {"full_name": f"{name_prefix} B", "phone_number": f"p:+91{prefix}0002",
         "city": "Pune", "platform": "ig", "is_organic": "false"},
    ]


def test_intake_multi_tab_all_processed_first_run(db, monkeypatch):
    """First run with two tabs processes all tabs."""
    ws1 = _FakeWS(_tab_records("9031", "Tab1"), "T1 Mar 2026")
    ws2 = _FakeWS(_tab_records("9032", "Tab2"), "T1 Apr 2026")
    _patch_sheet(monkeypatch, ws1, ws2)

    summary = lead_intake.run_intake(db, dry_run=False)
    assert summary["tabs_processed"] == 2
    assert summary["imported"] == 4


def test_intake_multi_tab_done_tab_skipped(db, monkeypatch):
    """A tab that was marked done is skipped on the next run (not the latest)."""
    ws1 = _FakeWS(_tab_records("9033", "Old"), "T2 May 2026")
    ws2 = _FakeWS(_tab_records("9034", "New"), "T2 Jun 2026")
    _patch_sheet(monkeypatch, ws1, ws2)

    # First run: both tabs processed; May is marked done (not the latest tab).
    first = lead_intake.run_intake(db, dry_run=False)
    assert first["tabs_processed"] == 2
    assert first["imported"] == 4

    # Second run with the same two tabs: May is done → skip it.
    # Jun is latest → still rescanned (but all phones already imported).
    second = lead_intake.run_intake(db, dry_run=False)
    assert second["tabs_processed"] == 1           # only Jun
    assert second["tabs"][0]["tab"] == "T2 Jun 2026"
    assert second["imported"] == 0                 # dedup catches everything
