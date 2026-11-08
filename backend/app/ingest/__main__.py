"""Ingestion command line.

Everyday (local):
    python -m app.ingest                  fetch -> parse -> gates -> chunk/embed -> activate
    python -m app.ingest --skip-fetch     same, from the saved snapshots (no network)
    python -m app.ingest --fetch-only     only download snapshots
    python -m app.ingest --parse-only     only re-parse saved snapshots

Daily refresh (GitHub Actions, implementation-plan.md Phase 7):
    python -m app.ingest --stage          build a new version but DON'T make it live
    python -m app.ingest --activate V     make version V live (also rollback)
    python -m app.ingest --prune --keep 3 delete old versions, keep 3 for rollback
    python -m app.ingest --status         show the live version and all versions

Options: --report (field coverage table), --summary FILE (append a Markdown run summary,
e.g. $GITHUB_STEP_SUMMARY), --keep N (versions kept after activation, default 1),
--force (build a new version even if nothing changed).

When $GITHUB_OUTPUT is set, writes `version=<id>` and `changed=true|false` for later steps.
Exit code 1 if any source failed, a required field is missing or a quality gate fails;
the live index is never touched in that case.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from app.config import get_settings
from app.ingest.fetch import fetch_all, latest_snapshot
from app.ingest.parse import REQUIRED_FIELDS, parse_html
from app.registry import load_schemes, load_sources

REPORT_FIELDS = REQUIRED_FIELDS + ("min_lumpsum", "lock_in", "fund_manager")
EXPECTED_FACTS = Path(__file__).resolve().parents[2] / "tests" / "eval" / "data" \
    / "facts_expected.json"


def parse_all(data_dir: Path) -> list[dict]:
    sources, schemes = load_sources(data_dir), load_schemes(data_dir)
    parsed_dir = data_dir / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    docs = []
    for source in sources:
        snapshot = latest_snapshot(data_dir / "raw", source.id)
        if snapshot is None:
            logging.error("no snapshot for %s; run without --parse-only first", source.id)
            continue
        html_path, meta = snapshot
        scheme = schemes.get(source.scheme)
        doc = parse_html(html_path.read_text(encoding="utf-8"), source, meta,
                         scheme.name if scheme else source.scheme)
        (parsed_dir / f"{source.id}.json").write_text(
            json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        docs.append(doc)
    return docs


def _store_and_catalog(data_dir: Path):
    from app.ingest.catalog import get_catalog
    from app.vectorstore import get_vector_store

    store = get_vector_store(get_settings(), data_dir)
    return store, get_catalog(store, data_dir)


def index_all(docs: list[dict], data_dir: Path, activate: bool = True, keep: int = 1,
              force: bool = False):
    """Chunk, embed and index the parsed docs; write facts.json. Returns (stats, chunks)."""
    from app.embeddings import get_embedder
    from app.ingest.chunk import chunk_document
    from app.ingest.facts import build_facts
    from app.ingest.index import build_index
    from app.ingest.summary import facts_snapshot

    settings = get_settings()
    store, catalog = _store_and_catalog(data_dir)
    chunks = [c for doc in docs for c in chunk_document(doc)]
    stats = build_index(chunks, data_dir, get_embedder(settings.embed_model), store,
                        settings.embed_model, catalog=catalog, activate=activate, keep=keep,
                        force=force, facts=facts_snapshot(docs))
    (data_dir / "facts.json").write_text(
        json.dumps(build_facts(docs, chunks), indent=2, ensure_ascii=False), encoding="utf-8")
    return stats, len(chunks)


def print_report(docs: list[dict], expected: int) -> None:
    def mark(doc, key):
        return "yes" if key in doc["fields"] else "MISSING" if key in REQUIRED_FIELDS else "-"

    width = max((len(d["source_id"]) for d in docs), default=10)
    print(f"\n{'source':<{width}}  " + "  ".join(f"{k:<13}" for k in REPORT_FIELDS) + "  doc_date")
    for d in docs:
        cells = "  ".join(f"{mark(d, k):<13}" for k in REPORT_FIELDS)
        print(f"{d['source_id']:<{width}}  {cells}  {d['doc_date']}")
    print(f"\nparsed {len(docs)}/{expected} sources")


def _github_output(**values) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for key, value in values.items():
                f.write(f"{key}={value}\n")


def _append_summary(path: str | None, text: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)


def _expected_facts() -> dict | None:
    return json.loads(EXPECTED_FACTS.read_text(encoding="utf-8")) \
        if EXPECTED_FACTS.exists() else None


# ---------------------------------------------------------------- version commands


def cmd_activate(version: str, data_dir: Path, summary: str | None) -> int:
    from app.ingest.index import activate_version

    store, catalog = _store_and_catalog(data_dir)
    previous = catalog.active()
    manifest = activate_version(version, store, catalog)
    print(f"version {version} is now live (was {previous['version'] if previous else '—'})")
    _append_summary(summary, f"### ✅ Activated\n\nVersion `{version}` is live "
                             f"(was `{previous['version'] if previous else '—'}`); data as on "
                             f"{manifest.get('corpus_date') or '—'}.\n\n")
    _github_output(version=version)
    return 0


def cmd_prune(keep: int, data_dir: Path, summary: str | None) -> int:
    from app.ingest.index import prune_versions

    store, catalog = _store_and_catalog(data_dir)
    deleted = prune_versions(store, catalog, keep=keep)
    print(f"pruned {len(deleted)} version(s): {', '.join(deleted) or 'none'} (keep {keep})")
    if deleted:
        _append_summary(summary, f"Pruned old versions: {', '.join(f'`{v}`' for v in deleted)}"
                                 "\n\n")
    return 0


def cmd_status(data_dir: Path) -> int:
    store, catalog = _store_and_catalog(data_dir)
    live = catalog.active()
    print(f"store: {store.name}; live version: {live['version'] if live else '—'}")
    for m in catalog.versions():
        state = "LIVE" if live and m["version"] == live["version"] else (
            "staged (never live)" if m.get("activated_at", "x") is None else "earlier")
        print(f"  {m['version']}  {state:<20} data as on {m.get('corpus_date')}  "
              f"built {m.get('built_at')}  chunks {len(m.get('chunk_ids', []))}")
    return 0


# ---------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.ingest")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fetch-only", action="store_true")
    mode.add_argument("--parse-only", action="store_true")
    mode.add_argument("--skip-fetch", action="store_true")
    mode.add_argument("--activate", metavar="VERSION")
    mode.add_argument("--prune", action="store_true")
    mode.add_argument("--status", action="store_true")
    parser.add_argument("--stage", action="store_true",
                        help="build a new version without making it live")
    parser.add_argument("--keep", type=int, default=None,
                        help="versions to keep (default: 1 after activation, 3 for --prune)")
    parser.add_argument("--force", action="store_true",
                        help="build a new version even if nothing changed")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--summary", metavar="FILE", help="append a Markdown run summary")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for noisy in ("httpx", "sentence_transformers", "chromadb", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    data_dir = get_settings().resolved_data_dir()

    if args.activate:
        return cmd_activate(args.activate, data_dir, args.summary)
    if args.prune:
        return cmd_prune(args.keep or 3, data_dir, args.summary)
    if args.status:
        return cmd_status(data_dir)

    from app.ingest import summary as run_summary
    from app.ingest.gates import check_docs

    sources = load_sources(data_dir)
    failed = False
    results = None

    if not (args.parse_only or args.skip_fetch):
        results = fetch_all(sources, data_dir / "raw")
        for r in results:
            status = f"ok via {r.method}" if r.ok else f"FAILED: {r.error}"
            warning = f" (warning: {r.warning})" if r.warning else ""
            print(f"fetch {r.source_id}: {status}{warning}")
        failed |= not all(r.ok for r in results)

    if args.fetch_only:
        return 1 if failed else 0

    docs = parse_all(data_dir)
    if args.report:
        print_report(docs, len(sources))
    if args.parse_only:
        return 1 if failed or any(d["missing_required"] for d in docs) else 0

    # Quality gates on the parsed data. Fetch failures count too: data from an older
    # snapshot must not be published as today's refresh.
    problems = check_docs(docs, expected_sources=len(sources),
                          max_age_days=7 if results is not None else None)
    if failed:
        problems.insert(0, "one or more sources failed to fetch")
    if problems:
        print("index NOT updated; quality gate problems:")
        for p in problems:
            print(f"  - {p}")
        _append_summary(args.summary, run_summary.render(
            fetch_results=results, docs=docs, problems=problems,
            headline="❌ Refresh stopped before staging; the live version is unchanged."))
        _github_output(changed="false")
        return 1

    _, catalog = _store_and_catalog(data_dir)
    previous = catalog.active()
    stats, total = index_all(docs, data_dir, activate=not args.stage,
                             keep=args.keep or 1, force=args.force)
    outcome = ("no changes, live version kept" if stats.version is None else
               f"version {stats.version} staged (not live)" if not stats.activated else
               f"version {stats.version} live")
    print(f"index ({get_settings().vector_store}): {total} chunks — added {stats.added}, "
          f"text changed {stats.updated}, metadata-only {stats.metadata_updated}, "
          f"deleted {stats.deleted}, unchanged {stats.unchanged}; embedded {stats.embedded}; "
          f"{outcome}")
    _github_output(version=stats.version or "", changed=str(stats.version is not None).lower())
    _append_summary(args.summary, run_summary.render(
        fetch_results=results, docs=docs, stats=stats, previous=previous,
        expected=_expected_facts()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
