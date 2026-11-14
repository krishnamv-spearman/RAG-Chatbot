"""In-process data refresh for a backend with a LOCAL index on a persistent disk
(VECTOR_STORE=chroma). Not used in the Render + Chroma Cloud production setup, where the
daily workflow builds new versions on the GitHub runner instead ("chroma" mode).

In "app" mode the daily GitHub Actions job calls `POST /api/admin/refresh`; the app runs, in a
background thread and in its own process (so it shares the app's ChromaDB client):

    fetch -> parse -> quality gates -> stage new version -> retrieval eval on it
          -> activate -> prune old versions -> switch the running pipeline to it

Same steps and gates as the CLI (`python -m app.ingest --stage/--activate/--prune`).
Any failure leaves the live version untouched. Only one refresh runs at a time.
"""

import json
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.ingest.normalize import utc_now_iso

logger = logging.getLogger("mf_faq.refresh")


@dataclass
class RefreshJob:
    id: str
    trigger: str
    force: bool
    state: str = "running"  # running | succeeded | no_changes | failed
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str | None = None
    version: str | None = None
    previous_version: str | None = None
    problems: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class Refresher:
    def __init__(self, data_dir: Path, store: Any, catalog: Any, embedder: Any,
                 embed_model: str, relevance_threshold: float,
                 on_activated: Callable[[], Any] | None = None,
                 evaluate: bool = True, keep: int = 3):
        self.data_dir, self.store, self.catalog = Path(data_dir), store, catalog
        self.embedder, self.embed_model = embedder, embed_model
        self.relevance_threshold = relevance_threshold
        self.on_activated, self.evaluate, self.keep = on_activated, evaluate, keep
        self._lock = threading.Lock()
        self.current: RefreshJob | None = None
        self.last: RefreshJob | None = None

    # ------------------------------------------------------------ public API

    def start(self, force: bool = False, trigger: str = "api",
              wait: bool = False) -> tuple[RefreshJob, bool]:
        """Start a refresh in the background. Returns (job, started); if one is already
        running, returns it with started=False. `wait=True` runs it inline (tests/CLI)."""
        with self._lock:
            if self.current is not None:
                return self.current, False
            job = RefreshJob(id=uuid.uuid4().hex[:12], trigger=trigger, force=force)
            self.current = job
        if wait:
            self._run(job)
        else:
            threading.Thread(target=self._run, args=(job,), name=f"refresh-{job.id}",
                             daemon=True).start()
        return job, True

    def status(self) -> RefreshJob | None:
        return self.current or self.last

    # ------------------------------------------------------------ the job

    def _run(self, job: RefreshJob) -> None:
        logger.info("refresh started", extra={"job": job.id, "trigger": job.trigger})
        try:
            self._steps(job)
        except Exception as exc:  # noqa: BLE001 - any failure must leave the live data alone
            job.state = "failed"
            job.problems.append(f"{type(exc).__name__}: {exc}")
            logger.exception("refresh crashed", extra={"job": job.id})
        finally:
            job.finished_at = utc_now_iso()
            with self._lock:
                self.last, self.current = job, None
            logger.info("refresh finished", extra={"job": job.id, "state": job.state,
                                                   "index_version": job.version,
                                                   "problems": job.problems})

    def _steps(self, job: RefreshJob) -> None:
        from app.ingest import summary as run_summary
        from app.ingest.__main__ import parse_all
        from app.ingest.chunk import chunk_document
        from app.ingest.facts import build_facts
        from app.ingest.fetch import fetch_all
        from app.ingest.gates import check_docs
        from app.ingest.index import activate_version, build_index, prune_versions
        from app.registry import load_sources

        sources = load_sources(self.data_dir)
        results = fetch_all(sources, self.data_dir / "raw")
        docs = parse_all(self.data_dir)
        problems = check_docs(docs, expected_sources=len(sources))
        if not all(r.ok for r in results):
            problems.insert(0, "one or more sources failed to fetch: " + ", ".join(
                f"{r.source_id} ({r.error})" for r in results if not r.ok))
        previous = self.catalog.active()
        job.previous_version = previous["version"] if previous else None
        if problems:
            job.state, job.problems = "failed", problems
            job.summary = run_summary.render(
                fetch_results=results, docs=docs, problems=problems,
                headline="❌ Refresh stopped before staging; the live version is unchanged.")
            return

        chunks = [c for d in docs for c in chunk_document(d)]
        stats = build_index(chunks, self.data_dir, self.embedder, self.store, self.embed_model,
                            catalog=self.catalog, activate=False, force=job.force,
                            facts=run_summary.facts_snapshot(docs))
        (self.data_dir / "facts.json").write_text(
            json.dumps(build_facts(docs, chunks), indent=2, ensure_ascii=False),
            encoding="utf-8")
        job.summary = run_summary.render(fetch_results=results, docs=docs, stats=stats,
                                         previous=previous)
        if stats.version is None:
            job.state = "no_changes"
            return
        job.version = stats.version

        gate_problems = self._retrieval_gate(stats.version) if self.evaluate else []
        if gate_problems:
            job.state, job.problems = "failed", gate_problems
            job.summary += "### ❌ Retrieval eval failed on the staged version\n\n" + \
                "\n".join(f"- {p}" for p in gate_problems) + "\n"
            return

        activate_version(stats.version, self.store, self.catalog)
        pruned = prune_versions(self.store, self.catalog, keep=self.keep)
        job.summary += (f"### ✅ Activated\n\nVersion `{stats.version}` is live (was "
                        f"`{job.previous_version or '—'}`).\n\n")
        if pruned:
            job.summary += f"Pruned: {', '.join(f'`{v}`' for v in pruned)}\n"
        job.state = "succeeded"
        if self.on_activated:
            self.on_activated()

    def _retrieval_gate(self, version: str) -> list[str]:
        """Run the Phase 2/3 retrieval eval against the staged version (no LLM calls)."""
        try:
            from tests.eval import retrieval_eval
        except ImportError:
            logger.warning("retrieval eval data not available; skipping the retrieval gate")
            return []
        from app.registry import load_schemes
        from app.retrieval import Retriever

        retriever = Retriever(self.data_dir, self.embedder, self.relevance_threshold,
                              store=self.store, schemes=load_schemes(self.data_dir),
                              catalog=self.catalog, version=version)
        report = retrieval_eval.evaluate(retriever, retrieval_eval.load_items())
        return [f"{name}: {value:.3f} (gate {gate})"
                for name, value, gate, ok in retrieval_eval.check_gates(report["metrics"])
                if not ok]
