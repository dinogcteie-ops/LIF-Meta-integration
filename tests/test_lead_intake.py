"""Tests for Google Sheet lead intake: header mapping, skips, and cursor dedup.

The Google Sheets call is stubbed via ``_open_worksheet`` so no network/creds
are needed; only the mapping + cursor logic is exercised.
"""
from app.services import lead_intake


class _FakeWS:
    def __init__(self, records):
        self._records = records

    def get_all_records(self):
        return list(self._records)


def _records():
    return [
        {"Timestamp": "6/5/2026 10:00:00", "Name": "Asha R",
         "Phone": "9000000001", "Event Type": "Wedding",
         "Event Date": "2026-12-01", "Message": "Looking for a package"},
        {"Timestamp": "6/5/2026 11:00:00", "Name": "",
         "Phone": "9000000002"},                       # skipped: no name
        {"Timestamp": "6/5/2026 12:00:00", "Name": "Bharat K",
         "Mobile": "9000000003", "Event": "Engagement"},  # alt header spellings
    ]


def test_intake_imports_maps_and_skips(db, monkeypatch):
    db.set_settings({"leads_intake_cursor": "0"})
    monkeypatch.setattr(lead_intake, "_open_worksheet", lambda: _FakeWS(_records()))

    summary = lead_intake.run_intake(db, dry_run=False)
    assert summary["new_rows"] == 3
    assert summary["imported"] == 2          # Asha + Bharat
    assert summary["skipped"] == 1           # blank name
    assert summary["cursor_after"] == 3

    by_name = {l.client_name: l for l in db.list_leads()}
    assert "Asha R" in by_name and "Bharat K" in by_name
    asha = by_name["Asha R"]
    assert asha.contact == "9000000001"
    assert asha.event_type == "Wedding"
    assert asha.source == "Google Form"
    assert str(asha.tentative_date) == "2026-12-01"
    # Alt header "Mobile" maps to contact; "Event" maps to event_type
    assert by_name["Bharat K"].contact == "9000000003"
    assert by_name["Bharat K"].event_type == "Engagement"


def test_intake_cursor_dedup(db, monkeypatch):
    db.set_settings({"leads_intake_cursor": "0"})
    monkeypatch.setattr(lead_intake, "_open_worksheet", lambda: _FakeWS(_records()))

    first = lead_intake.run_intake(db, dry_run=False)
    assert first["imported"] == 2

    # Second run over the same rows imports nothing (cursor advanced).
    second = lead_intake.run_intake(db, dry_run=False)
    assert second["new_rows"] == 0
    assert second["imported"] == 0
    assert second["cursor_after"] == 3


def test_intake_dry_run_writes_nothing(db, monkeypatch):
    db.set_settings({"leads_intake_cursor": "0"})
    monkeypatch.setattr(lead_intake, "_open_worksheet", lambda: _FakeWS(_records()))
    before = len(db.list_leads())

    summary = lead_intake.run_intake(db, dry_run=True)
    assert summary["dry_run"] is True
    assert summary["imported"] == 2          # would-import count
    assert summary["cursor_after"] == 0      # cursor not advanced
    assert len(db.list_leads()) == before    # no leads created
    assert int(db.get_settings_dict()["leads_intake_cursor"]) == 0
