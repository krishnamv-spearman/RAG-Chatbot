"""Phase 4 pipeline behaviour: PII -> router -> template replies / factual path.
Fake retriever, LLM and rewriter: no model, no network."""

import re

import pytest

from app import templates
from app.pipeline import Pipeline
from app.registry import load_schemes
from app.router import ConversationContext, Router
from tests.test_pipeline import ER, EXIT, MID, SMALL, SMALL_ER, FakeLLM, FakeRetriever, ok

AMFI = "https://www.amfiindia.com/investor"
SEBI = "https://investor.sebi.gov.in/"
URL_MID = MID["url"]
URL_SMALL = SMALL["url"]


class FakeRewriter:
    def __init__(self, result="What is the exit load of HDFC Small Cap Fund?"):
        self.result, self.calls = result, []

    def rewrite(self, question, context, scheme_name):
        self.calls.append((question, scheme_name))
        return self.result, "llm"


def make(chunks=(ER, EXIT), llm=None, rewriter=None, relevant=True):
    retriever = FakeRetriever(list(chunks), relevant=relevant)
    llm = llm or FakeLLM()
    rewriter = rewriter or FakeRewriter()
    p = Pipeline(retriever, llm, AMFI,
                 scheme_names={"hdfc-mid-cap": "HDFC Mid Cap Fund",
                               "hdfc-small-cap": "HDFC Small Cap Fund"},
                 router=Router(load_schemes()), rewriter=rewriter,
                 refusal_urls={"advice": AMFI, "comparison": AMFI, "prediction": SEBI})
    return p, retriever, llm, rewriter


def one_link_and_footer(text: str) -> bool:
    return len(re.findall(r"https?://", text)) == 1 and "\nLast updated from sources: " in text


# ---------------------------------------------------------------- refusals (exit criterion 1)


@pytest.mark.parametrize("question, url", [
    ("Should I invest in HDFC Mid Cap?", AMFI),
    ("Which is better, large cap or multi cap?", AMFI),
    ("Will silver ETF go up?", SEBI),
])
def test_advisory_questions_are_refused_without_retrieval_or_llm(question, url):
    p, retriever, llm, _ = make()
    r = p.answer(question)
    assert r.kind == "refusal" and r.source_url == url
    assert retriever.calls == [] and llm.calls == []
    assert one_link_and_footer(r.render())


def test_refusal_keeps_the_scheme_for_follow_ups():
    p, *_ = make()
    assert p.answer("Should I invest in HDFC Mid Cap?").scheme == "hdfc-mid-cap"  # FU-03


# ---------------------------------------------------------------- performance (criterion 2)


def test_returns_question_links_groww_page_and_has_no_numbers():
    p, retriever, llm, _ = make()
    r = p.answer("What were the 3-year returns of HDFC Small Cap?")
    assert r.kind == "performance" and r.source_url == URL_SMALL
    assert not re.search(r"\d", r.text)
    assert retriever.calls == [] and llm.calls == []


def test_returns_without_scheme_asks_which_scheme():
    p, *_ = make()
    r = p.answer("What are the returns?")
    assert r.kind == "clarify" and r.text == templates.PERFORMANCE_WHICH_SCHEME


# ---------------------------------------------------------------- PII (criterion 3)


@pytest.mark.parametrize("question", [
    "My PAN is ABCDE1234F, what is the exit load of mid cap?",
    "aadhaar 2345 6789 0124 linked to small cap?",
    "call me on 9876543210 about large cap",
    "mail statement to rahul@gmail.com",
])
def test_pii_is_blocked_before_anything_else(question):
    rewriter = FakeRewriter()
    p, retriever, llm, _ = make(rewriter=rewriter)
    r = p.answer(question, context=ConversationContext(last_scheme="hdfc-mid-cap"))
    assert r.kind == "pii_blocked" and r.render() == templates.PII_WARNING
    assert retriever.calls == [] and llm.calls == [] and rewriter.calls == []
    assert r.scheme == "hdfc-mid-cap"  # thread state unchanged
    assert "ABCDE1234F" not in str(r.debug) and "9876543210" not in str(r.debug)


# ---------------------------------------------------------------- follow-ups (criterion 4)


