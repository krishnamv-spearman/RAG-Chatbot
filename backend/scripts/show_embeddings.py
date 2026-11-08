"""Show the chunk embeddings stored in the active ChromaDB collection (read-only).

Run from backend/:

    python scripts/show_embeddings.py                         # summary + every chunk (first 8 dims)
    python scripts/show_embeddings.py --scheme hdfc-mid-cap   # one scheme only
    python scripts/show_embeddings.py --dims 16               # show more dimensions per row
    python scripts/show_embeddings.py --id "groww-hdfc-mid-cap#exit_load"   # one full vector
    python scripts/show_embeddings.py --export ../data/embeddings.csv       # all vectors to CSV
    python scripts/show_embeddings.py --export ../data/embeddings.json      # ...or JSON

The collection to read comes from data/index_manifest.json, i.e. exactly what the
retriever uses. Nothing is modified.
"""

import argparse
import csv
import json
import math
import sys
from pathlib import Path

from app.config import get_settings
from app.ingest.index import load_manifest
from app.vectorstore import ChromaStore


def norm(vec: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vec))


def load(scheme: str | None) -> tuple[dict, dict[str, tuple[list[float], dict]], dict[str, str]]:
    data_dir = get_settings().resolved_data_dir()
    manifest = load_manifest(data_dir)
    if manifest is None:
        sys.exit("No index found. Run `python -m app.ingest` first.")
    if manifest["store"] != "chroma":
        sys.exit(f"The active index is in {manifest['store']!r}, not ChromaDB.")
    store = ChromaStore(data_dir)
    rows = store.fetch(manifest["space"], sorted(store.ids(manifest["space"])))
    if scheme:
        rows = {i: r for i, r in rows.items() if r[1].get("scheme") == scheme}
    # Chunk texts live in the keyword file named by the manifest.
    payload = json.loads((data_dir / manifest["bm25_file"]).read_text(encoding="utf-8"))
    texts = dict(zip(payload["ids"], payload["texts"], strict=True))
    return manifest, rows, texts


def print_summary(manifest: dict, rows: dict) -> None:
    dims = {len(v) for v, _ in rows.values()}
    norms = [norm(v) for v, _ in rows.values()]
    schemes = sorted({m.get("scheme") for _, m in rows.values()})
    print("ChromaDB embeddings")
    print(f"  collection : {manifest['space']}  (build {manifest['version']})")
    print(f"  model      : {manifest['embed_model']}")
    print(f"  chunks     : {len(rows)} across {len(schemes)} scheme(s)")
    print(f"  dimensions : {', '.join(map(str, sorted(dims))) or '-'}")
    if norms:
        print(f"  vector norm: min {min(norms):.4f}  max {max(norms):.4f}  "
              "(1.0 = normalised, so cosine similarity = dot product)")
    print()


def print_table(rows: dict, dims: int) -> None:
    width = max((len(i) for i in rows), default=10)
    head = "  ".join(f"d{k:<6}" for k in range(dims))
    print(f"{'chunk_id':<{width}}  {'norm':>6}  {head}")
    print("-" * (width + 10 + 9 * dims))
    for cid in sorted(rows):
        vec, _ = rows[cid]
        values = "  ".join(f"{x:+.4f}" for x in vec[:dims])
        print(f"{cid:<{width}}  {norm(vec):6.3f}  {values}  …")


def print_one(cid: str, rows: dict, texts: dict) -> None:
    if cid not in rows:
        sys.exit(f"No chunk {cid!r}. Run without --id to list chunk ids.")
    vec, meta = rows[cid]
    print(f"chunk_id : {cid}")
    print(f"text     : {texts.get(cid, '')}")
    print(f"metadata : {json.dumps(meta, ensure_ascii=False)}")
    print(f"vector   : {len(vec)} dimensions, norm {norm(vec):.4f}\n")
    for start in range(0, len(vec), 8):
        cells = "  ".join(f"{x:+.5f}" for x in vec[start:start + 8])
        print(f"  [{start:3d}-{min(start + 7, len(vec) - 1):3d}]  {cells}")
    # The 5 chunks whose embeddings are closest to this one (cosine = dot product).
    sims = sorted(((sum(a * b for a, b in zip(vec, other, strict=True)), oid)
                   for oid, (other, _) in rows.items() if oid != cid), reverse=True)[:5]
    print("\nMost similar chunks (cosine similarity):")
    for sim, oid in sims:
        print(f"  {sim:.3f}  {oid}")


def export(path: Path, rows: dict, texts: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows)
    if path.suffix.lower() == ".json":
        data = [{"chunk_id": i, "text": texts.get(i, ""), "metadata": rows[i][1],
                 "embedding": rows[i][0]} for i in ordered]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        dim = len(rows[ordered[0]][0]) if ordered else 0
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["chunk_id", "scheme", "field", "text"] +
                            [f"d{k}" for k in range(dim)])
            for i in ordered:
                vec, meta = rows[i]
                writer.writerow([i, meta.get("scheme"), meta.get("field"), texts.get(i, "")] +
                                [f"{x:.6f}" for x in vec])
    print(f"Exported {len(rows)} embeddings to {path.resolve()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scheme", help="only this scheme, e.g. hdfc-mid-cap")
    parser.add_argument("--dims", type=int, default=8, help="dimensions per row in the table")
    parser.add_argument("--id", help="print one chunk's full vector and its nearest chunks")
    parser.add_argument("--export", type=Path, help="write all vectors to .csv or .json")
    args = parser.parse_args(argv)

    manifest, rows, texts = load(args.scheme)
    print_summary(manifest, rows)
    if args.id:
        print_one(args.id, rows, texts)
    elif args.export:
        export(args.export, rows, texts)
    else:
        print_table(rows, args.dims)
    return 0


if __name__ == "__main__":
    sys.exit(main())
