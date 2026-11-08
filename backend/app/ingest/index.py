"""Builds index versions: vectors (Chroma / Pinecone) + keyword (BM25) data + manifest.

Lifecycle (implementation-plan.md, Phases 3 and 7):

    stage     embed and write a NEW version beside the live one (not yet live)
    activate  check the version is complete, then switch the catalog to it (one record)
    prune     delete old versions, keeping the live one and the newest earlier ones for rollback

A normal local run does all three (`build_index(..., activate=True, keep=1)`). The daily
GitHub Actions refresh stages, runs quality gates against the staged version, and only
then activates, so a bad fetch or parse never replaces good data.

Unchanged data is a no-op (no new version). Chunks whose text is unchanged reuse their
stored embeddings, so only new or changed chunks are sent to the embedding model.
"""

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.embeddings import Embedder
from app.ingest.catalog import Catalog, get_catalog
from app.ingest.chunk import Chunk
from app.text import tokenize
from app.vectorstore import VectorStore

logger = logging.getLogger("mf_faq.index")


@dataclass
class IndexStats:
    added: int = 0
    updated: int = 0  # text changed -> re-embedded
    metadata_updated: int = 0  # same text, new metadata (e.g. fetched_at)
    deleted: int = 0
    unchanged: int = 0
    embedded: int = 0  # how many chunks were sent to the embedding model
    version: str | None = None  # new version, or None if nothing changed
    activated: bool = False
    previous_version: str | None = None  # the version that was live before this build
    pruned: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.metadata_updated or self.deleted)


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def load_manifest(data_dir: Path) -> dict | None:
    """The live manifest of the local file catalog (kept for backwards compatibility)."""
    from app.ingest.catalog import FileCatalog

    return FileCatalog(data_dir).active()


# Metadata that changes on every fetch without the data changing. Left out of the
# fingerprint so a daily run over unchanged pages makes no new version.
VOLATILE_METADATA = ("fetched_at",)


def signature(chunks: list[Chunk], embed_model: str, store_name: str) -> str:
    """Fingerprint of everything that ends up in the index (except fetch timestamps)."""
    h = hashlib.sha256(f"{embed_model}|{store_name}".encode())
    for c in sorted(chunks, key=lambda c: c.chunk_id):
        meta = {k: v for k, v in c.metadata.items() if k not in VOLATILE_METADATA}
        h.update(json.dumps([c.chunk_id, c.text, meta], sort_keys=True).encode())
    return h.hexdigest()


def _new_version(store: VectorStore, catalog: Catalog) -> str:
    base = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    taken = set(store.spaces()) | {f"{store.space_prefix}{m['version']}"
                                   for m in catalog.versions()}
    version, n = base, 1
    while f"{store.space_prefix}{version}" in taken:
        n += 1
        version = f"{base}-{n}"
    return version


def _bm25_payload(chunks: list[Chunk]) -> dict:
    ordered = sorted(chunks, key=lambda c: c.chunk_id)
    return {
        "ids": [c.chunk_id for c in ordered],
        "texts": [c.text for c in ordered],
        "metadatas": [c.metadata for c in ordered],
        "tokens": [tokenize(c.text) for c in ordered],
    }


