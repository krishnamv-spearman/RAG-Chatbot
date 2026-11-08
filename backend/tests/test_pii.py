"""PII guard: every item in tests/eval/data/pii.jsonl, plus the gates from docs/eval.md
(recall 100% per type, precision >= 95%, zero false positives on normal amounts)."""

import json
from collections import defaultdict
from pathlib import Path

import pytest

from app.guards.pii import PIIType, detect, verhoeff_valid

ITEMS = [json.loads(line) for line in
         (Path(__file__).parent / "eval" / "data" / "pii.jsonl").read_text(
             encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("item", ITEMS, ids=[i["text"][:40] for i in ITEMS])
def test_pii_item(item):
    assert [str(t) for t in detect(item["text"])] == sorted(item["expected"])


def test_at_least_10_positives_and_negatives_per_type():
    positives = defaultdict(int)
    for item in ITEMS:
        for t in item["expected"]:
            positives[t] += 1
    negatives = sum(1 for i in ITEMS if not i["expected"])
    for t in PIIType:
        assert positives[str(t)] >= 10, t
    assert negatives >= 30


def test_recall_and_precision_gates():
    tp = fp = fn = 0
    for item in ITEMS:
        got = {str(t) for t in detect(item["text"])}
        want = set(item["expected"])
        tp += len(got & want)
        fp += len(got - want)
        fn += len(want - got)
    assert fn == 0  # recall 100%
    assert tp / (tp + fp) >= 0.95  # precision


def test_returns_types_never_values():
    result = detect("PAN ABCDE1234F, phone 9876543210")
    assert all(isinstance(t, PIIType) for t in result)
    assert "ABCDE1234F" not in repr(result) and "9876543210" not in repr(result)


def test_verhoeff():
    assert verhoeff_valid("234567890124")
    assert not verhoeff_valid("234567890125")


def test_empty_text():
    assert detect("") == [] and detect(None) == []