def test_follow_up_uses_the_threads_scheme():
    p, retriever, llm, rewriter = make(llm=FakeLLM(ok(
        sentences=("HDFC Mid Cap Fund has an exit load of 1% if redeemed within 1 year.",),
        chunk=EXIT.chunk_id)))
    ctx = ConversationContext(last_scheme="hdfc-mid-cap", history=[
        ("user", "What's the expense ratio of HDFC Mid Cap?"),
        ("assistant", "The expense ratio is 0.74%.")])
    r = p.answer("And its exit load?", context=ctx)
    assert r.kind == "answer" and r.source_chunk_id == EXIT.chunk_id
    assert retriever.calls == [("And its exit load?", "hdfc-mid-cap")]
    assert rewriter.calls == []  # has a fact, so no rewrite needed
    assert r.debug["scheme_from_context"] is True


def test_elliptical_follow_up_is_rewritten_then_answered():
    exit_small = type(EXIT)("groww-hdfc-small-cap#exit_load",
                            "Exit load: HDFC Small Cap Fund: Exit load of 1% if redeemed "
                            "within 1 year.", SMALL, 0.03, 0.8)
    p, retriever, _, rewriter = make(chunks=[exit_small], llm=FakeLLM(ok(
        sentences=("HDFC Small Cap Fund has an exit load of 1% if redeemed within 1 year.",),
        chunk=exit_small.chunk_id)))
    ctx = ConversationContext(last_scheme="hdfc-mid-cap", history=[
        ("user", "exit load of mid cap"), ("assistant", "1% within 1 year.")])
    r = p.answer("and small cap?", context=ctx)
    assert rewriter.calls == [("and small cap?", "HDFC Small Cap Fund")]
    assert retriever.calls[0] == ("What is the exit load of HDFC Small Cap Fund?",
                                  "hdfc-small-cap")
    assert r.kind == "answer" and r.scheme == "hdfc-small-cap"


def test_rewritten_question_is_routed_again():
    # Clarify chip flow: "What are the returns?" -> "HDFC Small Cap Fund".
    rewriter = FakeRewriter("What are the returns of HDFC Small Cap Fund?")
    p, retriever, llm, _ = make(rewriter=rewriter)
    ctx = ConversationContext(history=[("user", "What are the returns?"),
                                       ("assistant", templates.PERFORMANCE_WHICH_SCHEME)])
    r = p.answer("HDFC Small Cap Fund", context=ctx)
    assert r.kind == "performance" and r.source_url == URL_SMALL
    assert retriever.calls == [] and llm.calls == []
    assert r.scheme == "hdfc-small-cap"


def test_follow_up_in_new_thread_asks_which_scheme():
    p, retriever, *_ = make()
    r = p.answer("and its exit load?")
    assert r.kind == "clarify" and retriever.calls == []


# ---------------------------------------------------------------- other template replies


def test_two_schemes_asks_one_at_a_time():
    p, retriever, *_ = make()
    r = p.answer("Exit load of mid cap and small cap?")
    assert r.kind == "clarify" and "HDFC Mid Cap Fund or HDFC Small Cap Fund" in r.text
    assert retriever.calls == []


def test_other_amc_is_out_of_scope():
    p, retriever, *_ = make()
    r = p.answer("SBI Small Cap expense ratio")
    assert r.kind == "out_of_scope" and retriever.calls == [] and r.scheme is None


def test_greeting():
    p, *_ = make()
    r = p.answer("hi")
    assert r.kind == "greeting" and one_link_and_footer(r.render())


def test_same_fact_across_schemes_without_scheme_asks_which():
    # GAP-18: "What is the expense ratio?" with no scheme anywhere.
    p, _, llm, _ = make(chunks=[ER, SMALL_ER])
    r = p.answer("What is the expense ratio?")
    assert r.kind == "clarify" and llm.calls == []


def test_question_naming_no_fact_is_not_turned_into_clarify():
    # "How do I download my capital gains statement?" names no fact, so even if the same
    # field from several schemes is retrieved, it goes to the LLM (-> not_found).
    p, _, llm, _ = make(chunks=[ER, SMALL_ER],
                        llm=FakeLLM(ok(kind="not_found", sentences=(), chunk=None)))
    r = p.answer("How do I download my capital gains statement?")
    assert r.kind == "not_found" and len(llm.calls) == 1


def test_answer_without_scheme_sets_thread_scheme_from_citation():
    p, *_ = make(chunks=[ER, EXIT], llm=FakeLLM(ok()))
    r = p.answer("expense ratio of HDFC Mid Cap?")
    assert r.scheme == "hdfc-mid-cap"


def test_every_non_pii_reply_has_one_link_and_footer():
    p, *_ = make(llm=FakeLLM(ok()))
    for q in ("hi", "Should I invest?", "3 year returns of mid cap", "SBI small cap NAV",
              "mid cap and small cap benchmark", "🤔", "expense ratio of mid cap"):
        assert one_link_and_footer(p.answer(q).render()), q
