import pytest

from app.retrieval import rrf_fuse
from app.text import expand_query, tokenize


def test_tokenize_keeps_numbers_and_percentages_and_drops_stop_words():
    assert tokenize("What is the Expense Ratio? 0.74% and ₹100") == [
        "expense", "ratio", "0.74%", "100"]


@pytest.mark.parametrize("query, expected", [
    ("TER of large cap", "expense ratio"),
    ("risk level of multi cap", "riskometer"),
    ("who manages small cap", "fund manager"),
    ("fund size of mid cap", "aum"),
    ("lumpsum for silver", "lump sum"),
])
def test_synonyms_are_expanded(query, expected):
    assert expected in expand_query(query)


def test_synonyms_match_whole_words_only():
    # "risk" must not fire inside "riskometer"; "ter" must not fire inside "after".
    assert expand_query("after riskometer") == "after riskometer riskometer risk"


def test_query_without_synonyms_is_unchanged():
    assert expand_query("ISIN of mid cap") == "ISIN of mid cap"


def test_rrf_rewards_agreement_between_rankings():
    scores = rrf_fuse([["a", "b", "c"], ["b", "a", "d"]])
    assert scores["a"] == scores["b"] == pytest.approx(1 / 61 + 1 / 62)
    assert scores["a"] > scores["c"] and scores["a"] > scores["d"]
    assert scores["c"] == pytest.approx(1 / 63)
