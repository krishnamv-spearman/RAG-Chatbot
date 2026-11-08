"""Phase 2 fact-extraction evaluation: data/facts.json vs hand-verified facts_expected.json.

    python -m tests.eval.facts_eval

Gate (docs/eval.md, Phase 2): field accuracy >= 95% overall and 100% on the 5 required
fields. A mismatch after re-ingestion means either a parsing bug or that Groww changed a
value: check the live page before updating facts_expected.json.
"""

import json
import sys
from pathlib import Path

from app.config import get_settings
from app.ingest.parse import REQUIRED_FIELDS

EXPECTED = Path(__file__).parent / "data" / "facts_expected.json"


def compare(actual: dict, expected: dict) -> tuple[list[dict], int]:
    mismatches, checked = [], 0
    for scheme, fields in expected.items():
        if scheme.startswith("_"):
            continue
        got = actual.get(scheme, {}).get("facts", {})
        for key, value in fields.items():
            checked += 1
            if got.get(key, {}).get("value") != value:
                mismatches.append({"scheme": scheme, "field": key, "expected": value,
                                   "got": got.get(key, {}).get("value")})
    return mismatches, checked


def main() -> int:
    actual = json.loads((get_settings().resolved_data_dir() / "facts.json")
                        .read_text(encoding="utf-8"))
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    mismatches, checked = compare(actual, expected)
    accuracy = 1 - len(mismatches) / checked
    required_bad = [m for m in mismatches if m["field"] in REQUIRED_FIELDS]

    print(f"Fact extraction — {checked} fields checked (verified {expected['_verified_on']})")
    print(f"Field accuracy:           {accuracy:.1%}  (gate >= 95%)")
    print(f"Required-field mismatches: {len(required_bad)}  (gate = 0)")
    for m in mismatches:
        print(f"  MISMATCH {m['scheme']}.{m['field']}: "
              f"expected {m['expected']!r}, got {m['got']!r}")
    return 0 if accuracy >= 0.95 and not required_bad else 1


if __name__ == "__main__":
    sys.exit(main())
