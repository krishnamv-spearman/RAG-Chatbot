import json

from app.ingest.parse import REQUIRED_FIELDS, extract_next_data, parse_html
from tests.conftest import make_page

META = {"fetched_at": "2026-11-07T10:00:00+00:00"}


def parse(data, source, body=""):
    return parse_html(make_page(data, body), source, META, "HDFC Mid Cap Fund")


def test_real_page_has_all_required_fields(mid_cap_data, mid_cap_source):
    doc = parse(mid_cap_data, mid_cap_source)
    assert doc["extraction"] == "next_data"
    assert doc["missing_required"] == []
    assert set(REQUIRED_FIELDS) <= set(doc["fields"])
    assert doc["doc_date"] == "2026-11-06"


def test_real_page_values(mid_cap_data, mid_cap_source):
    f = parse(mid_cap_data, mid_cap_source)["fields"]
    assert f["expense_ratio"] == {"value": "0.74%", "as_on": "2026-11-04", "volatile": False}
    assert f["exit_load"]["value"] == "Exit load of 1% if redeemed within 1 year."
    assert f["min_sip"]["value"] == "₹100"
    assert f["benchmark"]["value"] == "NIFTY Midcap 150 Total Return Index"
    assert f["nav"]["value"] == "₹226.045"
    assert f["nav"]["volatile"] is True


def test_riskometer_uses_current_risk_not_launch_risk(mid_cap_data, mid_cap_source):
    # nfo_risk is the level at launch ("Moderately High"); the current level is "Very High".
    assert mid_cap_data["nfo_risk"] == "Moderately High"
    assert parse(mid_cap_data, mid_cap_source)["fields"]["riskometer"]["value"] == "Very High"


def test_riskometer_falls_back_to_meta_description(mid_cap_data, mid_cap_source):
    mid_cap_data["return_stats"] = []
    assert parse(mid_cap_data, mid_cap_source)["fields"]["riskometer"]["value"] == "Very High"


def test_performance_opinion_and_peer_data_never_reach_the_corpus(mid_cap_data, mid_cap_source):
    doc = parse(mid_cap_data, mid_cap_source)
    corpus_text = json.dumps(doc["sections"]).lower()
    # (Not checking the bare word "return": benchmark names contain "Total Return Index".)
    stats = mid_cap_data["return_stats"][0]
    for excluded in ("sharpe", "annualised", "contrarian", "invesco", "groww rating",
                     f"{stats['return3y']}", f"{stats['standard_deviation']}"):
        assert excluded not in corpus_text, excluded


def test_every_section_names_the_scheme(mid_cap_data, mid_cap_source):
    for section in parse(mid_cap_data, mid_cap_source)["sections"]:
        assert "HDFC Mid Cap Fund" in section["text"]


def test_placeholder_exit_load_is_missing_not_zero(mid_cap_data, mid_cap_source):
    # FMT-09: "--" must never become "no exit load".
    mid_cap_data["exit_load"] = "--"
    mid_cap_data["historic_exit_loads"] = []
    doc = parse(mid_cap_data, mid_cap_source)
    assert "exit_load" not in doc["fields"]
    assert "exit_load" in doc["missing_required"]


def test_nil_exit_load_is_a_real_value(mid_cap_data, mid_cap_source):
    mid_cap_data["exit_load"] = "Nil"
    exit_load = parse(mid_cap_data, mid_cap_source)["fields"]["exit_load"]
    assert exit_load["value"] == "Nil (no exit load)"


def test_all_null_lock_in_is_not_reported(mid_cap_data, mid_cap_source):
    # The page doesn't state a lock-in; don't invent "no lock-in".
    assert "lock_in" not in parse(mid_cap_data, mid_cap_source)["fields"]


def test_lock_in_when_present(mid_cap_data, mid_cap_source):
    mid_cap_data["lock_in"] = {"years": 3, "months": None, "days": None}
    assert parse(mid_cap_data, mid_cap_source)["fields"]["lock_in"]["value"] == "3 years"


def test_layout_change_without_embedded_data_reports_missing_fields(mid_cap_source):
    # ING-08: no __NEXT_DATA__ -> text fallback, every required field reported missing.
    body = "<h1>HDFC Mid Cap Fund</h1><p>" + "Some page text about the fund. " * 20 + "</p>"
    doc = parse(None, mid_cap_source, body=body)
    assert doc["extraction"] == "text_fallback"
    assert doc["missing_required"] == list(REQUIRED_FIELDS)


def test_content_hash_ignores_daily_nav_and_aum_changes(mid_cap_data, mid_cap_source):
    # ING-06: NAV/AUM change daily; that alone shouldn't count as a content change.
    before = parse(mid_cap_data, mid_cap_source)["content_hash"]
    mid_cap_data["nav"], mid_cap_data["aum"] = 230.5, 102000.0
    assert parse(mid_cap_data, mid_cap_source)["content_hash"] == before
    mid_cap_data["expense_ratio"] = "0.77"
    assert parse(mid_cap_data, mid_cap_source)["content_hash"] != before


def test_extract_next_data_handles_broken_json():
    assert extract_next_data('<script id="__NEXT_DATA__">{not json</script>') is None
    assert extract_next_data("<html></html>") is None
