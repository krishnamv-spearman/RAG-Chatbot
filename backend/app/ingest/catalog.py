"""Index catalog: which index versions exist and which one is live.

A version = one vector-store space (all chunk vectors) + a small manifest (chunk ids,
signature, dates, key facts) + the keyword (BM25) data. Switching the live version means
updating ONE record, so the vector and keyword indexes can't drift apart and a failed
build never replaces a good one.

- FileCatalog          local stores (chroma, pinecone): files in data/
                         index_manifest.json        the live version's manifest
                         index_versions/<v>.json    every version's manifest
                         bm25-<v>.json              keyword data per version
- ChromaMetaCatalog    chroma_remote: everything lives in the shared Chroma, so a GitHub
                       Actions runner (wiped after each run) and the app see the same data.
                         collection mf_index_meta:  "version:<v>" (manifest), "active"
                         keyword data is rebuilt from the chunk texts stored with the
                         vectors (no large records: Chroma Cloud limits record size)
"""

import json
import os
from pathlib import Path
from typing import Protocol

from app.text import tokenize
from app.vectorstore import ChromaStore, VectorStore

MANIFEST_FILE = "index_manifest.json"
VERSIONS_DIR = "index_versions"
META_COLLECTION = "mf_index_meta"
LEGACY_FILES = ("bm25.json",)  # Phase 2 layout
LEGACY_SPACES = ("mf_chunks",)


def version_key(manifest: dict) -> tuple[str, int]:
    """Order versions by build time: "20261108T043000" < "...-2" < "...-10"."""
    base, _, n = manifest["version"].partition("-")
    return base, int(n) if n.isdigit() else 1


def payload_from_records(records: dict[str, tuple[str, dict]]) -> dict:
    """Keyword-index payload {ids, texts, metadatas, tokens}, ordered by chunk id."""
    ids = sorted(records)
    return {
        "ids": ids,
        "texts": [records[i][0] for i in ids],
        "metadatas": [records[i][1] for i in ids],
        "tokens": [tokenize(records[i][0]) for i in ids],
    }


class Catalog(Protocol):
    def active(self) -> dict | None: ...
    def get(self, version: str) -> dict | None: ...
    def versions(self) -> list[dict]: ...
    def save(self, manifest: dict, payload: dict | None = None) -> None: ...
    def set_active(self, manifest: dict) -> None: ...
    def delete(self, manifest: dict) -> None: ...
    def load_payload(self, manifest: dict, store: VectorStore) -> dict: ...
    def cleanup_legacy(self, store: VectorStore) -> None: ...


def _write_json_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)  # readers never see a half-written file


class FileCatalog:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.versions_dir = self.dir / VERSIONS_DIR

    def _read(self, path: Path) -> dict | None:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def active(self):
        return self._read(self.dir / MANIFEST_FILE)

    def get(self, version):
        found = self._read(self.versions_dir / f"{version}.json")
        if found is None:
            active = self.active()
            found = active if active and active["version"] == version else None
        return found

    def versions(self):
        found = {}
        if self.versions_dir.is_dir():
            for path in self.versions_dir.glob("*.json"):
                manifest = self._read(path)
                found[manifest["version"]] = manifest
        active = self.active()
        if active and active["version"] not in found:  # index built before versions/ existed
            found[active["version"]] = active
        return sorted(found.values(), key=version_key)

    def save(self, manifest, payload=None):
        if payload is not None:
            _write_json_atomic(self.dir / manifest["bm25_file"], payload)
        _write_json_atomic(self.versions_dir / f"{manifest['version']}.json", manifest)

    def set_active(self, manifest):
        self.save(manifest)
        outgoing = self.active()
        if outgoing and not (self.versions_dir / f"{outgoing['version']}.json").exists():
            self.save(outgoing)  # keep tracking it (rollback / pruning) after the switch
        _write_json_atomic(self.dir / MANIFEST_FILE, manifest)  # the switch-over

    def delete(self, manifest):
        for path in (self.versions_dir / f"{manifest['version']}.json",
                     self.dir / (manifest.get("bm25_file") or "")):
            if path.is_file():
                path.unlink()

    def load_payload(self, manifest, store):
        return json.loads((self.dir / manifest["bm25_file"]).read_text(encoding="utf-8"))

    def cleanup_legacy(self, store):
        """Remove the Phase 2 layout and anything no tracked version refers to (e.g. a
        version that was live before index_versions/ existed)."""
        tracked = self.versions()
        _drop_orphan_spaces(store, tracked)
        keep_files = {m.get("bm25_file") for m in tracked}
        for path in self.dir.glob("bm25-*.json"):
            if path.name not in keep_files:
                path.unlink()
        for space in LEGACY_SPACES:
            store.drop(space)
        for name in LEGACY_FILES:
            path = self.dir / name
            if path.exists():
                path.unlink()


def _drop_orphan_spaces(store: VectorStore, tracked: list[dict]) -> None:
    known = {m["space"] for m in tracked}
    for space in store.spaces():
        if space.startswith(store.space_prefix) and space not in known:
            store.drop(space)


class ChromaMetaCatalog:
    """Catalog records in a small collection next to the vector collections."""

    def __init__(self, store: ChromaStore):
        self.store = store
        self.collection = store.client.get_or_create_collection(META_COLLECTION,
                                                                embedding_function=None)

    def _put(self, record_id: str, document: str, metadata: dict) -> None:
        # Chroma needs a vector per record; these records are looked up by id only.
        self.collection.upsert(ids=[record_id], documents=[document], metadatas=[metadata],
                               embeddings=[[1.0]])

    def get(self, version):
        got = self.collection.get(ids=[f"version:{version}"], include=["documents"])
        return json.loads(got["documents"][0]) if got["ids"] else None

    def active(self):
        got = self.collection.get(ids=["active"], include=["metadatas"])
        return self.get(got["metadatas"][0]["version"]) if got["ids"] else None

    def versions(self):
        got = self.collection.get(where={"kind": "version"}, include=["documents"])
        return sorted((json.loads(d) for d in got["documents"]), key=version_key)

    def save(self, manifest, payload=None):
        # payload isn't stored: the retriever rebuilds it from the chunk texts.
        self._put(f"version:{manifest['version']}", json.dumps(manifest, ensure_ascii=False),
                  {"kind": "version", "version": manifest["version"]})

    def set_active(self, manifest):
        self.save(manifest)
        self._put("active", manifest["version"], {"kind": "active",
                                                   "version": manifest["version"]})

    def delete(self, manifest):
        self.collection.delete(ids=[f"version:{manifest['version']}"])

    def load_payload(self, manifest, store):
        return payload_from_records(store.fetch_documents(manifest["space"]))

    def cleanup_legacy(self, store):
        _drop_orphan_spaces(store, self.versions())
        for space in LEGACY_SPACES:
            store.drop(space)


def get_catalog(store: VectorStore, data_dir: Path) -> Catalog:
    if store.name == "chroma_remote":
        return ChromaMetaCatalog(store)
    return FileCatalog(data_dir)
