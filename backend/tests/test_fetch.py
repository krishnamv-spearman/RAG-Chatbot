import json

import httpx
import pytest

from app.ingest import fetch
from app.ingest.fetch import fetch_source, latest_snapshot
from app.registry import Source
from tests.conftest import make_page


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(fetch.time, "sleep", lambda _s: None)


def client_returning(status: int, html: str, final_path: str | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if final_path and request.url.path != final_path:
            return httpx.Response(301, headers={"Location": final_path})
        return httpx.Response(status, text=html)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def source(render="http"):
    return Source(id="groww-hdfc-mid-cap",
                  url="https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth",
                  scheme="hdfc-mid-cap", source_type="groww_scheme_page", render=render)


def test_successful_fetch_saves_snapshot_and_meta(tmp_path, mid_cap_data):
    result = fetch_source(source(), client_returning(200, make_page(mid_cap_data)), tmp_path)
    assert result.ok and result.method == "http" and result.warning is None
    html_path, meta = latest_snapshot(tmp_path, "groww-hdfc-mid-cap")
    assert html_path.read_text(encoding="utf-8").startswith("<html>")
    assert meta["http_status"] == 200 and len(meta["raw_sha256"]) == 64
    assert meta["url"] == source().url


def test_http_error_keeps_last_good_snapshot(tmp_path, mid_cap_data):
    fetch_source(source(), client_returning(200, make_page(mid_cap_data)), tmp_path)
    good_path, _ = latest_snapshot(tmp_path, "groww-hdfc-mid-cap")
    good_html = good_path.read_text(encoding="utf-8")

    result = fetch_source(source(), client_returning(403, "Forbidden"), tmp_path)
    assert not result.ok and result.error == "HTTP 403"
    path, _ = latest_snapshot(tmp_path, "groww-hdfc-mid-cap")
    assert path.read_text(encoding="utf-8") == good_html  # ING-01 / ING-10


def test_bot_challenge_page_is_rejected(tmp_path):
    challenge = "<html><title>Attention Required!</title>Please complete the captcha</html>"
    result = fetch_source(source(), client_returning(200, challenge), tmp_path)
    assert not result.ok and "challenge" in result.error
    assert latest_snapshot(tmp_path, "groww-hdfc-mid-cap") is None


def test_page_without_fund_details_is_rejected(tmp_path):
    result = fetch_source(source(), client_returning(200, make_page(None, "<p>Loading…</p>")),
                          tmp_path)
    assert not result.ok and "without fund details" in result.error


def test_auto_mode_falls_back_to_browser(tmp_path, mid_cap_data, monkeypatch):
    monkeypatch.setattr(fetch, "_render_with_browser", lambda url: make_page(mid_cap_data))
    shell = make_page(None, "<div id='root'></div>")
    result = fetch_source(source("auto"), client_returning(200, shell), tmp_path)
    assert result.ok and result.method == "playwright"


def test_auto_mode_reports_both_failures(tmp_path, monkeypatch):
    def broken(url):
        raise RuntimeError("Browser render failed")

    monkeypatch.setattr(fetch, "_render_with_browser", broken)
    result = fetch_source(source("auto"), client_returning(503, "down"), tmp_path)
    assert not result.ok
    assert "HTTP 503" in result.error and "Browser render failed" in result.error


def test_redirect_is_flagged_but_registry_url_kept(tmp_path, mid_cap_data):
    # ING-04
    client = client_returning(200, make_page(mid_cap_data), final_path="/mutual-funds/new-slug")
    result = fetch_source(source(), client, tmp_path)
    assert result.ok and "redirected" in result.warning
    _, meta = latest_snapshot(tmp_path, "groww-hdfc-mid-cap")
    assert meta["url"] == source().url
    assert meta["final_url"].endswith("/mutual-funds/new-slug")


def test_latest_snapshot_picks_newest(tmp_path):
    d = tmp_path / "groww-hdfc-mid-cap"
    d.mkdir()
    for day in ("2026-11-01", "2026-11-07", "2026-11-03"):
        (d / f"{day}.html").write_text(day, encoding="utf-8")
        (d / f"{day}.meta.json").write_text(json.dumps({"fetched_at": day}), encoding="utf-8")
    path, meta = latest_snapshot(tmp_path, "groww-hdfc-mid-cap")
    assert path.name == "2026-11-07.html" and meta["fetched_at"] == "2026-11-07"
