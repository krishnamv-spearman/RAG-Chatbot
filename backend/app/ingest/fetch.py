"""Fetches the registered Groww pages and saves raw snapshots.

Layout: data/raw/<source_id>/<YYYY-MM-DD>.html plus <YYYY-MM-DD>.meta.json.
A failed fetch never overwrites the last good snapshot (edge cases ING-01, ING-10).
"""

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from app.ingest.normalize import utc_now_iso
from app.ingest.parse import extract_next_data
from app.registry import Source

logger = logging.getLogger("mf_faq.ingest")

USER_AGENT = "Mozilla/5.0 (compatible; MF-FAQ-Assistant/0.1; educational project)"
REQUEST_DELAY_SECONDS = 1.0
MAX_ATTEMPTS = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}
CHALLENGE_MARKERS = ("captcha", "cf-chl", "attention required", "are you a robot", "access denied")


@dataclass
class FetchResult:
    source_id: str
    ok: bool
    method: str | None = None  # "http" | "playwright"
    http_status: int | None = None
    html_path: str | None = None
    final_url: str | None = None
    error: str | None = None
    warning: str | None = None


def has_key_content(html: str) -> bool:
    """The page is usable if the embedded fund data is present, or at least the
    rendered fund-details labels are."""
    data = extract_next_data(html)
    if data and data.get("expense_ratio") is not None:
        return True
    lowered = html.lower()
    return "expense ratio" in lowered and "exit load" in lowered


def looks_like_challenge(html: str) -> bool:
    lowered = html[:20000].lower()
    return any(marker in lowered for marker in CHALLENGE_MARKERS)


def _http_get(client: httpx.Client, url: str) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.get(url)
            if response.status_code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                return response
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt == MAX_ATTEMPTS:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError(f"unreachable: {last_exc}")


def _render_with_browser(url: str) -> str:
    """Headless-browser fallback for pages whose data only appears after JavaScript runs."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed: pip install -e '.[ingest]'") from exc
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, wait_until="networkidle", timeout=45000)
            html = page.content()
            browser.close()
            return html
    except Exception as exc:  # browser binaries missing, timeouts, navigation errors
        raise RuntimeError(
            f"Browser render failed ({exc}). "
            "If browsers are missing, run: playwright install chromium"
        ) from exc


def _save_snapshot(raw_dir: Path, source: Source, html: str, meta: dict) -> Path:
    target_dir = raw_dir / source.id
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = meta["fetched_at"][:10]
    html_path = target_dir / f"{stamp}.html"
    html_path.write_text(html, encoding="utf-8")
    (target_dir / f"{stamp}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return html_path


def fetch_source(source: Source, client: httpx.Client, raw_dir: Path) -> FetchResult:
    result = FetchResult(source_id=source.id, ok=False)
    html: str | None = None

    if source.render in ("auto", "http"):
        try:
            response = _http_get(client, source.url)
        except httpx.HTTPError as exc:
            result.error = f"HTTP error: {exc}"
        else:
            result.http_status = response.status_code
            result.final_url = str(response.url)
            if result.final_url.rstrip("/") != source.url.rstrip("/"):
                # Edge case ING-04: keep citing the registry URL; flag for a manual check.
                result.warning = f"redirected to {result.final_url}; registry URL kept"
            if response.status_code == 200 and has_key_content(response.text):
                html, result.method = response.text, "http"
            elif response.status_code == 200 and looks_like_challenge(response.text):
                result.error = "bot challenge page returned"
            elif response.status_code != 200:
                result.error = f"HTTP {response.status_code}"
            else:
                result.error = "page returned without fund details"

    if html is None and source.render in ("auto", "js"):
        try:
            rendered = _render_with_browser(source.url)
        except RuntimeError as exc:
            result.error = f"{result.error + '; ' if result.error else ''}{exc}"
        else:
            if has_key_content(rendered) and not looks_like_challenge(rendered):
                html, result.method, result.error = rendered, "playwright", None
            else:
                # Keep the HTTP error (e.g. "HTTP 404"): it's usually the real cause.
                result.error = (f"{result.error + '; ' if result.error else ''}"
                                "rendered page has no fund details (possible bot challenge)")

    if html is None:
        logger.warning("fetch failed for %s: %s (last good snapshot kept)", source.id, result.error)
        return result

    meta = {
        "source_id": source.id,
        "url": source.url,
        "final_url": result.final_url or source.url,
        "fetched_at": utc_now_iso(),
        "http_status": result.http_status,
        "method": result.method,
        "bytes": len(html.encode("utf-8")),
        "raw_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
    }
    result.html_path = str(_save_snapshot(raw_dir, source, html, meta))
    result.ok = True
    return result


def fetch_all(
    sources: list[Source], raw_dir: Path, client: httpx.Client | None = None
) -> list[FetchResult]:
    own_client = client is None
    client = client or httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"},
        timeout=30.0,
        follow_redirects=True,
    )
    results = []
    try:
        for i, source in enumerate(sources):
            if i:
                time.sleep(REQUEST_DELAY_SECONDS)
            results.append(fetch_source(source, client, raw_dir))
    finally:
        if own_client:
            client.close()
    return results


def latest_snapshot(raw_dir: Path, source_id: str) -> tuple[Path, dict] | None:
    """Return the newest saved (html_path, meta) for a source, or None."""
    source_dir = raw_dir / source_id
    if not source_dir.is_dir():
        return None
    snapshots = sorted(source_dir.glob("*.html"))
    if not snapshots:
        return None
    html_path = snapshots[-1]
    meta_path = html_path.with_name(html_path.stem + ".meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return html_path, meta


def result_to_dict(result: FetchResult) -> dict:
    return asdict(result)