def build_index(chunks: list[Chunk], data_dir: Path, embedder: Embedder,
                store: VectorStore, embed_model: str, *, catalog: Catalog | None = None,
                activate: bool = True, keep: int = 1, force: bool = False,
                facts: dict | None = None) -> IndexStats:
    """Stage a new version (and, by default, activate it and prune old versions).
    `facts` ({scheme: {field: value}}) is stored in the manifest so later runs can report
    which facts changed."""
    catalog = catalog or get_catalog(store, data_dir)
    ids = [c.chunk_id for c in chunks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate chunk ids")

    sig = signature(chunks, embed_model, store.name)
    live = catalog.active()
    same_store = bool(live) and live.get("store") == store.name
    stats = IndexStats(previous_version=live["version"] if live else None)
    if (not force and same_store and live.get("signature") == sig
            and store.ids(live["space"]) == set(ids)):
        stats.unchanged = len(chunks)
        return stats

    old_space = live["space"] if same_store else None
    old = store.fetch(old_space, sorted(store.ids(old_space))) if old_space else {}
    reuse_vectors = same_store and live.get("embed_model") == embed_model

    embeddings: dict[str, list[float]] = {}
    to_embed: list[Chunk] = []
    for chunk in chunks:
        prev = old.get(chunk.chunk_id)
        if prev is None:
            stats.added += 1
        elif prev[1].get("chunk_hash") != chunk.metadata["chunk_hash"]:
            stats.updated += 1
        elif prev[1] != chunk.metadata:
            stats.metadata_updated += 1
        else:
            stats.unchanged += 1
        if prev is not None and reuse_vectors \
                and prev[1].get("chunk_hash") == chunk.metadata["chunk_hash"]:
            embeddings[chunk.chunk_id] = prev[0]
        else:
            to_embed.append(chunk)
    stats.deleted = len(set(old) - set(ids))

    if to_embed:
        vectors = embedder.embed_passages([c.text for c in to_embed])
        for chunk, vector in zip(to_embed, vectors, strict=True):
            embeddings[chunk.chunk_id] = vector
    stats.embedded = len(to_embed)

    version = _new_version(store, catalog)
    space = f"{store.space_prefix}{version}"
    store.upsert(space, ids, [embeddings[i] for i in ids], [c.metadata for c in chunks],
                 documents=[c.text for c in chunks])
    if store.ids(space) != set(ids):
        store.drop(space)
        raise RuntimeError(f"new index space {space} is incomplete; live index kept")

    doc_dates = [c.metadata.get("doc_date") for c in chunks if c.metadata.get("doc_date")]
    manifest = {
        "version": version,
        "store": store.name,
        "space": space,
        "bm25_file": f"bm25-{version}.json",
        "embed_model": embed_model,
        "signature": sig,
        "chunk_ids": sorted(ids),
        "corpus_date": max(doc_dates) if doc_dates else None,
        "built_at": now_iso(),
        "activated_at": None,
        "facts": facts or {},
    }
    catalog.save(manifest, _bm25_payload(chunks))
    stats.version = version
    if activate:
        activate_version(version, store, catalog)
        stats.activated = True
        stats.pruned = prune_versions(store, catalog, keep=keep)
    return stats


def activate_version(version: str, store: VectorStore, catalog: Catalog) -> dict:
    """Make `version` live (also used for rollback). Refuses incomplete versions."""
    manifest = catalog.get(version)
    if manifest is None:
        raise ValueError(f"unknown index version {version!r}")
    if manifest["store"] != store.name:
        raise ValueError(f"version {version} was built for {manifest['store']!r}")
    if store.ids(manifest["space"]) != set(manifest["chunk_ids"]):
        raise RuntimeError(f"version {version} is incomplete in the vector store")
    manifest["activated_at"] = now_iso()
    catalog.set_active(manifest)
    logger.info("index version %s is now live", version)
    return manifest


def _was_live(manifest: dict) -> bool:
    # Manifests from before Phase 7 have no "activated_at" key: they were always live.
    return "activated_at" not in manifest or manifest["activated_at"] is not None


def prune_versions(store: VectorStore, catalog: Catalog, keep: int = 3) -> list[str]:
    """Keep the live version plus the newest `keep - 1` earlier live versions (rollback
    targets); delete everything else, including staged versions that never went live.
    Cleanup failures are logged, never fatal: the live index is already in place."""
    live = catalog.active()
    if live is None:
        return []
    earlier = [m for m in catalog.versions()
               if m["version"] != live["version"] and _was_live(m)]
    keep_versions = {live["version"]} | {m["version"] for m in earlier[-(keep - 1):]
                                         if keep > 1}
    deleted = []
    for manifest in catalog.versions():
        if manifest["version"] in keep_versions:
            continue
        try:
            store.drop(manifest["space"])
            catalog.delete(manifest)
            deleted.append(manifest["version"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not delete version %s: %s", manifest["version"], exc)
    try:
        catalog.cleanup_legacy(store)
    except Exception as exc:  # noqa: BLE001
        logger.warning("legacy cleanup failed: %s", exc)
    return deleted


def stats_to_dict(stats: IndexStats) -> dict:
    return asdict(stats)
