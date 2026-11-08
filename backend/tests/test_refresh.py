"""Phase 7: daily data refresh: index versions (stage / activate / prune / rollback),
the shared-Chroma catalog, quality gates, run summary, CLI outputs, hot reload, staleness.

No network and no model download: a fake embedder and temporary Chroma databases."""

from datetime import date
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.ingest import __main__ as cli
from app.ingest import gates, summary
from app.ingest.catalog import META_COLLECTION, ChromaMetaCatalog, FileCatalog, version_key
from app.ingest.chunk import chunk_document
from app.ingest.index import activate_version, build_index, prune_versions
from app.ingest.parse import parse_html
from app.main import is_stale
from app.pipeline import IndexSource, reload_if_changed
from app.registry import Source, load_schemes
from app.retrieval import Retriever
from app.vectorstore import ChromaRemoteStore, ChromaStore
from tests.conftest import make_page
from tests.fakes import HashingEmbedder

MODEL = "fake-model"


def _docs(data, expense="0.74"):
    data = dict(data, expense_ratio=expense)
    src = Source(id="groww-hdfc-mid-cap", url="https://groww.in/mutual-funds/mid-cap",
                 scheme="hdfc-mid-cap", source_type="groww_scheme_page")
    return [parse_html(make_page(data), src, {"fetched_at": "2026-11-07T10:00:00+00:00"},
                       "HDFC Mid Cap Fund")]


def _chunks(docs):
    return [c for d in docs for c in chunk_document(d)]


@pytest.fixture(params=["files", "chroma_remote"])
def setup(request, tmp_path):
    """(data_dir, store, catalog) for the local file catalog and the shared-Chroma one."""
    if request.param == "files":
        store = ChromaStore(tmp_path)
        return tmp_path, store, FileCatalog(tmp_path)
    import chromadb

    client = chromadb.PersistentClient(path=str(tmp_path / "server"))
    store = ChromaRemoteStore(Settings(_env_file=None), client=client)
    return tmp_path, store, ChromaMetaCatalog(store)


def build(setup, data, expense="0.74", **kw):
    data_dir, store, catalog = setup
    docs = _docs(data, expense)
    return build_index(_chunks(docs), data_dir, HashingEmbedder(), store, MODEL,
                       catalog=catalog, facts=summary.facts_snapshot(docs), **kw)


# ---------------------------------------------------------------- stage / activate


def test_stage_does_not_change_the_live_version(setup, mid_cap_data):
    _, store, catalog = setup
    first = build(setup, mid_cap_data)
    staged = build(setup, mid_cap_data, expense="0.77", activate=False)
    assert first.activated and not staged.activated
    assert catalog.active()["version"] == first.version
    assert catalog.get(staged.version)["activated_at"] is None
    assert store.ids(catalog.get(staged.version)["space"])  # vectors are there


def test_staged_version_can_be_evaluated_then_activated(setup, mid_cap_data):
    data_dir, store, catalog = setup
    build(setup, mid_cap_data)
    staged = build(setup, mid_cap_data, expense="0.77", activate=False)
    retriever = Retriever(data_dir, HashingEmbedder(), 0.1, store=store,
                          schemes=load_schemes(), catalog=catalog, version=staged.version)
    hit = retriever.retrieve("expense ratio", scheme="hdfc-mid-cap").chunks[0]
    assert "0.77%" in hit.text
    activate_version(staged.version, store, catalog)
    assert catalog.active()["version"] == staged.version
    assert catalog.active()["facts"]["hdfc-mid-cap"]["expense_ratio"] == "0.77%"


def test_unchanged_data_is_a_no_op_unless_forced(setup, mid_cap_data):
    _, _, catalog = setup
    first = build(setup, mid_cap_data)
    again = build(setup, mid_cap_data, activate=False)
    assert again.version is None and not again.changed
    forced = build(setup, mid_cap_data, activate=False, force=True)
    assert forced.version and forced.version != first.version
    assert catalog.active()["version"] == first.version


