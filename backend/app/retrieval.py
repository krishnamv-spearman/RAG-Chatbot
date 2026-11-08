"""Hybrid retriever: BM25 keyword search + vector search, fused with Reciprocal Rank Fusion.

- **Relevance gate:** best vector cosine similarity of the ORIGINAL question vs
  RELEVANCE_THRESHOLD. (RRF scores depend only on rank positions, so they can't tell a
  nonsense question from a good one.)
- **Ranking:** when the scheme is known, rank on the question with the scheme's name and
  aliases removed. The name carries no information once results are filtered to that
  scheme, and in short chunks it otherwise outweighs the fact being asked about
  (data-research.md §5: top-1 92.5% -> 100%).
- The active index (vector-store space + keyword file) comes from index_manifest.json;
  the retriever refuses to start if the vector store and the manifest disagree.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from rank_bm25 import BM25Okapi

from app.embeddings import Embedder
from app.ingest.catalog import Catalog, get_catalog
from app.registry import Scheme, load_schemes
from app.text import expand_query, tokenize
from app.vectorstore import VectorStore

CANDIDATES = 20
RRF_K = 60

# Generic words stripped along with scheme names. Plan/option words ("direct", "regular",
# "growth", "plan") are deliberately kept: they change the meaning of a question.
_GENERIC_TERMS = re.compile(r"\b(?:hdfc|mutual funds?|funds?|etf fof|fof)\b")


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    metadata: dict
    score: float  # fused RRF score
    similarity: float | None = None  # vector cosine similarity (ranking query)
    bm25: float | None = None


@dataclass
class RetrievalResult:
    query: str
    scheme: str | None
    ranking_query: str = ""
    chunks: list[RetrievedChunk] = field(default_factory=list)
    best_similarity: float = 0.0  # gate signal, from the original question
    relevant: bool = False


def rrf_fuse(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion: score(d) = Σ 1 / (k + rank), rank starting at 1."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


def scheme_terms(schemes: dict[str, Scheme]) -> list[str]:
    """All scheme names and aliases, longest first (so 'silver etf fof' beats 'silver')."""
    terms = {a for s in schemes.values() for a in s.aliases}
    terms |= {s.name.lower() for s in schemes.values()}
    return sorted(terms, key=len, reverse=True)


def strip_scheme_terms(question: str, terms: list[str]) -> str:
    """'What is the TER of HDFC Large Cap?' -> 'what is the ter of'. Returns the original
    question if nothing searchable would be left."""
    text = question.lower()
    for term in terms:
        text = re.sub(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", " ", text)
    text = _GENERIC_TERMS.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" ?.!,'\"")
    return text if tokenize(text) else question


class Retriever:
    """`version=None` serves the live version; a specific version is used to evaluate a
    staged build before it goes live (daily refresh quality gate)."""

    def __init__(self, data_dir: Path, embedder: Embedder, threshold: float,
                 store: VectorStore | None = None, schemes: dict[str, Scheme] | None = None,
                 catalog: Catalog | None = None, version: str | None = None):
        if store is None:
            from app.config import get_settings
            from app.vectorstore import get_vector_store

            store = get_vector_store(get_settings(), data_dir)
        catalog = catalog or get_catalog(store, data_dir)
        manifest = catalog.get(version) if version else catalog.active()
        if manifest is None:
            raise RuntimeError(f"index version {version!r} not found" if version else
                               "no index found; run `python -m app.ingest` first")
        if manifest["store"] != store.name:
            raise RuntimeError(f"index was built for {manifest['store']!r}, but VECTOR_STORE is "
                               f"{store.name!r}; re-run ingestion")

        self.embedder, self.threshold, self.store = embedder, threshold, store
        self.manifest = manifest
        self.version = manifest["version"]
        self.space = manifest["space"]
        payload = catalog.load_payload(manifest, store)
        self.ids: list[str] = payload["ids"]
        self.texts: list[str] = payload["texts"]
        self.metadatas: list[dict] = payload["metadatas"]
        self._index = {cid: i for i, cid in enumerate(self.ids)}
        self._bm25 = BM25Okapi(payload["tokens"])

        expected = set(manifest["chunk_ids"])
        if set(self.ids) != expected or store.ids(self.space) != expected:
            raise RuntimeError("vector and keyword indexes are out of sync with "
                               "index_manifest.json; re-run ingestion")
        self._terms = scheme_terms(schemes if schemes is not None else load_schemes(data_dir))

    def _keyword_ranking(self, query: str, scheme: str | None) -> tuple[list[str], dict]:
        tokens = tokenize(expand_query(query))
        if not tokens:
            return [], {}
        scores = self._bm25.get_scores(tokens)
        pairs = [
            (self.ids[i], float(s))
            for i, s in enumerate(scores)
            if s > 0 and (scheme is None or self.metadatas[i]["scheme"] == scheme)
        ]
        pairs.sort(key=lambda p: p[1], reverse=True)
        top = pairs[:CANDIDATES]
        return [p[0] for p in top], dict(top)

    def retrieve(self, query: str, scheme: str | None = None, k: int = 5) -> RetrievalResult:
        gate_hits = self.store.query(self.space, self.embedder.embed_query(query),
                                     CANDIDATES, scheme)
        best = max((s for _, s in gate_hits), default=0.0)

        ranking_query = strip_scheme_terms(query, self._terms) if scheme else query
        if ranking_query == query:
            vec_hits = gate_hits
        else:
            vec_hits = self.store.query(self.space, self.embedder.embed_query(ranking_query),
                                        CANDIDATES, scheme)
        vec_hits = [(cid, s) for cid, s in vec_hits if cid in self._index]
        kw_ids, kw_scores = self._keyword_ranking(ranking_query, scheme)
        sims = dict(vec_hits)
        fused = rrf_fuse([kw_ids, [cid for cid, _ in vec_hits]])

        top = sorted(fused.items(), key=lambda p: p[1], reverse=True)[:k]
        chunks = [
            RetrievedChunk(
                chunk_id=cid,
                text=self.texts[self._index[cid]],
                metadata=self.metadatas[self._index[cid]],
                score=score,
                similarity=sims.get(cid),
                bm25=kw_scores.get(cid),
            )
            for cid, score in top
        ]
        return RetrievalResult(query=query, scheme=scheme, ranking_query=ranking_query,
                               chunks=chunks, best_similarity=best,
                               relevant=best >= self.threshold)
