"""Router: every item in tests/eval/data/router.jsonl (edge cases IN, ADV, PERF, SCH, FU)."""

import json
from pathlib import Path

import pytest

from app.registry import load_schemes
from app.router import ConversationContext, Router, normalize

ITEMS = [json.loads(line) for line in
         (Path(__file__).parent / "eval" / "data" / "router.jsonl").read_text(
             encoding="utf-8").splitlines() if line.strip()]
ROUTER = Router(load_schemes())


def context_for(item) -> ConversationContext:
    c = item.get("context") or {}
    return ConversationContext(last_scheme=c.get("last_scheme"),
                               history=[tuple(t) for t in c.get("history", [])])


@pytest.mark.parametrize("item", ITEMS, ids=[i["id"] for i in ITEMS])
def test_route(item):
    route = ROUTER.route(item["text"], context_for(item))
    assert route.intent == item["intent"], route.reason
    if "scheme" in item:
        assert route.scheme == item["scheme"]
    for key in ("variant", "clarify", "from_context", "needs_rewrite"):
        if key in item:
            assert getattr(route, key) == item[key], key


def test_every_advisory_item_is_refused():
    advisory = [i for i in ITEMS if i["intent"] == "advisory"]
    assert len(advisory) >= 15
    assert all(ROUTER.route(i["text"], context_for(i)).intent == "advisory" for i in advisory)


def test_scheme_resolution_longest_match_and_order():
    assert ROUTER.find_schemes("silver etf fof") == ["hdfc-silver-etf-fof"]
    assert ROUTER.find_schemes("small cap and mid cap") == ["hdfc-small-cap", "hdfc-mid-cap"]
    assert ROUTER.find_schemes("large and mid cap fund") == []  # a different category
    assert ROUTER.find_schemes("the multi-cap fund") == ["hdfc-multi-cap"]


def test_fuzzy_matching_does_not_confuse_close_schemes():
    assert ROUTER.find_schemes("mid cpa") == ["hdfc-mid-cap"]
    assert ROUTER.find_schemes("multi cap") == ["hdfc-multi-cap"]  # exact wins over fuzzy
    assert ROUTER.find_schemes("what is a cap") == []


def test_normalize():
    assert normalize("Mid-Cap & Small-Cap!") == "mid cap and small cap"
