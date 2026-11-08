from datetime import date

import pytest

from app.ingest.normalize import (
    clean_text,
    display_date,
    format_inr,
    format_percent,
    is_missing,
    parse_date,
)


def test_clean_text_collapses_whitespace_and_drops_bad_chars():
    assert clean_text("  022 �  66316333\n\t") == "022 66316333"
    assert clean_text("Ａ B") == "A B"  # NFKC: full-width A, non-breaking space
    assert clean_text(None) == ""


@pytest.mark.parametrize("value", [None, "", " -- ", "-", "NA", "n/a", "null"])
def test_missing_markers(value):
    assert is_missing(value)


@pytest.mark.parametrize("value", ["Nil", "0", 0, "0%", "Exit load of 1%"])
def test_real_values_are_not_missing(value):
    # FMT-09: "Nil" and zero are real values; only placeholders count as missing.
    assert not is_missing(value)


@pytest.mark.parametrize("raw, expected", [
    ("0.74", "0.74%"), (0.74, "0.74%"), ("1.00", "1%"), ("0.60", "0.6%"),
    ("0.740%", "0.74%"), ("--", None), ("abc", None),
])
def test_format_percent(raw, expected):
    assert format_percent(raw) == expected


@pytest.mark.parametrize("raw, kwargs, expected", [
    (100, {}, "₹100"),
    (1000, {}, "₹1,000"),
    (100000, {}, "₹1,00,000"),
    (101856.06, {"suffix": " Cr"}, "₹1,01,856.06 Cr"),
    (4497.0, {"suffix": " Cr"}, "₹4,497 Cr"),
    (226.045, {"max_decimals": 4}, "₹226.045"),
    (None, {}, None),
])
def test_format_inr_indian_grouping(raw, kwargs, expected):
    assert format_inr(raw, **kwargs) == expected


@pytest.mark.parametrize("raw, expected", [
    ("06-Nov-2026", date(2026, 11, 6)),
    ("2026-11-04T00:00:00", date(2026, 11, 4)),
    ("2013-01-01", date(2013, 1, 1)),
    # Midnight IST stored as UTC must map to the Indian calendar date.
    ("2012-12-31T18:30:00.000Z", date(2013, 1, 1)),
    ("Oct'26", None),
    (None, None),
])
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_display_date():
    assert display_date("2026-11-04") == "04 Nov 2026"
    assert display_date(None) is None
