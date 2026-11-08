"""Data research: compare chunking strategies, embedding models and search modes on the
real parsed corpus (data/parsed/) using the labelled retrieval set.

    python -m tests.eval.retrieval_experiments [--models bge-small,minilm] [--out path.json]

Everything runs in memory (numpy cosine + rank_bm25), so the comparison is about the data,
the chunking and the model, not the vector store. No LLM calls. See docs/data-research.md.
"""

import argparse
import json
import statistics as st
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

from app.config import get_settings
from app.ingest.chunk import chunk_document
from app.retrieval import rrf_fuse
from app.text import expand_query, tokenize

DATA = Path(__file__).parent / "data" / "retrieval.jsonl"

MODELS = {
    # key: (hf id, query prefix, passage prefix)
    "bge-small": ("BAAI/bge-small-en-v1.5",
                  "Represent this sentence for searching relevant passages: ", ""),
    "bge-base": ("BAAI/bge-base-en-v1.5",
                 "Represent this sentence for searching relevant passages: ", ""),
    "minilm": ("sentence-transformers/all-MiniLM-L6-v2", "", ""),
    "e5-small": ("intfloat/e5-small-v2", "query: ", "passage: "),
}

# Questions asking for two facts at once (edge case IN-09). Full recall = both in top 5.
MULTI_FACT = [
    ("Expense ratio and exit load of HDFC Mid Cap Fund?", "hdfc-mid-cap",
     ["expense_ratio", "exit_load"]),
    ("What are the minimum SIP and minimum lump sum for HDFC Small Cap?", "hdfc-small-cap",
     ["min_sip", "min_lumpsum"]),
    ("Benchmark and riskometer of HDFC Large Cap Fund", "hdfc-large-cap",
     ["benchmark", "riskometer"]),
    ("Who manages HDFC Multi Cap and what is its expense ratio?", "hdfc-multi-cap",
     ["fund_manager", "expense_ratio"]),
    ("exit load and lock-in of silver ETF FoF", "hdfc-silver-etf-fof",
     ["exit_load", "lock_in_or_absent"]),
]

KEY_FACTS = ["expense_ratio", "exit_load", "min_sip", "min_lumpsum",
             "min_additional_investment", "benchmark", "riskometer", "lock_in", "plan",
             "category"]
ABOUT = ["fund_manager", "fund_house", "registrar", "launch_date", "isin", "stamp_duty"]
PORTFOLIO = ["aum", "nav", "top_holdings"]


@dataclass
class Unit:
    """A retrievable unit under some chunking strategy."""
    id: str
    scheme: str
    text: str
    fields: frozenset  # which facts this unit contains


# ------------------------------------------------------------------ chunking strategies

def per_fact(docs):
    return [Unit(c.chunk_id, c.metadata["scheme"], c.text, frozenset([c.metadata["field"]]))
            for d in docs for c in chunk_document(d)]


def grouped(docs):
    """Topic groups per scheme: key facts / about / objective / portfolio."""
    units = []
    for d in docs:
        by_field = {s["field"]: s for s in d["sections"]}
        for name, group in (("key_facts", KEY_FACTS), ("about", ABOUT),
                            ("objective", ["objective"]), ("portfolio", PORTFOLIO)):
            parts = [by_field[f] for f in group if f in by_field]
            if parts:
                text = f"{d['scheme_name']} — {name.replace('_', ' ')}: " + " ".join(
                    f"{p['heading']}: {p['text']}" for p in parts)
                units.append(Unit(f"{d['source_id']}#{name}", d["scheme"], text,
                                  frozenset(p["field"] for p in parts)))
    return units


def scheme_doc(docs):
    """One unit per scheme page (~630 tokens)."""
    return [Unit(f"{d['source_id']}#page", d["scheme"],
                 " ".join(f"{s['heading']}: {s['text']}" for s in d["sections"]),
                 frozenset(s["field"] for s in d["sections"])) for d in docs]


def page_windows(docs, words=260, overlap=38):
    """Plan's original idea: fixed ~350-token windows (~50 overlap) over the page text."""
    units = []
    for d in docs:
        pieces = [(s["field"], f"{s['heading']}: {s['text']}") for s in d["sections"]]
        flat = [(f, w) for f, t in pieces for w in t.split()]
        start, n = 0, 0
        while start < len(flat):
            window = flat[start:start + words]
            n += 1
            units.append(Unit(f"{d['source_id']}#w{n}", d["scheme"],
                              " ".join(w for _, w in window), frozenset(f for f, _ in window)))
            if start + words >= len(flat):
                break
            start += words - overlap
    return units


STRATEGIES = {"per_fact": per_fact, "grouped": grouped, "page_windows": page_windows,
              "scheme_doc": scheme_doc}


# ------------------------------------------------------------------ search

