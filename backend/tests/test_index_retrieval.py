"""Index build + retriever tests, run against BOTH vector stores: a real temporary ChromaDB
and Pinecone (via an in-memory fake index with the same API). No model download needed."""

import json

import pytest

from app.config import Settings
from app.ingest.catalog import MANIFEST_FILE
from app.ingest.chunk import chunk_document
from app.ingest.facts import build_facts
from app.ingest.index import build_index, load_manifest
from app.ingest.parse import parse_html
from app.registry import Source, load_schemes
from app.retrieval import Retriever, scheme_terms, strip_scheme_terms
from app.vectorstore import ChromaStore, PineconeStore
from tests.conftest import make_page
from tests.fakes import FakePineconeIndex, HashingEmbedder

MODEL = "fake-hashing-model"


def _source(scheme: str) -> Source:
    return Source(id=f"groww-hdfc-{scheme}", url=f"https://groww.in/mutual-funds/{scheme}",
                  scheme=f"hdfc-{scheme}", source_type="groww_scheme_page")


def _docs(mid_cap_data):
    small = dict(mid_cap_data, scheme_name="HDFC Small Cap Fund Direct Growth",
                 expense_ratio="0.79", exit_load="Exit load of 1% if redeemed within 1 year.")
    meta = {"fetched_at": "2026-11-07T10:00:00+00:00"}
    return [
        parse_html(make_page(mid_cap_data), _source("mid-cap"), meta, "HDFC Mid Cap Fund"),
        parse_html(make_page(small), _source("small-cap"), meta, "HDFC Small Cap Fund"),
    ]


def _chunks(docs):
    return [c for d in docs for c in chunk_document(d)]


@pytest.fixture(params=["chroma", "pinecone"])
def store(request, tmp_path):
    if request.param == "chroma":
        return ChromaStore(tmp_path)
    return PineconeStore(Settings(_env_file=None), index=FakePineconeIndex())


@pytest.fixture
def built(tmp_path, mid_cap_data, store):
    docs = _docs(mid_cap_data)
    chunks = _chunks(docs)
    embedder = HashingEmbedder()
    stats = build_index(chunks, tmp_path, embedder, store, MODEL)
    return tmp_path, docs, chunks, embedder, stats, store


def _retriever(data_dir, embedder, store, threshold=0.1):
    return Retriever(data_dir, embedder, threshold, store=store, schemes=load_schemes())


# ---------------------------------------------------------------- building


def test_first_build_writes_manifest_space_and_keyword_file(built):
    data_dir, _, chunks, embedder, stats, store = built
    assert stats.added == len(chunks) and embedder.calls == len(chunks) and stats.version
    manifest = load_manifest(data_dir)
    assert manifest["store"] == store.name and manifest["embed_model"] == MODEL
    assert manifest["space"] == f"{store.space_prefix}{stats.version}"
    assert store.ids(manifest["space"]) == set(manifest["chunk_ids"]) == {c.chunk_id
                                                                          for c in chunks}
    assert (data_dir / manifest["bm25_file"]).exists()
    assert manifest["corpus_date"] == "2026-11-06"


def test_rebuild_with_same_data_is_a_no_op(built):
    data_dir, _, chunks, _, _, store = built
    before = (data_dir / MANIFEST_FILE).read_text(encoding="utf-8")
    embedder = HashingEmbedder()
    stats = build_index(chunks, data_dir, embedder, store, MODEL)
    assert not stats.changed and stats.version is None and embedder.calls == 0
    assert (data_dir / MANIFEST_FILE).read_text(encoding="utf-8") == before


def test_changed_fact_builds_new_space_reembeds_only_that_chunk(built, mid_cap_data):
    data_dir, _, _, _, first, store = built
    old_manifest = load_manifest(data_dir)
    mid_cap_data["expense_ratio"] = "0.77"
    del mid_cap_data["stamp_duty"]
    embedder = HashingEmbedder()
    stats = build_index(_chunks(_docs(mid_cap_data)), data_dir, embedder, store, MODEL)
    assert stats.updated == 1 and embedder.calls == 1  # only mid cap's expense ratio
    # The small cap test doc is copied from the mid cap data, so stamp_duty goes from both.
    assert stats.deleted == 2
    new_manifest = load_manifest(data_dir)
    assert new_manifest["space"] != old_manifest["space"]
    # Old build removed only after the switch-over.
    assert old_manifest["space"] not in store.spaces()
    assert not (data_dir / old_manifest["bm25_file"]).exists()


def test_new_fetch_time_alone_is_not_a_change(built):
    # A daily refresh over unchanged pages must not create a new version (Phase 7).
    data_dir, docs, _, _, _, store = built
    for d in docs:
        d["fetched_at"] = "2026-11-08T10:00:00+00:00"
    embedder = HashingEmbedder()
    stats = build_index(_chunks(docs), data_dir, embedder, store, MODEL)
    assert stats.version is None and not stats.changed and embedder.calls == 0


def test_new_data_date_is_metadata_only(built):
    data_dir, docs, _, _, _, store = built
    for d in docs:
        d["doc_date"] = "2026-11-08"
    chunks = _chunks(docs)
    embedder = HashingEmbedder()
    stats = build_index(chunks, data_dir, embedder, store, MODEL)
    assert stats.version and stats.metadata_updated == len(chunks) and embedder.calls == 0