def test_activate_refuses_unknown_or_incomplete_versions(setup, mid_cap_data):
    _, store, catalog = setup
    staged = build(setup, mid_cap_data, activate=False)
    with pytest.raises(ValueError, match="unknown"):
        activate_version("19990101T000000", store, catalog)
    store.drop(catalog.get(staged.version)["space"])
    with pytest.raises(RuntimeError, match="incomplete"):
        activate_version(staged.version, store, catalog)


# ---------------------------------------------------------------- prune / rollback


def test_prune_keeps_live_plus_rollback_targets_and_drops_failed_stages(setup, mid_cap_data):
    _, store, catalog = setup
    v1 = build(setup, mid_cap_data, expense="0.70", keep=10).version
    v2 = build(setup, mid_cap_data, expense="0.71", keep=10).version
    v3 = build(setup, mid_cap_data, expense="0.72", keep=10).version
    failed = build(setup, mid_cap_data, expense="0.99", activate=False).version  # never live
    assert failed in {m["version"] for m in catalog.versions()}
    # The next activation's prune already removes the never-live staged version...
    v4 = build(setup, mid_cap_data, expense="0.73", keep=10).version
    assert failed not in {m["version"] for m in catalog.versions()}
    # ...and an explicit prune keeps the live version + the 2 newest earlier ones.
    assert prune_versions(store, catalog, keep=3) == [v1]
    remaining = {m["version"] for m in catalog.versions()}
    assert remaining == {v2, v3, v4}
    assert all(store.ids(catalog.get(v)["space"]) for v in remaining)
    assert all(f"{store.space_prefix}{v}" not in store.spaces() for v in (v1, failed))


def test_rollback_to_an_earlier_version(setup, mid_cap_data):
    _, store, catalog = setup
    v1 = build(setup, mid_cap_data, expense="0.70", keep=3).version
    build(setup, mid_cap_data, expense="0.71", keep=3)
    activate_version(v1, store, catalog)
    assert catalog.active()["version"] == v1
    assert catalog.active()["facts"]["hdfc-mid-cap"]["expense_ratio"] == "0.7%"


def test_index_from_before_versions_folder_is_tracked_and_pruned(tmp_path, mid_cap_data):
    """An index built before index_versions/ existed (only index_manifest.json) must not
    leak its collection or keyword file after the next activation."""
    import shutil

    store, catalog = ChromaStore(tmp_path), FileCatalog(tmp_path)
    setup = (tmp_path, store, catalog)
    old = build(setup, mid_cap_data, expense="0.70")
    shutil.rmtree(tmp_path / "index_versions")  # simulate the pre-Phase-7 layout
    old_manifest = catalog.active()
    old_manifest.pop("activated_at")
    (tmp_path / "index_manifest.json").write_text(__import__("json").dumps(old_manifest))

    new = build(setup, mid_cap_data, expense="0.71")  # activate + prune keep=1
    assert [m["version"] for m in catalog.versions()] == [new.version]
    assert f"{store.space_prefix}{old.version}" not in store.spaces()
    assert sorted(p.name for p in tmp_path.glob("bm25-*.json")) == [f"bm25-{new.version}.json"]


def test_prune_removes_orphan_collections_and_files(setup, mid_cap_data):
    data_dir, store, catalog = setup
    live = build(setup, mid_cap_data).version
    store.upsert(f"{store.space_prefix}orphan", ["x"], [[1.0, 0.0]], [{"scheme": "s"}])
    (data_dir / "bm25-orphan.json").write_text("{}")
    prune_versions(store, catalog, keep=3)
    assert f"{store.space_prefix}orphan" not in store.spaces()
    assert store.ids(catalog.get(live)["space"])
    if isinstance(catalog, FileCatalog):
        assert not (data_dir / "bm25-orphan.json").exists()


