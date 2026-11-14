"""In-app refresh (deployment): the Refresher job and the /api/admin/refresh endpoints."""

import json
import shutil
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.ingest import fetch as fetch_module
from app.ingest.catalog import FileCatalog
from app.ingest.fetch import FetchResult
from app.ingest.refresh import Refresher, RefreshJob
from app.main import create_app
from app.vectorstore import ChromaStore
from tests.conftest import make_page
from tests.fakes import HashingEmbedder

REAL_DATA = Path(__file__).resolve().parents[2] / "data"
MID_SOURCE = """- id: groww-hdfc-mid-cap
  url: https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth
  scheme: hdfc-mid-cap
  source_type: groww_scheme_page
  render: http
"""


@pytest.fixture
def data_dir(tmp_path):
    shutil.copy(REAL_DATA / "schemes.yaml", tmp_path)
    (tmp_path / "sources.yaml").write_text(MID_SOURCE, encoding="utf-8")
    return tmp_path


@pytest.fixture
def fake_fetch(monkeypatch, mid_cap_data):
    """Replace the network fetch: saves a snapshot dated today; `state` controls it."""
    state = {"ok": True, "expense": "0.74"}

    def fetch_all(sources, raw_dir, client=None):
        out = []
        for source in sources:
            if not state["ok"]:
                out.append(FetchResult(source_id=source.id, ok=False, error="HTTP 503"))
                continue
            data = dict(mid_cap_data, expense_ratio=state["expense"],
                        nav_date=date.today().strftime("%d-%b-%Y"))
            folder = raw_dir / source.id
            folder.mkdir(parents=True, exist_ok=True)
            stamp = date.today().isoformat()
            (folder / f"{stamp}.html").write_text(make_page(data), encoding="utf-8")
            (folder / f"{stamp}.meta.json").write_text(
                json.dumps({"fetched_at": f"{stamp}T05:00:00+00:00"}), encoding="utf-8")
            out.append(FetchResult(source_id=source.id, ok=True, method="http"))
        return out

    monkeypatch.setattr(fetch_module, "fetch_all", fetch_all)
    return state


def make_refresher(data_dir, **kw):
    store, catalog = ChromaStore(data_dir), FileCatalog(data_dir)
    calls = []
    refresher = Refresher(data_dir, store, catalog, HashingEmbedder(), "fake-model", 0.1,
                          on_activated=lambda: calls.append("reloaded"), evaluate=False, **kw)
    return refresher, catalog, calls


def test_refresh_activates_new_data_and_reloads(data_dir, fake_fetch):
    refresher, catalog, calls = make_refresher(data_dir)
    job, started = refresher.start(wait=True)
    assert started and job.state == "succeeded", job.problems
    assert catalog.active()["version"] == job.version and calls == ["reloaded"]
    assert "Activated" in job.summary and (data_dir / "facts.json").exists()


def test_second_refresh_with_same_data_changes_nothing(data_dir, fake_fetch):
    refresher, catalog, calls = make_refresher(data_dir)
    first, _ = refresher.start(wait=True)
    second, _ = refresher.start(wait=True)
    assert second.state == "no_changes" and catalog.active()["version"] == first.version
    assert calls == ["reloaded"]


def test_fetch_failure_keeps_the_live_version(data_dir, fake_fetch):
    refresher, catalog, calls = make_refresher(data_dir)
    live = refresher.start(wait=True)[0].version
    fake_fetch["ok"] = False
    job, _ = refresher.start(wait=True)
    assert job.state == "failed" and "HTTP 503" in job.problems[0]
    assert catalog.active()["version"] == live and calls == ["reloaded"]


def test_failed_retrieval_gate_keeps_the_live_version(data_dir, fake_fetch, monkeypatch):
    refresher, catalog, _ = make_refresher(data_dir)
    live = refresher.start(wait=True)[0].version
    refresher.evaluate = True
    monkeypatch.setattr(refresher, "_retrieval_gate", lambda version: ["Top-1 accuracy: 0.5"])
    fake_fetch["expense"] = "0.77"
    job, _ = refresher.start(wait=True)
    assert job.state == "failed" and job.version != live
    assert catalog.active()["version"] == live
    assert catalog.get(job.version)["activated_at"] is None  # staged, never live


def test_only_one_refresh_at_a_time(data_dir):
    refresher, _, _ = make_refresher(data_dir)
    running = RefreshJob(id="abc", trigger="api", force=False)
    refresher.current = running
    job, started = refresher.start()
    assert job is running and not started


def test_unexpected_error_is_reported_not_raised(data_dir, monkeypatch):
    refresher, catalog, _ = make_refresher(data_dir)

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(fetch_module, "fetch_all", boom)
    job, _ = refresher.start(wait=True)
    assert job.state == "failed" and "disk full" in job.problems[0]
    assert refresher.current is None and refresher.status() is job


# ---------------------------------------------------------------- endpoints


class FakeRefresher:
    def __init__(self):
        self.job = None

    def start(self, force=False, trigger="api", wait=False):
        self.job = RefreshJob(id="job1", trigger=trigger, force=force, state="running")
        return self.job, True

    def status(self):
        return self.job


async def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_admin_endpoints_disabled_without_token(tmp_path):
    from tests.test_api import make_pipeline

    app = create_app(Settings(_env_file=None, data_dir=tmp_path), pipeline=make_pipeline())
    async with await _client(app) as c:
        assert (await c.post("/api/admin/refresh")).status_code == 404
        assert (await c.get("/api/admin/refresh")).status_code == 404


async def test_admin_endpoints_require_the_token(tmp_path):
    from tests.test_api import make_pipeline

    fake = FakeRefresher()
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, admin_token="s3cret"),
                     pipeline=make_pipeline(), refresher=fake)
    async with await _client(app) as c:
        assert (await c.post("/api/admin/refresh")).status_code == 401
        bad = {"Authorization": "Bearer wrong"}
        assert (await c.post("/api/admin/refresh", headers=bad)).status_code == 401
        ok = {"Authorization": "Bearer s3cret"}
        r = await c.post("/api/admin/refresh?force=true", headers=ok)
        assert r.status_code == 202 and r.json()["started"] is True
        assert r.json()["job"]["force"] is True
        status = await c.get("/api/admin/refresh", headers=ok)
        assert status.json()["job"]["id"] == "job1"
        health = (await c.get("/api/health")).json()
        assert health["last_refresh"]["state"] == "running"
        assert "s3cret" not in json.dumps(health)


# ---------------------------------------------------------------- degraded -> healthy


def test_degraded_app_heals_when_the_index_appears(tmp_path):
    from app.main import try_heal
    from tests.test_api import make_pipeline

    attempts = {"n": 0}

    def factory():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("no index found; run `python -m app.ingest` first")
        return make_pipeline()

    app = create_app(Settings(_env_file=None, data_dir=tmp_path), pipeline_factory=factory)
    app.state.pipeline_factory = factory
    assert try_heal(app) is False and "no index" in app.state.pipeline_error
    assert try_heal(app) is False
    assert try_heal(app) is True and app.state.pipeline is not None
    assert app.state.pipeline_error is None
    assert try_heal(app) is True and attempts["n"] == 3  # no rebuild once healthy
