# Data Research: Chunking, Embedding Model and Vector Store

> **Question:** given the actual cleaned corpus (`data/parsed/`, output of Phase 1), what chunking strategy, embedding model and vector store should the system use?
> **Date:** 07 Nov 2026 · **Corpus:** 5 Groww HDFC scheme pages · **Decisions feed:** [implementation-plan.md](implementation-plan.md), Phase 3 ("Retrieval configuration").
>
> Reproduce: `python -m tests.eval.retrieval_experiments [--no-scheme]` (from `backend/`). Raw results: `backend/tests/eval/results/experiments-{filtered,noscheme}-2026-11-07.json`.

## 1. Summary of Decisions

| Area | Decision | Main evidence |
|---|---|---|
| **Chunking** | **Keep one chunk per fact** (19 per scheme, 95 total). No token-window splitting needed. | Top-1 accuracy equal or better than coarser chunks with **5–20× less context** (~125 vs 600–2,470 tokens); fact-level "as on" dates and grounding |
| **Query processing** (new) | **Rank on the question with the scheme name removed** (when the scheme is known); **gate on the original question** | Top-1 **92.5% → 100%** on every model tested; gate margin unchanged |
| **Embedding model** | **Keep `BAAI/bge-small-en-v1.5`** | After query processing, all 4 models reach 100% top-1; bge-small has a healthy gate margin (0.129) at 1/3 the latency and size of bge-base |
| **Search mode** | **Keep hybrid** (BM25 + vector, RRF) | Beats vector-only and BM25-only in every configuration; BM25-only fails 3/5 multi-fact questions without a scheme |
| **Vector store** | Research recommended an in-process exact (numpy) index. **Superseded by project decision (07 Nov 2026): ChromaDB (default) or Pinecone (optional)** behind a `VectorStore` interface; see implementation-plan.md, Phase 3, R1 | 95 × 384 vectors = 146 KB; exact search 0.013 ms vs Chroma 1.09 ms; both negligible. The sync and atomic-rebuild concerns are handled in R1 |

## 2. What the Data Looks Like

| Measure | Value |
|---|---|
| Documents | 5 (one per scheme) |
| Sections / chunks (current) | 19 per scheme, **95 total** |
| Tokens per chunk (bge tokenizer) | min 19 · **median 29** · mean 33 · max 142 |
| Whole corpus | **3,149 tokens** (~630 per scheme) |
| Chunks over 256 tokens | 0 (the model limit is 512) |
| Longest fields | top holdings (66–142), objective (37–44), AUM (41–44), fund manager (40–43) |
| Fields with dates | expense ratio, NAV, AUM, top holdings |
| Daily-changing fields | NAV, AUM |

**Findings that shape the design:**

1. **The corpus is tiny.** 3,149 tokens would fit in a single LLM prompt. Retrieval is still worth having, because it picks the *one* fact to cite, enables the relevance gate and keeps the prompt small. But approximate-search infrastructure isn't needed (§6).
2. **Facts are short, self-contained sentences.** The plan's ~350-token windows would never trigger: no chunk is over 142 tokens.
3. **7 of 19 fields have the same value in all 5 schemes:** `min_sip` (₹100), `min_lumpsum`, `min_additional_investment`, `riskometer` ("Very High"), `plan`, `fund_house`, `stamp_duty`. Their chunks differ only by scheme name, so **picking the right scheme matters more than finding the right fact**. Without the scheme filter, a "minimum SIP" question matches 5 near-identical chunks.
4. **Every chunk repeats the long scheme name** (e.g. "HDFC Large Cap Fund Direct Growth"). In short chunks the name dominates the embedding (§5).

## 3. Method

- **Test set:** `tests/eval/data/retrieval.jsonl`: 40 answerable questions (every fact type, 10 synonym questions, 5 with no scheme given), 7 off-domain, 3 in-domain but not in the corpus. Plus **5 multi-fact questions** (two facts at once, edge case IN-09) defined in the experiment script.
- **Chunking strategies:**
  - `per_fact`: current; one chunk per fact
  - `grouped`: 4 topic chunks per scheme (key facts / about / objective / portfolio)
  - `page_windows`: plan's original ~350-token windows over the page, ~50 overlap
  - `scheme_doc`: one chunk per scheme page
