"""Local sentence embeddings (BAAI/bge-small-en-v1.5 via sentence-transformers)."""

from functools import lru_cache
from typing import Protocol

# bge models are trained with this prefix on the query side only (not on passages).
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder(Protocol):
    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self._model = SentenceTransformer(model_name)
        self._query_prefix = BGE_QUERY_PREFIX if "bge" in model_name.lower() else ""

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(texts, normalize_embeddings=True, batch_size=32)
        return vectors.tolist()

    def embed_query(self, text: str) -> list[float]:
        vector = self._model.encode(self._query_prefix + text, normalize_embeddings=True)
        return vector.tolist()


@lru_cache
def get_embedder(model_name: str) -> SentenceTransformerEmbedder:
    return SentenceTransformerEmbedder(model_name)
