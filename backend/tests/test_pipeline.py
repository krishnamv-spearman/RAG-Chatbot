"""Pipeline tests with a fake retriever and a fake LLM (no model, no network)."""

import json

from app.llm import LLMAnswer, LLMResult
from app.pipeline import Pipeline
from app.retrieval import RetrievalResult, RetrievedChunk

URL_MID = "https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth"
URL_SMALL = "https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth"
AMFI = "https://www.amfiindia.com/investor"


def meta(scheme, url, name):
    return {"scheme": scheme, "url": url, "scheme_name": name, "doc_date": "2026-11-06",
            "fetched_at": "2026-11-07T10:00:00+00:00", "as_on": ""}


MID = meta("hdfc-mid-cap", URL_MID, "HDFC Mid Cap Fund Direct Growth")
SMALL = meta("hdfc-small-cap", URL_SMALL, "HDFC Small Cap Fund Direct Growth")
ER = RetrievedChunk("groww-hdfc-mid-cap#expense_ratio",
                    "Expense ratio: The expense ratio of HDFC Mid Cap Fund Direct Growth is "
                    "0.74% (as on 04 Nov 2026).",
                    {**MID, "as_on": "2026-11-04", "field": "expense_ratio"}, 0.03, 0.9)
EXIT = RetrievedChunk("groww-hdfc-mid-cap#exit_load",
                      "Exit load: Exit load of 1% if redeemed within 1 year.",
                      {**MID, "field": "exit_load"}, 0.02, 0.7)
SMALL_ER = RetrievedChunk("groww-hdfc-small-cap#expense_ratio",
                          "Expense ratio: HDFC Small Cap Fund Direct Growth is 0.79%.",
                          {**SMALL, "field": "expense_ratio"}, 0.01, 0.6)


class FakeRetriever:
    def __init__(self, chunks, relevant=True, similarity=0.8):
        self.chunks, self.relevant, self.similarity = chunks, relevant, similarity
        self.metadatas = [MID, SMALL]
        self.calls = []

    def retrieve(self, query, scheme=None, k=5):
        self.calls.append((query, scheme))
        return RetrievalResult(query=query, scheme=scheme, ranking_query=query,
                               chunks=self.chunks, best_similarity=self.similarity,
                               relevant=self.relevant)


class FakeLLM:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def generate(self, question, chunks, feedback=None):
        self.calls.append(feedback)
        return self.results.pop(0)


def ok(kind="answer", sentences=("The expense ratio is 0.74% (as on 04 Nov 2026).",),
       chunk=ER.chunk_id):
    return LLMResult(status="ok", attempts=1, prompt_tokens=700, completion_tokens=60,
                     answer=LLMAnswer(kind=kind, sentences=list(sentences),
                                      source_chunk_id=chunk))


def error(kind="rate_limit"):
    return LLMResult(status="error", error_kind=kind, attempts=1)


def pipeline(retriever, llm):
    return Pipeline(retriever, llm, AMFI, scheme_names={"hdfc-mid-cap": "HDFC Mid Cap Fund",
                                                        "hdfc-small-cap": "HDFC Small Cap Fund"})


def test_good_answer_cites_the_chunks_page():
    r = pipeline(FakeRetriever([ER, EXIT]), FakeLLM(ok())).answer(
        "What is the expense ratio of HDFC Mid Cap?", scheme="hdfc-mid-cap")
    assert r.kind == "answer" and r.source_url == URL_MID
    assert r.source_chunk_id == ER.chunk_id
    assert r.render().endswith("Last updated from sources: 06 Nov 2026")
    assert r.debug["llm_calls"] == 1 and r.debug["prompt_tokens"] == 700


def test_below_gate_skips_the_llm():
    llm = FakeLLM()
    r = pipeline(FakeRetriever([ER], relevant=False, similarity=0.4), llm).answer(
        "capital of France?")
    assert r.kind == "not_found" and llm.calls == [] and r.source_url == AMFI
    assert r.debug["reason"] == "below relevance threshold"


