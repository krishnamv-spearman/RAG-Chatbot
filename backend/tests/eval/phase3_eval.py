"""Phase 3 live end-to-end check (implementation-plan.md, Phase 3 exit criteria).

    python -m tests.eval.phase3_eval

Runs the REAL pipeline (retrieval + Groq) on tests/eval/data/phase3_live.jsonl:
20 factual questions and 3 the corpus can't answer. About 25 Groq calls (uses your
LLM_API_KEY quota). Expected values come from the hand-verified facts_expected.json.

Checks per answer: correct kind, correct fact value, cites the right scheme page,
<= 3 sentences, exactly one link, footer present. Exit code 1 on any failure.
"""

import json
import re
import sys
import time
from datetime import date
from pathlib import Path

from app.pipeline import build_pipeline
from app.validate import numbers_in, split_sentences

HERE = Path(__file__).parent
ITEMS = HERE / "data" / "phase3_live.jsonl"
EXPECTED = HERE / "data" / "facts_expected.json"
RESULTS = HERE / "results"
NUMERIC_FACTS = {"expense_ratio", "exit_load", "min_sip", "min_lumpsum"}
DELAY_S = 1.5  # stay well inside Groq free-tier rate limits


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def fact_present(fact: str, expected: str, text: str) -> bool:
    if fact in NUMERIC_FACTS:
        return numbers_in(expected) <= numbers_in(text)
    if fact == "fund_manager":
        names = [p.split(" (since")[0].strip() for p in expected.split("),")]
        return all(_norm(n) in _norm(text) for n in names if n)
    return _norm(expected) in _norm(text)


def format_ok(rendered: str, body: str) -> list[str]:
    problems = []
    if len(split_sentences(body)) > 3:
        problems.append("more than 3 sentences")
    if len(re.findall(r"https?://", rendered)) != 1:
        problems.append("not exactly one link")
    if not re.search(r"\nLast updated from sources: \d{2} [A-Z][a-z]{2} \d{4}$", rendered):
        problems.append("footer missing or malformed")
    return problems


def main() -> int:
    items = [json.loads(line) for line in ITEMS.read_text(encoding="utf-8").splitlines() if line]
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    pipeline = build_pipeline()
    rows, passed = [], 0
    for n, item in enumerate(items):
        if n:
            time.sleep(DELAY_S)
        response = pipeline.answer(item["question"], scheme=item["scheme"])
        rendered = response.render()
        problems = format_ok(rendered, response.text)
        if response.kind != item["expected_kind"]:
            problems.append(f"kind {response.kind}, expected {item['expected_kind']}")
        if item["fact"] and response.kind == "answer":
            value = expected[item["scheme"]][item["fact"]]
            if not fact_present(item["fact"], value, response.text):
                problems.append(f"expected value {value!r} not in answer")
            if response.source_url != pipeline.pages[item["scheme"]].url:
                problems.append("cites the wrong scheme page")
        ok = not problems
        passed += ok
        rows.append({"id": item["id"], "ok": ok, "kind": response.kind, "text": response.text,
                     "source_url": response.source_url, "footer": response.footer,
                     "problems": problems, "debug": response.debug})
        print(f"{'PASS' if ok else 'FAIL'} {item['id']} [{response.kind}] {response.text}")
        for p in problems:
            print(f"      - {p}")

    factual = [r for r, i in zip(rows, items, strict=True) if i["expected_kind"] == "answer"]
    llm_calls = sum(r["debug"].get("llm_calls", 0) for r in rows)
    latencies = sorted(r["debug"]["latency_ms"] for r in rows)
    summary = {
        "date": date.today().isoformat(),
        "passed": passed, "total": len(rows),
        "factual_passed": sum(r["ok"] for r in factual), "factual_total": len(factual),
        "llm_calls": llm_calls,
        "latency_ms_p50": latencies[len(latencies) // 2],
        "latency_ms_max": latencies[-1],
    }
    print(f"\n{passed}/{len(rows)} passed (factual {summary['factual_passed']}/"
          f"{summary['factual_total']}); {llm_calls} LLM calls; latency p50 "
          f"{summary['latency_ms_p50']} ms, max {summary['latency_ms_max']} ms")
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"phase3-live-{summary['date']}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"Saved {out}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