- **Embedding models** (all local, CPU):
  - `bge-small-en-v1.5`: 384 dimensions, ~130 MB
  - `bge-base-en-v1.5`: 768 dimensions, ~440 MB
  - `all-MiniLM-L6-v2`: 384 dimensions, ~90 MB
  - `e5-small-v2`: 384 dimensions, ~130 MB
  - Each model uses the query/passage prefixes it was trained with.
- **Search modes:** hybrid (BM25 + vector, RRF k=60), vector only, BM25 only.
- **Two conditions:** with the scheme filter (as when the router resolves the scheme) and **without** it (harder; tests telling schemes apart).
- **Metrics:**
  - top-1 accuracy (the correct fact ranked first, which is what gets cited)
  - Recall@5 and MRR
  - multi-fact full recall (both facts in the top 5)
  - wrong-scheme top-1 for no-scheme questions
  - context size of the top 5 (tokens sent to the LLM)
  - **gate margin**: lowest in-scope similarity minus highest off-domain similarity. Positive means a threshold can separate them.
- Everything ran in memory with exact cosine search, so the comparison isolates data, chunking and model from the database.

## 4. Chunking Results

Hybrid search, **no scheme filter** (the harder condition):

| Strategy | Chunks | Top-1 (bge-small / bge-base) | Multi-fact | Context of top 5 |
|---|---|---|---|---|
| **per_fact** | 95 | 0.925 / 0.950 | 5/5 | **~125 tokens** |
| grouped | 20 | 0.950 / 0.975 | 5/5 | ~800 tokens |
| page_windows | 10 | 0.850 / 0.850 | 5/5 | ~1,440 tokens |
| scheme_doc | 5 | 1.000 / 1.000 | 5/5 | ~2,470 tokens |

