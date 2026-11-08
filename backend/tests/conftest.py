import copy
import json
from pathlib import Path

import pytest

from app.registry import Source

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def mid_cap_data() -> dict:
    """Trimmed copy of the real embedded data from the HDFC Mid Cap Groww page."""
    with (FIXTURES / "groww_mid_cap_data.json").open(encoding="utf-8") as f:
        return copy.deepcopy(json.load(f))


def make_page(data: dict | None, body: str = "") -> str:
    """Wrap scheme data the way Groww embeds it (Next.js __NEXT_DATA__ script)."""
    script = ""
    if data is not None:
        payload = {"props": {"pageProps": {"mfServerSideData": data}}}
        script = (f'<script id="__NEXT_DATA__" type="application/json">'
                  f"{json.dumps(payload)}</script>")
    return f"<html><head><title>Groww</title></head><body>{body}{script}</body></html>"


@pytest.fixture
def mid_cap_source() -> Source:
    return Source(
        id="groww-hdfc-mid-cap",
        url="https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth",
        scheme="hdfc-mid-cap",
        source_type="groww_scheme_page",
    )
