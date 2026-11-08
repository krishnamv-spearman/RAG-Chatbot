import json

import groq
import httpx

from app.followup import FollowUpRewriter, fallback_rewrite
from app.registry import load_schemes
from app.retrieval import scheme_terms
from app.router import ConversationContext
from tests.test_llm import FakeClient, completion

TERMS = scheme_terms(load_schemes())
CTX = ConversationContext(last_scheme="hdfc-mid-cap", history=[
    ("user", "What is the exit load of HDFC Mid Cap Fund?"),
    ("assistant", "HDFC Mid Cap Fund has an exit load of 1% if redeemed within 1 year."),
])


def rewriter(*responses):
    client = FakeClient(*responses)
    return FollowUpRewriter(client, "openai/gpt-oss-120b", TERMS), client


def test_llm_rewrite_used_when_valid():
    rw, client = rewriter(completion(json.dumps(
        {"standalone_query": "What is the exit load of HDFC Small Cap Fund?"})))
    query, method = rw.rewrite("and small cap?", CTX, "HDFC Small Cap Fund")
    assert (query, method) == ("What is the exit load of HDFC Small Cap Fund?", "llm")
    req = client.requests[0]
    assert req["temperature"] == 0
    assert req["response_format"]["json_schema"]["strict"] is True
    user = req["messages"][1]["content"]
    assert "What is the exit load of HDFC Mid Cap Fund?" in user  # conversation included
    assert "Scheme in focus: HDFC Small Cap Fund" in user


def test_api_error_falls_back_without_llm():
    # FU-10: a failed rewrite never blocks the answer.
    req = httpx.Request("POST", "https://api.groq.com")
    rw, _ = rewriter(groq.APIConnectionError(request=req))
    query, method = rw.rewrite("and small cap?", CTX, "HDFC Small Cap Fund")
    assert method == "fallback" and query == "what is the exit load of HDFC Small Cap Fund?"


def test_bad_json_or_truncation_falls_back():
    for bad in (completion("not json"),
                completion(json.dumps({"standalone_query": "x"}), finish_reason="length"),
                completion(json.dumps({"standalone_query": "   "}))):
        rw, _ = rewriter(bad)
        assert rw.rewrite("and small cap?", CTX, "HDFC Small Cap Fund")[1] == "fallback"


def test_fallback_swaps_the_scheme_in_the_previous_question():
    assert fallback_rewrite("and small cap?", CTX, "HDFC Small Cap Fund", TERMS) == \
        "what is the exit load of HDFC Small Cap Fund?"


def test_fallback_without_history_keeps_the_question():
    empty = ConversationContext()
    assert fallback_rewrite("and small cap?", empty, "HDFC Small Cap Fund", TERMS) == \
        "and small cap?"
