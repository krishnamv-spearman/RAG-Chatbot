"""The same contract tests for every VectorStore implementation."""

import pytest

from app.config import Settings
from app.vectorstore import ChromaStore, PineconeStore, get_vector_store
from tests.fakes import FakePineconeIndex


@pytest.fixture(params=["chroma", "pinecone"])
def store(request, tmp_path):
    if request.param == "chroma":
        return ChromaStore(tmp_path)
    return PineconeStore(Settings(_env_file=None), index=FakePineconeIndex(list_page_size=2))


def unit(*xs):
    norm = sum(x * x for x in xs) ** 0.5
    return [x / norm for x in xs]


IDS = ["mid#a", "mid#b", "small#a", "small#b", "small#c"]
VECS = [unit(1, 0, 0), unit(0, 1, 0), unit(1, 0.1, 0), unit(0, 0, 1), unit(0.5, 0.5, 0)]
METAS = [{"scheme": "hdfc-mid-cap" if i.startswith("mid") else "hdfc-small-cap",
          "chunk_hash": i, "volatile": False} for i in IDS]


@pytest.fixture
def filled(store):
    space = f"{store.space_prefix}v1"
    store.upsert(space, IDS, VECS, METAS)
    return store, space


def test_upsert_ids_and_spaces(filled):
    store, space = filled
    assert store.ids(space) == set(IDS)  # Pinecone fake pages 2 at a time
    assert space in store.spaces()


def test_fetch_returns_vectors_and_metadata(filled):
    store, space = filled
    got = store.fetch(space, ["mid#a", "small#c", "missing"])
    assert set(got) == {"mid#a", "small#c"}
    vec, meta = got["mid#a"]
    assert vec == pytest.approx(VECS[0]) and meta["scheme"] == "hdfc-mid-cap"


def test_query_returns_cosine_similarity_best_first(filled):
    store, space = filled
    hits = store.query(space, unit(1, 0, 0), k=3)
    assert [h[0] for h in hits][:2] == ["mid#a", "small#a"]
    assert hits[0][1] == pytest.approx(1.0, abs=1e-4)
    assert hits[0][1] >= hits[1][1] >= hits[2][1]


def test_query_scheme_filter(filled):
    store, space = filled
    hits = store.query(space, unit(1, 0, 0), k=5, scheme="hdfc-small-cap")
    assert {h[0] for h in hits} == {"small#a", "small#b", "small#c"}


def test_spaces_are_independent_and_droppable(filled):
    store, space = filled
    other = f"{store.space_prefix}v2"
    store.upsert(other, ["x"], [unit(1, 1, 1)], [{"scheme": "s", "chunk_hash": "x"}])
    store.drop(space)
    assert space not in store.spaces() and store.ids(other) == {"x"}
    assert store.ids(space) == set() and store.fetch(space, ["mid#a"]) == {}
    store.drop("never-existed")  # no error


def test_factory_and_validation(tmp_path):
    assert get_vector_store(Settings(_env_file=None), tmp_path).name == "chroma"
    assert get_vector_store(Settings(_env_file=None, vector_store="pinecone"),
                            tmp_path).name == "pinecone"
    with pytest.raises(ValueError, match="VECTOR_STORE"):
        get_vector_store(Settings(_env_file=None, vector_store="faiss"), tmp_path)


def test_pinecone_without_api_key_fails_clearly():
    store = PineconeStore(Settings(_env_file=None, pinecone_api_key=None))
    with pytest.raises(RuntimeError, match="PINECONE_API_KEY"):
        store.spaces()
