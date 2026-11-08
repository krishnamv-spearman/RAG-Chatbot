"""Builds data/facts.json: the key facts per scheme, each linked to its source chunk.

Used as ground truth by the evals (values are compared against the hand-checked
tests/eval/data/facts_expected.json) and later as an extra lookup source.
"""

from app.ingest.chunk import Chunk

FACT_KEYS = (
    "expense_ratio", "exit_load", "min_sip", "min_lumpsum", "benchmark", "riskometer",
    "fund_manager", "lock_in",
)


def build_facts(docs: list[dict], chunks: list[Chunk]) -> dict:
    chunk_ids = {c.chunk_id for c in chunks}
    facts = {}
    for doc in docs:
        scheme_facts = {}
        for key in FACT_KEYS:
            field = doc["fields"].get(key)
            if not field:
                continue
            chunk_id = f"{doc['source_id']}#{key}"
            scheme_facts[key] = {
                "value": field["value"],
                "as_on": field.get("as_on"),
                "source_chunk_id": chunk_id if chunk_id in chunk_ids else None,
            }
        facts[doc["scheme"]] = {
            "name": doc["scheme_name"],
            "source_id": doc["source_id"],
            "url": doc["url"],
            "doc_date": doc.get("doc_date"),
            "facts": scheme_facts,
            "missing": [k for k in FACT_KEYS if k not in scheme_facts],
        }
    return facts