def test_below_gate_with_scheme_links_that_scheme():
    r = pipeline(FakeRetriever([ER], relevant=False), FakeLLM()).answer(
        "who is the auditor?", scheme="hdfc-small-cap")
    assert r.kind == "not_found" and r.source_url == URL_SMALL
    assert "HDFC Small Cap Fund" in r.text


def test_bad_citation_is_retried_with_feedback_then_accepted():
    llm = FakeLLM(ok(chunk="groww-hdfc-mid-cap#invented"), ok())
    r = pipeline(FakeRetriever([ER, EXIT]), llm).answer("expense ratio?", "hdfc-mid-cap")
    assert r.kind == "answer"
    assert llm.calls[0] is None and "not one of the passages" in llm.calls[1]
    assert r.debug["llm_calls"] == 2 and r.debug["first_attempt_issues"]


def test_two_bad_answers_become_not_found_never_a_wrong_answer():
    made_up = ok(sentences=("The expense ratio is 0.99%.",))
    r = pipeline(FakeRetriever([ER]), FakeLLM(made_up, made_up)).answer(
        "expense ratio?", "hdfc-mid-cap")
    assert r.kind == "not_found" and "0.99" not in r.render()
    assert "retry limit reached" in r.debug["validation"]["issues"]


def test_model_refusal_gives_refusal_template_with_education_link():
    r = pipeline(FakeRetriever([ER]), FakeLLM(ok(kind="refusal", sentences=(), chunk=None))
                 ).answer("Should I invest?", "hdfc-mid-cap")
    assert r.kind == "refusal" and r.source_url == AMFI


def test_advice_in_answer_becomes_refusal():
    advice = ok(sentences=("At 0.74%, it is a good choice for investors.",))
    r = pipeline(FakeRetriever([ER]), FakeLLM(advice)).answer("expense ratio?", "hdfc-mid-cap")
    assert r.kind == "refusal" and "good choice" not in r.render()


def test_not_found_without_scheme_links_closest_match():
    r = pipeline(FakeRetriever([SMALL_ER, EXIT]),
                 FakeLLM(ok(kind="not_found", sentences=(), chunk=None))).answer("auditor?")
    assert r.kind == "not_found" and r.source_url == URL_SMALL
    # ...but doesn't claim the user asked about that scheme.
    assert "Small Cap" not in r.text and "scheme information I have" in r.text


def test_rate_limit_gives_friendly_unavailable_response():
    # Phase 3 exit criterion: a simulated 429 returns the friendly fallback, not an error.
    r = pipeline(FakeRetriever([ER]), FakeLLM(error("rate_limit"))).answer(
        "expense ratio?", "hdfc-mid-cap")
    assert r.kind == "unavailable" and "try again" in r.text and r.source_url == URL_MID
    assert r.debug["reason"] == "llm error: rate_limit"


def test_error_on_retry_also_gives_unavailable():
    llm = FakeLLM(ok(chunk="bad-id"), error("timeout"))
    r = pipeline(FakeRetriever([ER]), llm).answer("expense ratio?", "hdfc-mid-cap")
    assert r.kind == "unavailable"


def test_every_response_kind_has_one_link_and_footer():
    cases = [
        (FakeRetriever([ER]), FakeLLM(ok())),
        (FakeRetriever([ER], relevant=False), FakeLLM()),
        (FakeRetriever([ER]), FakeLLM(ok(kind="refusal", sentences=(), chunk=None))),
        (FakeRetriever([ER]), FakeLLM(error())),
    ]
    for retriever, llm in cases:
        text = pipeline(retriever, llm).answer("q", "hdfc-mid-cap").render()
        assert text.count("https://") == 1 and "\nLast updated from sources: " in text


def test_debug_info_is_json_serialisable():
    r = pipeline(FakeRetriever([ER]), FakeLLM(ok())).answer("q", "hdfc-mid-cap")
    json.dumps(r.debug)