class Index:
    def __init__(self, units, model, model_key):
        self.units = units
        _, self.qp, pp = MODELS[model_key]
        self.model = model
        self.bm25 = BM25Okapi([tokenize(u.text) for u in units])
        self.vecs = model.encode([pp + u.text for u in units], normalize_embeddings=True)

    def search(self, query, scheme, mode, k=5):
        allowed = [i for i, u in enumerate(self.units) if scheme is None or u.scheme == scheme]
        q = self.model.encode(self.qp + query, normalize_embeddings=True)
        sims = self.vecs @ q
        vec_rank = sorted(allowed, key=lambda i: -sims[i])[:20]
        kw = self.bm25.get_scores(tokenize(expand_query(query)))
        kw_rank = [i for i in sorted(allowed, key=lambda i: -kw[i]) if kw[i] > 0][:20]
        if mode == "vector":
            order = vec_rank
        elif mode == "bm25":
            order = kw_rank
        else:
            fused = rrf_fuse([[str(i) for i in kw_rank], [str(i) for i in vec_rank]])
            order = [int(i) for i, _ in sorted(fused.items(), key=lambda p: -p[1])]
        best = float(max(sims[i] for i in allowed)) if allowed else 0.0
        return order[:k], best


def field_of(chunk_id):
    return chunk_id.split("#", 1)[1]


def run(index, items, mode, ignore_scheme=False):
    hits, rr, in_sims, off_sims, ctx_tokens, wrong_scheme_top1 = 0, 0.0, [], [], [], 0
    top1 = 0
    answerable = [i for i in items if i["in_scope"]]
    for item in items:
        scheme = None if ignore_scheme else item["scheme"]
        order, best = index.search(item["question"], scheme, mode)
        if not item["in_scope"]:
            if "off_domain" in item["tags"]:
                off_sims.append(best)
            continue
        in_sims.append(best)
        targets = {(r.split("#")[0], field_of(r)) for r in item["relevant"]}
        rank = next((n for n, i in enumerate(order, 1)
                     if any(f in index.units[i].fields and index.units[i].id.split("#")[0] == s
                            for s, f in targets)), None)
        hits += rank is not None
        top1 += rank == 1
        rr += 1 / rank if rank else 0
        ctx_tokens.append(sum(len(index.units[i].text.split()) for i in order) * 1.33)
        if scheme is None and order:
            expected_scheme = item["relevant"][0].split("#")[0]
            wrong_scheme_top1 += index.units[order[0]].id.split("#")[0] != expected_scheme

    multi_full = 0
    for question, scheme, fields in MULTI_FACT:
        order, _ = index.search(question, None if ignore_scheme else scheme, mode)
        got = set().union(*(index.units[i].fields for i in order)) if order else set()
        needed = [f for f in fields if f != "lock_in_or_absent"]
        multi_full += all(f in got for f in needed)

    n = len(answerable)
    return {
        "top1": round(top1 / n, 3),
        "recall_at_5": round(hits / n, 3),
        "mrr": round(rr / n, 3),
        "gate_margin": round(min(in_sims) - max(off_sims), 3),
        "in_min": round(min(in_sims), 3),
        "off_max": round(max(off_sims), 3),
        "no_scheme_wrong_top1": wrong_scheme_top1,
        "multi_fact_full_recall": f"{multi_full}/{len(MULTI_FACT)}",
        "context_tokens_top5": round(st.mean(ctx_tokens)),
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default=",".join(MODELS))
    parser.add_argument("--out", default=None)
    parser.add_argument("--no-scheme", action="store_true",
                        help="ignore the scheme filter on every question (harder test)")
    args = parser.parse_args(argv)

    from sentence_transformers import SentenceTransformer

    data_dir = get_settings().resolved_data_dir()
    docs = [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((data_dir / "parsed").glob("*.json"))]
    items = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line]
    results = []
    for model_key in args.models.split(","):
        t0 = time.perf_counter()
        model = SentenceTransformer(MODELS[model_key][0])
        load_s = time.perf_counter() - t0
        for strategy, build in STRATEGIES.items():
            units = build(docs)
            t0 = time.perf_counter()
            index = Index(units, model, model_key)
            embed_s = time.perf_counter() - t0
            for mode in ("hybrid", "vector", "bm25"):
                t0 = time.perf_counter()
                metrics = run(index, items, mode, ignore_scheme=args.no_scheme)
                per_query_ms = (time.perf_counter() - t0) / (len(items) + len(MULTI_FACT)) * 1000
                row = {"model": model_key, "strategy": strategy, "mode": mode,
                       "scheme_filter": not args.no_scheme,
                       "units": len(units), **metrics,
                       "query_ms": round(per_query_ms, 1), "embed_corpus_s": round(embed_s, 2),
                       "model_load_s": round(load_s, 1),
                       "dim": int(index.vecs.shape[1])}
                results.append(row)
                print(json.dumps(row), flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