**Reading the table:**
- `scheme_doc` "wins" only because top 5 of 5 documents is the **entire corpus**: it isn't retrieving, it's sending everything. That removes the relevance gate's meaning, makes the LLM search 2,470 tokens for one number, and loses fact-level "as on" dates.
- `grouped` gains at most one question out of 40 for **6–7× more context**. Its chunks mix facts with different dates (expense ratio "as on 04 Nov" next to undated exit load), which weakens the footer date and the grounding check.
- `page_windows` (the plan's original idea) is the worst: windows cut across unrelated facts.
- `per_fact` keeps each citation pointing at exactly the sentence that supports the answer, with its own date. Multi-fact questions still work, because both facts land in the top 5 (5/5).

**Decision:** keep `per_fact`. Keep the window-splitting code as a safety net for unusually long sections; at current sizes it never triggers.

## 5. Query Processing: the Biggest Win

All top-1 misses with `per_fact` (any model) had the same cause:

| Question | Ranked first instead |
|---|---|
| "What is the TER of HDFC Large Cap?" | *category* chunk |
| "what stocks does mid cap hold" | *category* chunk |
| "What does HDFC Silver ETF FoF invest in?" | *category* chunk |

The category chunk is almost all scheme name ("HDFC Large Cap Fund Direct Growth belongs to the category Equity – Large Cap"). When a question names the scheme but uses unusual words for the fact, the name match wins.

Once the scheme is known (the filter already restricts results to it), its name carries no information for ranking. So the fix is to **remove the scheme name and aliases** (plus "HDFC", "fund", "FoF") from the question used for ranking, while **keeping plan/option words** ("direct plan", "regular"):

| Model | Top-1 before | Top-1 after | Gate margin (original question) |
|---|---|---|---|
| bge-small-en-v1.5 | 0.925 | **1.000** | 0.129 |
| bge-base-en-v1.5 | 0.950 | **1.000** | 0.135 |
| e5-small-v2 | 0.950 | **1.000** | 0.065 |
| all-MiniLM-L6-v2 | 0.925 | **1.000** | 0.166 |

**Important:** the gate must use the **original** question. Running the gate on the stripped question made the margin **negative** for every model (e.g. bge-small: −0.048), because "what is the ter of" looks like nothing. Proposed rule:
- **Gate:** original question, best vector similarity ≥ `RELEVANCE_THRESHOLD`.
- **Ranking:** stripped question, only when a scheme is resolved; otherwise the original question.
- Cost: one extra query embedding (~12 ms) when the scheme is known.

## 6. Embedding Model Results

Hybrid, `per_fact`, scheme filter on:

| Model | Top-1 | MRR | Gate margin | In-scope min / off-domain max | Query time (CPU) | Size |
|---|---|---|---|---|---|---|
| **bge-small-en-v1.5** | 0.925 → **1.0** with §5 | 0.954 | **0.129** | 0.632 / 0.503 | ~12 ms | ~130 MB |
| bge-base-en-v1.5 | 0.950 → 1.0 | 0.971 | 0.135 | 0.562 / 0.427 | ~31 ms | ~440 MB |
| all-MiniLM-L6-v2 | 0.925 → 1.0 | 0.944 | 0.166 | 0.355 / 0.189 | ~5 ms | ~90 MB |
| e5-small-v2 | 0.950 → 1.0 | 0.975 | **0.065** | 0.852 / 0.787 | ~12 ms | ~130 MB |

- **Ranking quality is no longer a differentiator** after query processing (all 100%). Before it, models differed by one question in 40, which is within noise for a 40-item set.
- **Gate margin** decides robustness. e5-small packs all similarities into a narrow high band (0.75–0.85), leaving only 0.065 between real and off-topic questions, so it's fragile. bge-base's margin is barely larger than bge-small's (0.135 vs 0.129) at ~3× the latency and size. MiniLM has the largest margin but the weakest vector-only ranking without a scheme (wrong scheme at top-1 three times with `grouped`).
- **Decision:** keep **bge-small-en-v1.5** and `RELEVANCE_THRESHOLD = 0.57` (the gap is 0.503–0.632). Revisit bge-base only if the Phase 8 held-out eval shows ranking errors that query processing doesn't fix.

## 7. Search Mode Results

| Mode (per_fact, bge-small, no scheme filter) | Top-1 | Multi-fact | Wrong scheme at top-1 (no-scheme questions) |
|---|---|---|---|
| **Hybrid** | **0.925** | **5/5** | **0** |
| Vector only | 0.850 | 5/5 | 1 |
| BM25 only | 0.900 | 2/5 | 2 |

Hybrid is best or tied in every configuration tested, so **keep hybrid**. Keyword search catches exact terms (ISIN, "stamp duty", numbers); vector search catches paraphrases ("Is there any charge if I redeem early?" → exit load).

## 8. Vector Store

| Measure | ChromaDB (current) | In-process exact numpy |
|---|---|---|
| Data size | 978 KB on disk (SQLite + HNSW files) | 146 KB matrix (95 × 384 float32) |
| Filtered top-20 query | 1.09 ms | **0.013 ms** |
| Results | Same as exact at this size (checked) | Exact by definition |
| Dependencies | `chromadb` + 27 direct requirements (incl. kubernetes, grpcio, opentelemetry, onnxruntime) | `numpy` (already installed) |
| Failure modes | Two stores (Chroma + `bm25.json`) can drift out of sync; partial writes | One file with vectors + BM25 tokens + metadata, written atomically |
| Atomic rebuild (edge case GAP-33) | Hard (database directory) | Trivial (write a temp file, then rename) |

Both are negligible next to query embedding (~12 ms) and the LLM call (~1 s), so **speed is not the reason**. The reasons are simplicity and correctness: one artefact, exact results, atomic swaps, no out-of-sync state, and a much smaller dependency tree.

> **Superseded (07 Nov 2026):** the project uses **ChromaDB by default, with Pinecone as an option**, behind a `VectorStore` interface (implementation-plan.md, Phase 3, R1). The recommendation below is kept for the record.

**Research recommendation:** replace ChromaDB with a small `VectorIndex` class (numpy matrix + metadata) saved together with the BM25 data in **one file** (e.g. `data/index.npz` + JSON, or a single JSON/NPZ bundle), behind an interface so a real vector database (Chroma, FAISS, pgvector) can be plugged back in. **When to revisit:** above roughly 50,000 chunks, or with many AMCs and frequent incremental updates, exact search starts costing milliseconds and a vector database becomes worthwhile. This corpus is 95 chunks.

## 9. Limitations of This Research

- **Small, self-written test set** (40 answerable questions, written while building the system). 100% top-1 here doesn't guarantee 100% on real users' wording. The Phase 8 held-out golden set must confirm it, and real misses should be added to `retrieval.jsonl`.
- Multi-fact coverage is only 5 questions.
- The not-in-corpus questions (statements, ELSS, tax) score 0.52–0.60, inside the "relevant" range. No model separates them, because they are on-topic. The LLM's `not_found` (Phase 3) must handle them, not the gate.
- Latency was measured on one Windows laptop CPU; relative differences matter more than absolute numbers.
