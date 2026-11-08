"""Phase 2 retrieval evaluation (docs/eval.md §4, Phase 2).

    python -m tests.eval.retrieval_eval [--threshold 0.6] [--verbose]

Needs a built index (python -m app.ingest). No LLM calls, so it's free to run.
Exit code 1 if any gate fails.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from app.config import get_settings
from app.embeddings import get_embedder
from app.retrieval import Retriever

DATA = Path(__file__).parent / "data" / "retrieval.jsonl"
RESULTS = Path(__file__).parent / "results"

GATES = {
    "top1": 0.95,  # the top chunk is what gets cited (Phase 3 exit criterion)
    "recall_at_5": 0.90,
    "mrr": 0.75,
    "synonym_recall_at_5": 0.90,
    "scheme_leakage_max": 0.0,
    "off_domain_below_threshold": 0.95,
    "in_scope_above_threshold": 0.95,
}


def load_items() -> list[dict]:
    return [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line]


def suggest_threshold(in_sims: list[float], off_sims: list[float]) -> float | None:
    """Midpoint of the gap between off-domain and in-scope similarities, if they separate."""
    if not in_sims or not off_sims or max(off_sims) >= min(in_sims):
        return None
    return round((max(off_sims) + min(in_sims)) / 2, 3)


def evaluate(retriever: Retriever, items: list[dict], verbose: bool = False) -> dict:
    hits, rr, syn_hits, syn_total, top1 = 0, 0.0, 0, 0, 0
    leaked, filtered_total = 0, 0
    in_sims, off_sims, nic_sims, misses = [], [], [], []
    in_scope = [i for i in items if i["in_scope"]]

    for item in items:
        result = retriever.retrieve(item["question"], scheme=item["scheme"], k=5)
        ids = [c.chunk_id for c in result.chunks]
        if not item["in_scope"]:
            bucket = off_sims if "off_domain" in item["tags"] else nic_sims
            bucket.append(result.best_similarity)
            if verbose:
                print(f"{item['id']} sim={result.best_similarity:.3f} [{item['tags'][0]}] "
                      f"{item['question']}")
            continue

        in_sims.append(result.best_similarity)
        rank = next((r for r, cid in enumerate(ids, 1) if cid in item["relevant"]), None)
        hits += rank is not None
        top1 += rank == 1
        rr += 1 / rank if rank else 0.0
        if "synonym" in item["tags"]:
            syn_total += 1
            syn_hits += rank is not None
        if item["scheme"]:
            filtered_total += len(result.chunks)
            leaked += sum(c.metadata["scheme"] != item["scheme"] for c in result.chunks)
        if rank != 1:
            misses.append({"id": item["id"], "question": item["question"], "rank": rank,
                           "got": ids[:3]})
        if verbose:
            print(f"{item['id']} rank={rank} sim={result.best_similarity:.3f} {item['question']}")

    threshold = retriever.threshold
    n = len(in_scope)
    return {
        "date": date.today().isoformat(),
        "threshold": threshold,
        "items": len(items),
        "store": retriever.store.name,
        "metrics": {
            "top1": top1 / n,
            "recall_at_5": hits / n,
            "mrr": rr / n,
            "synonym_recall_at_5": syn_hits / syn_total if syn_total else 1.0,
            "scheme_leakage": leaked / filtered_total if filtered_total else 0.0,
            "off_domain_below_threshold": sum(s < threshold for s in off_sims) / len(off_sims),
            "in_scope_above_threshold": sum(s >= threshold for s in in_sims) / len(in_sims),
        },
        "similarity": {
            "in_scope_min": min(in_sims), "in_scope_mean": sum(in_sims) / len(in_sims),
            "off_domain_max": max(off_sims), "off_domain_mean": sum(off_sims) / len(off_sims),
            "not_in_corpus": [round(s, 3) for s in nic_sims],
        },
        "suggested_threshold": suggest_threshold(in_sims, off_sims),
        "misses": misses,
    }


def check_gates(m: dict) -> list[tuple[str, float, str, bool]]:
    rows = [
        ("Top-1 accuracy", m["top1"], f">= {GATES['top1']:.0%}", m["top1"] >= GATES["top1"]),
        ("Recall@5", m["recall_at_5"], f">= {GATES['recall_at_5']:.0%}",
         m["recall_at_5"] >= GATES["recall_at_5"]),
        ("MRR", m["mrr"], f">= {GATES['mrr']}", m["mrr"] >= GATES["mrr"]),
        ("Synonym Recall@5", m["synonym_recall_at_5"], f">= {GATES['synonym_recall_at_5']:.0%}",
         m["synonym_recall_at_5"] >= GATES["synonym_recall_at_5"]),
        ("Scheme leakage", m["scheme_leakage"], "= 0%",
         m["scheme_leakage"] <= GATES["scheme_leakage_max"]),
        ("Off-domain below gate", m["off_domain_below_threshold"],
         f">= {GATES['off_domain_below_threshold']:.0%}",
         m["off_domain_below_threshold"] >= GATES["off_domain_below_threshold"]),
        ("In-scope above gate", m["in_scope_above_threshold"],
         f">= {GATES['in_scope_above_threshold']:.0%}",
         m["in_scope_above_threshold"] >= GATES["in_scope_above_threshold"]),
    ]
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--version", default=None,
                        help="evaluate a specific (e.g. staged, not yet live) index version")
    args = parser.parse_args(argv)

    settings = get_settings()
    threshold = settings.relevance_threshold if args.threshold is None else args.threshold
    retriever = Retriever(settings.resolved_data_dir(), get_embedder(settings.embed_model),
                          threshold, version=args.version)
    print(f"Evaluating index version {retriever.version}")
    report = evaluate(retriever, load_items(), verbose=args.verbose)

    print(f"\nRetrieval eval — {report['date']} — {report['items']} items — "
          f"store {report['store']} — threshold {report['threshold']}")
    print(f"{'Metric':<24}{'Value':>8}  {'Gate':<8}  Status")
    rows = check_gates(report["metrics"])
    for name, value, gate, ok in rows:
        print(f"{name:<24}{value:>8.3f}  {gate:<8}  {'PASS' if ok else 'FAIL'}")
    s = report["similarity"]
    print(f"\nSimilarity: in-scope min {s['in_scope_min']:.3f} / mean {s['in_scope_mean']:.3f}; "
          f"off-domain max {s['off_domain_max']:.3f} / mean {s['off_domain_mean']:.3f}")
    print(f"Not-in-corpus similarities (informational): {s['not_in_corpus']}")
    print(f"Suggested threshold: {report['suggested_threshold']}")
    for miss in report["misses"]:
        print(f"NOT TOP-1 {miss['id']} (rank {miss['rank']}): {miss['question']} "
              f"-> {miss['got']}")

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"retrieval-{report['date']}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSaved {out}")
    return 0 if all(ok for *_, ok in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
