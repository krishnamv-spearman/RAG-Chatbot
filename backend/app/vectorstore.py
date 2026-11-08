"""Vector store interface with three implementations:

- ChromaStore        local files (data/chroma/), the default for development
- ChromaRemoteStore  a shared Chroma server or Chroma Cloud: used by the daily GitHub
                     Actions refresh (Phase 7), whose runner machine is wiped after each run
- PineconeStore      optional managed cloud store

This is the only module that imports `chromadb` or `pinecone`. A "space" is one complete,
versioned copy of the index (a Chroma collection or a Pinecone namespace); which space is
live is recorded by the index catalog (app/ingest/catalog.py).
"""

import logging
import time
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings

logger = logging.getLogger("mf_faq.vectorstore")

Embedding = list[float]


class VectorStore(Protocol):
    name: str  # "chroma" | "chroma_remote" | "pinecone"
    space_prefix: str  # new spaces are named f"{space_prefix}{version}"

    def upsert(self, space: str, ids: list[str], embeddings: list[Embedding],
               metadatas: list[dict], documents: list[str] | None = None) -> None: ...

    def fetch(self, space: str, ids: list[str]) -> dict[str, tuple[Embedding, dict]]: ...

    def fetch_documents(self, space: str) -> dict[str, tuple[str, dict]]:
        """Every record's (text, metadata) in a space."""
        ...

    def ids(self, space: str) -> set[str]: ...

    def query(self, space: str, embedding: Embedding, k: int,
              scheme: str | None = None) -> list[tuple[str, float]]:
        """Top-k (id, cosine similarity), best first, optionally limited to one scheme."""
        ...

    def drop(self, space: str) -> None: ...

    def spaces(self) -> list[str]: ...


# ---------------------------------------------------------------- ChromaDB


def _chroma_settings():
    from chromadb.config import Settings as ChromaSettings

    return ChromaSettings(anonymized_telemetry=False)


class ChromaStore:
    """Local ChromaDB (files). Also the base for the remote store: only the client differs."""

    name = "chroma"
    space_prefix = "mf_chunks_"

    def __init__(self, data_dir: Path | None = None, client: Any | None = None):
        if client is None:
            import chromadb

            client = chromadb.PersistentClient(path=str(Path(data_dir) / "chroma"),
                                               settings=_chroma_settings())
        self.client = client

    def _collection(self, space: str):
        return self.client.get_or_create_collection(
            space, metadata={"hnsw:space": "cosine"}, embedding_function=None)

    def upsert(self, space, ids, embeddings, metadatas, documents=None):
        if ids:
            self._collection(space).upsert(ids=ids, embeddings=embeddings, metadatas=metadatas,
                                           documents=documents)

    def fetch(self, space, ids):
        if not ids or space not in self.spaces():
            return {}
        got = self._collection(space).get(ids=ids, include=["embeddings", "metadatas"])
        return {
            i: ([float(x) for x in e], m)
            for i, e, m in zip(got["ids"], got["embeddings"], got["metadatas"], strict=True)
        }

    def fetch_documents(self, space):
        if space not in self.spaces():
            return {}
        got = self._collection(space).get(include=["documents", "metadatas"])
        return {i: (d or "", m) for i, d, m in
                zip(got["ids"], got["documents"], got["metadatas"], strict=True)}

    def ids(self, space):
        if space not in self.spaces():
            return set()
        return set(self._collection(space).get(include=[])["ids"])

    def query(self, space, embedding, k, scheme=None):
        collection = self._collection(space)
        count = collection.count()
        if count == 0:
            return []
        result = collection.query(
            query_embeddings=[embedding],
            n_results=min(k, count),
            where={"scheme": scheme} if scheme else None,
            include=["distances"],
        )
        # Chroma cosine distance = 1 - cosine similarity
        return [(i, 1.0 - d) for i, d in zip(result["ids"][0], result["distances"][0],
                                             strict=True)]

    def drop(self, space):
        if space in self.spaces():
            self.client.delete_collection(space)

    def spaces(self):
        return [getattr(c, "name", c) for c in self.client.list_collections()]


class ChromaRemoteStore(ChromaStore):
    """A shared Chroma server (client/server mode) or Chroma Cloud.

    Chroma Cloud when CHROMA_API_KEY is set (with CHROMA_TENANT / CHROMA_DATABASE);
    otherwise a self-hosted server at CHROMA_HOST:CHROMA_PORT, with an optional bearer token.
    """

    name = "chroma_remote"

    def __init__(self, settings: Settings, client: Any | None = None):
        if client is None:
            import chromadb

            if settings.chroma_api_key and settings.chroma_api_key.get_secret_value().strip():
                client = chromadb.CloudClient(
                    tenant=settings.chroma_tenant, database=settings.chroma_database,
                    api_key=settings.chroma_api_key.get_secret_value(),
                    settings=_chroma_settings())
            else:
                token = settings.chroma_auth_token
                headers = ({"Authorization": f"Bearer {token.get_secret_value()}"}
                           if token and token.get_secret_value().strip() else None)
                client = chromadb.HttpClient(
                    host=settings.chroma_host, port=settings.chroma_port,
                    ssl=settings.chroma_ssl, headers=headers, settings=_chroma_settings())
        super().__init__(client=client)


