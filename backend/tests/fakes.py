"""Test doubles shared by several test modules."""

import hashlib
import math
from types import SimpleNamespace

from app.text import tokenize

DIM = 128


class HashingEmbedder:
    """Bag-of-words hashed into a fixed vector: shared words -> higher cosine similarity.
    Stands in for the real model so tests need no download."""

    def __init__(self):
        self.calls = 0

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * DIM
        for tok in tokenize(text):
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_passages(self, texts):
        self.calls += len(texts)
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


class FakePineconeIndex:
    """In-memory stand-in for a Pinecone serverless index, returning the same response
    shapes as pinecone>=5 (FetchResponse.vectors, QueryResponse.matches, ListResponse
    pages, DescribeIndexStatsResponse.namespaces)."""

    def __init__(self, list_page_size: int = 7):
        self.namespaces: dict[str, dict[str, tuple[list[float], dict]]] = {}
        self.page = list_page_size

    def upsert(self, *, vectors, namespace="", show_progress=True, **_):
        ns = self.namespaces.setdefault(namespace, {})
        for v in vectors:
            ns[v["id"]] = (list(v["values"]), dict(v["metadata"]))

    def fetch(self, *, ids, namespace="", **_):
        ns = self.namespaces.get(namespace, {})
        return SimpleNamespace(vectors={
            i: SimpleNamespace(id=i, values=ns[i][0], metadata=ns[i][1]) for i in ids if i in ns})

    def list(self, *, namespace="", **_):
        ids = sorted(self.namespaces.get(namespace, {}))
        for start in range(0, len(ids), self.page):
            yield SimpleNamespace(vectors=[SimpleNamespace(id=i)
                                           for i in ids[start:start + self.page]])

    def query(self, *, vector, top_k, namespace="", filter=None, include_metadata=False, **_):
        ns = self.namespaces.get(namespace, {})
        wanted = filter["scheme"]["$eq"] if filter else None
        scored = [
            SimpleNamespace(id=i, score=sum(a * b for a, b in zip(vector, vals, strict=True)))
            for i, (vals, meta) in ns.items() if wanted is None or meta.get("scheme") == wanted
        ]
        scored.sort(key=lambda m: m.score, reverse=True)
        return SimpleNamespace(matches=scored[:top_k])

    def delete(self, *, delete_all=False, namespace="", ids=None, **_):
        if delete_all:
            self.namespaces.pop(namespace, None)
        else:
            for i in ids or []:
                self.namespaces.get(namespace, {}).pop(i, None)

    def describe_index_stats(self, **_):
        return SimpleNamespace(namespaces={
            ns: SimpleNamespace(vector_count=len(v)) for ns, v in self.namespaces.items()})