def test_changing_the_embedding_model_reembeds_everything(built):
    data_dir, _, chunks, _, _, store = built
    embedder = HashingEmbedder()
    build_index(chunks, data_dir, embedder, store, "another-model")
    assert embedder.calls == len(chunks)


def test_failed_build_keeps_the_previous_index(built, mid_cap_data, monkeypatch):
    data_dir, _, _, _, _, store = built
    before = load_manifest(data_dir)
    monkeypatch.setattr(store, "upsert", lambda *a, **k: None)  # writes silently lost
    mid_cap_data["expense_ratio"] = "0.99"
    with pytest.raises(RuntimeError, match="incomplete"):
        build_index(_chunks(_docs(mid_cap_data)), data_dir, HashingEmbedder(), store, MODEL)
    assert load_manifest(data_dir) == before
    assert store.ids(before["space"]) == set(before["chunk_ids"])


# ---------------------------------------------------------------- retrieving


def test_scheme_filter_never_returns_other_schemes(built):
    data_dir, _, _, embedder, _, store = built
    result = _retriever(data_dir, embedder, store).retrieve("exit load", scheme="hdfc-small-cap")
    assert result.chunks
    assert {c.metadata["scheme"] for c in result.chunks} == {"hdfc-small-cap"}


def test_retrieves_the_right_fact(built):
    data_dir, _, _, embedder, _, store = built
    result = _retriever(data_dir, embedder, store).retrieve(
        "What is the expense ratio of HDFC Mid Cap Fund?", scheme="hdfc-mid-cap")
    assert result.chunks[0].chunk_id == "groww-hdfc-mid-cap#expense_ratio"
    assert result.ranking_query == "what is the expense ratio of"
    assert result.relevant and result.best_similarity > 0


def test_gate_uses_the_original_question(built):
    data_dir, _, _, embedder, _, store = built
    r = _retriever(data_dir, embedder, store)
    with_name = r.retrieve("expense ratio of HDFC Mid Cap Fund", scheme="hdfc-mid-cap")
    gate_vec = embedder.embed_query("expense ratio of HDFC Mid Cap Fund")
    expected = max(s for _, s in store.query(r.space, gate_vec, 20, "hdfc-mid-cap"))
    assert with_name.best_similarity == pytest.approx(expected)


def test_threshold_blocks_weak_matches(built):
    data_dir, _, _, embedder, _, store = built
    result = _retriever(data_dir, embedder, store, threshold=0.99).retrieve("expense ratio")
    assert result.chunks and not result.relevant


def test_retriever_refuses_to_start_when_out_of_sync(built):
    data_dir, _, _, embedder, _, store = built
    manifest = load_manifest(data_dir)
    store.drop(manifest["space"])
    with pytest.raises(RuntimeError, match="out of sync"):
        _retriever(data_dir, embedder, store)


def test_retriever_refuses_index_built_for_another_store(built, tmp_path):
    data_dir, _, _, embedder, _, store = built
    other = (PineconeStore(Settings(_env_file=None), index=FakePineconeIndex())
             if store.name == "chroma" else ChromaStore(tmp_path / "other"))
    with pytest.raises(RuntimeError, match="re-run ingestion"):
        _retriever(data_dir, embedder, other)


def test_missing_manifest_gives_a_clear_error(tmp_path):
    with pytest.raises(RuntimeError, match="run `python -m app.ingest`"):
        Retriever(tmp_path, HashingEmbedder(), 0.5, store=ChromaStore(tmp_path))


def test_keyword_file_matches_manifest(built):
    data_dir, _, chunks, _, _, _ = built
    manifest = load_manifest(data_dir)
    payload = json.loads((data_dir / manifest["bm25_file"]).read_text(encoding="utf-8"))
    assert payload["ids"] == manifest["chunk_ids"]


def test_facts_link_to_existing_chunks(built):
    _, docs, chunks, _, _, _ = built
    facts = build_facts(docs, chunks)
    mid = facts["hdfc-mid-cap"]["facts"]
    assert mid["expense_ratio"]["value"] == "0.74%"
    assert mid["expense_ratio"]["source_chunk_id"] == "groww-hdfc-mid-cap#expense_ratio"
    assert "lock_in" in facts["hdfc-mid-cap"]["missing"]


# ---------------------------------------------------------------- query processing (R2)

TERMS = scheme_terms(load_schemes())


@pytest.mark.parametrize("question, expected", [
    ("What is the TER of HDFC Large Cap?", "what is the ter of"),
    ("what stocks does mid cap hold", "what stocks does hold"),
    ("What does HDFC Silver ETF FoF invest in?", "what does invest in"),
    ("Exit load of HDFC Mid-Cap Opportunities fund", "exit load of"),
    # Plan/option words are kept: they change the meaning.
    ("Is this the direct plan or regular plan of large cap?",
     "is this the direct plan or regular plan of"),
])
def test_strip_scheme_terms(question, expected):
    assert strip_scheme_terms(question, TERMS) == expected


def test_strip_falls_back_to_original_when_nothing_is_left():
    assert strip_scheme_terms("HDFC Mid Cap Fund?", TERMS) == "HDFC Mid Cap Fund?"
