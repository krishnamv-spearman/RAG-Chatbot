"""Text, number and date normalisation shared by ingestion (and later the validator)."""

import re
import unicodedata
from datetime import UTC, date, datetime, timedelta, timezone

# India has no daylight saving, so a fixed offset avoids needing the tzdata package on Windows.
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

_MISSING_MARKERS = {"", "-", "--", "---", "na", "n/a", "null", "none", "not available"}
_WHITESPACE = re.compile(r"\s+")


def clean_text(text: str | None) -> str:
    """NFKC-normalise, drop replacement characters and collapse whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text)).replace("�", " ")
    return _WHITESPACE.sub(" ", text).strip()


def is_missing(value) -> bool:
    """True for None and placeholder values such as '--' (edge case FMT-09: '--' is not zero)."""
    if value is None:
        return True
    if isinstance(value, str):
        return clean_text(value).lower() in _MISSING_MARKERS
    return False


def _strip_zeros(number: float, max_decimals: int = 2) -> str:
    text = f"{number:.{max_decimals}f}".rstrip("0").rstrip(".")
    return text or "0"


def format_percent(value) -> str | None:
    """'0.74' / 0.74 / '0.740%' -> '0.74%'."""
    if is_missing(value):
        return None
    try:
        number = float(str(value).replace("%", "").strip())
    except ValueError:
        return None
    return f"{_strip_zeros(number)}%"


def _indian_grouping(integer_part: str) -> str:
    """'10185606' -> '1,01,85,606' (Indian digit grouping)."""
    if len(integer_part) <= 3:
        return integer_part
    head, tail = integer_part[:-3], integer_part[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


def format_inr(amount, suffix: str = "", max_decimals: int = 2) -> str | None:
    """100 -> '₹100'; 101856.06 with suffix ' Cr' -> '₹1,01,856.06 Cr'.
    Use max_decimals=4 for NAVs, which are quoted to 3-4 decimal places."""
    if is_missing(amount):
        return None
    try:
        number = float(amount)
    except (TypeError, ValueError):
        return None
    text = _strip_zeros(number, max_decimals)
    integer_part, _, decimals = text.partition(".")
    sign = "-" if integer_part.startswith("-") else ""
    grouped = _indian_grouping(integer_part.lstrip("-"))
    return f"₹{sign}{grouped}{'.' + decimals if decimals else ''}{suffix}"


_DATE_FORMATS = ("%d-%b-%Y", "%Y-%m-%d", "%d %b %Y", "%d/%m/%Y", "%d-%m-%Y")


def parse_date(value) -> date | None:
    """Parse the date formats seen on Groww pages.

    Timestamps ending in 'Z' are UTC; they're converted to the IST calendar date
    (e.g. '2012-12-31T18:30:00.000Z' is 1 Jan 2013 in India).
    """
    if is_missing(value):
        return None
    text = clean_text(str(value))
    if "T" in text:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is not None:
            return dt.astimezone(IST).date()
        return dt.date()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def iso_date(value) -> str | None:
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else None


def display_date(iso: str | None) -> str | None:
    """'2026-11-04' -> '04 Nov 2026' (the footer format)."""
    if not iso:
        return None
    return date.fromisoformat(iso).strftime("%d %b %Y")


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