# ---------------------------------------------------------------- Pinecone

PINECONE_BATCH = 100
PINECONE_VISIBLE_TIMEOUT_S = 60  # serverless writes become queryable after a short delay


class PineconeStore:
    """One serverless index; each build is a namespace `<prefix>-<version>`. Chunk texts
    are kept in the metadata field `text`."""

    name = "pinecone"

    def __init__(self, settings: Settings, index: Any | None = None,
                 dimension: int | None = None):
        self.settings = settings
        self.space_prefix = f"{settings.pinecone_namespace_prefix}-"
        self._dimension = dimension
        self._index = index  # injected in tests
        self._pc = None

    # Index creation is lazy: the dimension is only known once embeddings exist.
    def _get_index(self, dimension: int | None = None):
        if self._index is not None:
            return self._index
        key = self.settings.pinecone_api_key
        if not key or not key.get_secret_value().strip():
            raise RuntimeError("PINECONE_API_KEY is not set (VECTOR_STORE=pinecone)")
        try:
            from pinecone import Pinecone, ServerlessSpec
        except ImportError as exc:
            raise RuntimeError("Pinecone SDK not installed: pip install -e '.[pinecone]'") from exc
        self._pc = self._pc or Pinecone(api_key=key.get_secret_value())
        name = self.settings.pinecone_index
        if not self._pc.has_index(name):
            dim = dimension or self._dimension
            if dim is None:
                raise RuntimeError(f"Pinecone index {name!r} doesn't exist; run ingestion first")
            logger.info("creating Pinecone index %s (dimension %d, cosine)", name, dim)
            self._pc.create_index(
                name=name, dimension=dim, metric="cosine",
                spec=ServerlessSpec(cloud=self.settings.pinecone_cloud,
                                    region=self.settings.pinecone_region))
        self._index = self._pc.Index(name)
        return self._index

    def _namespace_count(self, space: str) -> int:
        stats = self._get_index().describe_index_stats()
        summary = (stats.namespaces or {}).get(space)
        return int(getattr(summary, "vector_count", 0) or 0) if summary else 0

    def upsert(self, space, ids, embeddings, metadatas, documents=None):
        if not ids:
            return
        index = self._get_index(len(embeddings[0]))
        texts = documents or [None] * len(ids)
        vectors = [{"id": i, "values": e,
                    "metadata": {**m, "text": t} if t is not None else m}
                   for i, e, m, t in zip(ids, embeddings, metadatas, texts, strict=True)]
        for start in range(0, len(vectors), PINECONE_BATCH):
            index.upsert(vectors=vectors[start:start + PINECONE_BATCH], namespace=space,
                         show_progress=False)
        # Wait until the writes are visible, so the catalog never points at an
        # index that's still filling up.
        deadline = time.monotonic() + PINECONE_VISIBLE_TIMEOUT_S
        while self._namespace_count(space) < len(set(ids)):
            if time.monotonic() > deadline:
                raise RuntimeError(f"Pinecone namespace {space} not fully visible after "
                                   f"{PINECONE_VISIBLE_TIMEOUT_S}s")
            time.sleep(1)

    def _fetch_raw(self, space, ids):
        out = {}
        index = self._get_index()
        for start in range(0, len(ids), PINECONE_BATCH):
            response = index.fetch(ids=ids[start:start + PINECONE_BATCH], namespace=space)
            for vid, vec in (response.vectors or {}).items():
                out[vid] = ([float(x) for x in vec.values], dict(vec.metadata or {}))
        return out

    def fetch(self, space, ids):
        return {i: (v, {k: x for k, x in m.items() if k != "text"})
                for i, (v, m) in self._fetch_raw(space, ids).items()}

    def fetch_documents(self, space):
        raw = self._fetch_raw(space, sorted(self.ids(space)))
        return {i: (m.get("text", ""), {k: x for k, x in m.items() if k != "text"})
                for i, (_, m) in raw.items()}

    def ids(self, space):
        found: set[str] = set()
        for page in self._get_index().list(namespace=space):
            items = getattr(page, "vectors", page)  # ListResponse, or a plain list of ids
            for item in items:
                found.add(item if isinstance(item, str) else getattr(item, "id", None)
                          or item["id"])
        return found

    def query(self, space, embedding, k, scheme=None):
        response = self._get_index().query(
            vector=embedding, top_k=k, namespace=space,
            filter={"scheme": {"$eq": scheme}} if scheme else None,
            include_metadata=False)
        # For a cosine index, Pinecone's score is the cosine similarity.
        return [(m.id, float(m.score)) for m in response.matches]

    def drop(self, space):
        if space in self.spaces():
            self._get_index().delete(delete_all=True, namespace=space)

    def spaces(self):
        stats = self._get_index().describe_index_stats()
        return list((stats.namespaces or {}).keys())


def get_vector_store(settings: Settings, data_dir: Path) -> VectorStore:
    if settings.vector_store == "chroma":
        return ChromaStore(data_dir)
    if settings.vector_store == "chroma_remote":
        return ChromaRemoteStore(settings)
    if settings.vector_store == "pinecone":
        return PineconeStore(settings)
    raise ValueError("VECTOR_STORE must be 'chroma', 'chroma_remote' or 'pinecone', "
                     f"not {settings.vector_store!r}")