def test_versions_sort_numerically():
    names = ["20261108T043000-10", "20261108T043000-2", "20261108T043000", "20261107T043000"]
    ordered = sorted(({"version": n} for n in names), key=version_key)
    assert [m["version"] for m in ordered] == ["20261107T043000", "20261108T043000",
                                               "20261108T043000-2", "20261108T043000-10"]


def test_default_local_run_keeps_only_the_live_version(setup, mid_cap_data):
    _, _, catalog = setup
    build(setup, mid_cap_data, expense="0.70")
    live = build(setup, mid_cap_data, expense="0.71").version
    assert [m["version"] for m in catalog.versions()] == [live]


# ---------------------------------------------------------------- shared Chroma specifics


def test_remote_catalog_needs_no_local_files(tmp_path, mid_cap_data):
    import chromadb

    client = chromadb.PersistentClient(path=str(tmp_path / "server"))
    store = ChromaRemoteStore(Settings(_env_file=None), client=client)
    catalog = ChromaMetaCatalog(store)
    data_dir = tmp_path / "runner"  # e.g. a GitHub Actions runner
    data_dir.mkdir()
    docs = _docs(mid_cap_data)
    build_index(_chunks(docs), data_dir, HashingEmbedder(), store, MODEL, catalog=catalog)
    assert list(data_dir.iterdir()) == []  # nothing written next to the code
    assert META_COLLECTION in store.spaces()

    # The app (another machine) sees the same live version through the server only.
    app_store = ChromaRemoteStore(Settings(_env_file=None), client=client)
    retriever = Retriever(tmp_path / "app", HashingEmbedder(), 0.1, store=app_store,
                          schemes=load_schemes(), catalog=ChromaMetaCatalog(app_store))
    assert retriever.version == catalog.active()["version"]
    assert retriever.retrieve("expense ratio", scheme="hdfc-mid-cap").chunks


def test_remote_store_picks_cloud_or_http_client(monkeypatch):
    import chromadb

    calls = {}
    monkeypatch.setattr(chromadb, "HttpClient", lambda **kw: calls.setdefault("http", kw))
    monkeypatch.setattr(chromadb, "CloudClient", lambda **kw: calls.setdefault("cloud", kw))
    ChromaRemoteStore(Settings(_env_file=None, chroma_host="chroma.example", chroma_port=443,
                               chroma_ssl=True, chroma_auth_token="tok"))
    assert calls["http"]["host"] == "chroma.example" and calls["http"]["ssl"] is True
    assert calls["http"]["headers"] == {"Authorization": "Bearer tok"}
    ChromaRemoteStore(Settings(_env_file=None, chroma_api_key="ck-1", chroma_tenant="t",
                               chroma_database="d"))
    assert calls["cloud"]["api_key"] == "ck-1" and calls["cloud"]["tenant"] == "t"


# ---------------------------------------------------------------- quality gates


def test_gates_pass_on_real_shaped_data(mid_cap_data):
    assert gates.check_docs(_docs(mid_cap_data), 1, today=date(2026, 11, 8)) == []


@pytest.mark.parametrize("change, expected", [
    ({"expense_ratio": "7.5"}, "expense ratio"),
    ({"min_sip_investment": 0}, "min_sip"),
    ({"return_stats": [{"risk": "Extreme"}], "meta_desc": ""}, "riskometer"),
])
def test_gates_catch_out_of_range_values(mid_cap_data, change, expected):
    data = dict(mid_cap_data, **change)
    expense = change.get("expense_ratio", "0.74")
    problems = gates.check_docs(_docs(data, expense), 1, today=date(2026, 11, 8))
    assert any(expected in p for p in problems), problems


def test_gates_catch_stale_or_missing_sources(mid_cap_data):
    docs = _docs(mid_cap_data)
    assert any("more than 7 days old" in p
               for p in gates.check_docs(docs, 1, today=date(2026, 12, 1)))
    assert gates.check_docs(docs, 1, today=date(2026, 12, 1), max_age_days=None) == []
    assert any("parsed 1 of 5" in p for p in gates.check_docs(docs, 5, today=date(2026, 11, 8)))


# ---------------------------------------------------------------- summary


def test_summary_lists_fact_changes_and_eval_differences(mid_cap_data):
    old_docs, new_docs = _docs(mid_cap_data, "0.74"), _docs(mid_cap_data, "0.77")
    previous = {"facts": summary.facts_snapshot(old_docs)}
    expected = {"_note": "x", "hdfc-mid-cap": {"expense_ratio": "0.74%"}}
    stats = SimpleNamespace(version="20261108T043000", activated=False,
                            previous_version="20261107T043000", added=0, updated=1,
                            metadata_updated=18, deleted=0, unchanged=0, embedded=1)
    text = summary.render(docs=new_docs, stats=stats, previous=previous, expected=expected)
    assert "| hdfc-mid-cap | expense_ratio | 0.74% | 0.77% |" in text
    assert "staged (not live yet)" in text
    assert "Differs from the hand-checked eval values" in text


# ---------------------------------------------------------------- CLI commands


@pytest.fixture
def cli_env(tmp_path, monkeypatch, mid_cap_data):
    store, catalog = ChromaStore(tmp_path), FileCatalog(tmp_path)
    monkeypatch.setattr(cli, "_store_and_catalog", lambda data_dir: (store, catalog))
    outputs = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    setup = (tmp_path, store, catalog)
    return setup, outputs


def test_cli_activate_prune_and_status(cli_env, mid_cap_data, capsys, tmp_path):
    setup, outputs = cli_env
    _, store, catalog = setup
    build(setup, mid_cap_data, expense="0.70")
    staged = build(setup, mid_cap_data, expense="0.71", activate=False).version
    summary_file = tmp_path / "summary.md"
    assert cli.cmd_activate(staged, tmp_path, str(summary_file)) == 0
    assert catalog.active()["version"] == staged
    assert f"version={staged}" in outputs.read_text()
    assert "Activated" in summary_file.read_text(encoding="utf-8")
    assert cli.cmd_prune(1, tmp_path, None) == 0
    assert [m["version"] for m in catalog.versions()] == [staged]
    cli.cmd_status(tmp_path)
    assert "LIVE" in capsys.readouterr().out


# ---------------------------------------------------------------- app: reload & staleness


class _Retriever:
    def __init__(self, version, date_):
        self.version = version
        self.metadatas = [{"scheme": "hdfc-mid-cap", "url": "https://x", "doc_date": date_}]


def test_reload_swaps_in_the_new_version():
    from app.pipeline import Pipeline

    pipeline = Pipeline(_Retriever("v1", "2026-11-06"), llm=None, education_url="https://a")
    live = {"version": "v1"}
    pipeline.index_source = IndexSource(
        catalog=SimpleNamespace(active=lambda: live),
        make_retriever=lambda v: _Retriever(v, "2026-11-08"))
    assert reload_if_changed(pipeline) is None  # nothing new
    live["version"] = "v2"
    assert reload_if_changed(pipeline) == "v2"
    assert pipeline.retriever.version == "v2" and pipeline.corpus_date == "2026-11-08"


def test_failed_reload_keeps_the_current_version():
    from app.pipeline import Pipeline

    pipeline = Pipeline(_Retriever("v1", "2026-11-06"), llm=None, education_url="https://a")

    def broken(version):
        raise RuntimeError("server down")

    pipeline.index_source = IndexSource(catalog=SimpleNamespace(active=lambda: {"version": "v2"}),
                                        make_retriever=broken)
    with pytest.raises(RuntimeError):
        reload_if_changed(pipeline)
    assert pipeline.retriever.version == "v1"


def test_is_stale():
    today = date(2026, 11, 8)
    assert not is_stale("2026-11-07", 2, today)
    assert not is_stale("2026-11-06", 2, today)
    assert is_stale("2026-11-05", 2, today)
    assert is_stale(None, 2, today)
