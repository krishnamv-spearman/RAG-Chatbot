import pytest

from app.llm import LLMAnswer
from app.validate import (
    Action,
    find_advice,
    normalize_number,
    resolve_after_retry,
    retry_feedback,
    split_sentences,
    ungrounded_numbers,
    validate,
)
from tests.test_llm import CHUNKS

ER = "groww-hdfc-mid-cap#expense_ratio"
EXIT = "groww-hdfc-mid-cap#exit_load"
SMALL = type(CHUNKS[0])("groww-hdfc-small-cap#expense_ratio",
                        "Expense ratio: The expense ratio of HDFC Small Cap Fund Direct Growth "
                        "is 0.79% (as on 04 Nov 2026).",
                        {"scheme": "hdfc-small-cap"})
ALL = [*CHUNKS, SMALL]


def answer(*sentences, chunk=ER, kind="answer"):
    return LLMAnswer(kind=kind, sentences=list(sentences), source_chunk_id=chunk)


def check(ans, question="What is the expense ratio of HDFC Mid Cap?", scheme="hdfc-mid-cap"):
    return validate(ans, ALL, question, expected_scheme=scheme)


# ---------------------------------------------------------------- happy path


def test_good_answer_is_accepted_unchanged():
    r = check(answer("The expense ratio of HDFC Mid Cap Fund Direct Growth is 0.74% "
                     "(as on 04 Nov 2026)."))
    assert r.action is Action.ACCEPT and r.source_chunk_id == ER and r.fixes == []
    assert r.text.startswith("The expense ratio")


def test_exit_load_condition_numbers_are_grounded():
    r = check(answer("HDFC Mid Cap Fund charges an exit load of 1% if redeemed within 1 year.",
                     chunk=EXIT), question="exit load of mid cap?")
    assert r.action is Action.ACCEPT


# ---------------------------------------------------------------- model's own verdicts


def test_model_refusal_and_not_found_pass_through():
    assert check(answer(kind="refusal", chunk=None)).action is Action.REFUSAL
    assert check(answer(kind="not_found", chunk=None)).action is Action.NOT_FOUND


def test_unparseable_reply_means_retry():
    assert validate(None, ALL, "q").action is Action.RETRY


# ---------------------------------------------------------------- citation (LLM-04..07)


@pytest.mark.parametrize("ans, issue", [
    (answer("The expense ratio is 0.74%.", chunk=None), "no source_chunk_id"),
    (answer("The expense ratio is 0.74%.", chunk="groww-hdfc-mid-cap#made_up"),
     "not one of the passages"),
    (answer(), "no sentences"),
    (answer("   "), "no sentences"),
])
def test_bad_citation_or_empty_answer_is_retried(ans, issue):
    r = check(ans)
    assert r.action is Action.RETRY and issue in r.issues[0]


def test_citing_another_schemes_chunk_is_retried():
    r = check(answer("The expense ratio is 0.79%.", chunk=SMALL.chunk_id))
    assert r.action is Action.RETRY and "hdfc-small-cap" in r.issues[0]


def test_scheme_check_skipped_when_scheme_unknown():
    r = check(answer("The expense ratio is 0.79%.", chunk=SMALL.chunk_id), scheme=None)
    assert r.action is Action.ACCEPT


# ---------------------------------------------------------------- grounding (LLM-08, FMT-06/07)


def test_made_up_number_is_retried():
    r = check(answer("The expense ratio of HDFC Mid Cap Fund is 0.80%."))
    assert r.action is Action.RETRY and "0.8" in r.issues[0]


@pytest.mark.parametrize("text", [
    "The expense ratio is 0.740%.",           # trailing zero
    "As on 4 Nov 2026, the expense ratio is 0.74%.",  # leading zero dropped from the day
])
def test_differently_formatted_numbers_still_count_as_grounded(text):
    assert check(answer(text)).action is Action.ACCEPT


def test_number_repeated_from_the_question_is_allowed():
    r = check(answer("No, the expense ratio is 0.74%, not 1.5%."),
              question="Is the expense ratio of mid cap 1.5%?")
    assert r.action is Action.ACCEPT


def test_number_normalisation():
    assert normalize_number("1,00,000") == "100000"
    assert normalize_number("1.00") == "1"
    assert normalize_number("04") == "4"
    assert normalize_number("0.740") == "0.74"
    assert ungrounded_numbers("₹1,00,000 and 5%", "Rs 100000", "") == {"5"}


# ---------------------------------------------------------------- advice (ADV-12, ADV-08)


@pytest.mark.parametrize("text", [
    "The low expense ratio makes it a good choice for long-term investors.",
    "You should invest via SIP.",
    "I recommend this fund.",
    "It has outperformed its benchmark.",
    "It is better than the small cap fund.",
    "Its NAV will rise next year.",
])
def test_advice_language_turns_answer_into_refusal(text):
    r = check(answer(text))
    assert r.action is Action.REFUSAL and "advice" in r.issues[0]


@pytest.mark.parametrize("text", [
    "The benchmark is the NIFTY Midcap 150 Total Return Index.",
    "The exit load is 1% if redeemed within 1 year.",
    "The riskometer classification is Very High.",
    "It is better known by its former name.",
])
def test_factual_text_is_not_flagged_as_advice(text):
    assert find_advice(text) is None


# ---------------------------------------------------------------- formatting (FMT-01..05)


def test_decimals_and_abbreviations_do_not_split_sentences():
    assert split_sentences("The expense ratio is 0.74%. Minimum SIP is Rs. 100.") == [
        "The expense ratio is 0.74%.", "Minimum SIP is Rs. 100."]
    assert len(split_sentences("Managed by Mr. Setalvad, e.g. since 2013.")) == 1


def test_more_than_three_sentences_is_truncated():
    r = check(answer("The expense ratio is 0.74%. It is as on 04 Nov 2026.",
                     "This is the Direct plan.", "It is a mid cap fund. It is by HDFC."))
    assert r.action is Action.ACCEPT and len(r.sentences) == 3
    assert "truncated 5 sentences to 3" in r.fixes


def test_urls_markdown_and_source_lines_are_removed():
    r = check(answer("**The expense ratio** is 0.74% (see https://groww.in/x).",
                     "Source: groww.in"))
    assert r.action is Action.ACCEPT
    # The URL goes, the punctuation after it stays.
    assert r.sentences == ["The expense ratio is 0.74% (see)."]
    assert {"removed URL", "removed markdown", "removed source/footer line"} <= set(r.fixes)


@pytest.mark.parametrize("text, expected", [
    ("Details at www.groww.in.", "Details at."),
    ("Visit groww.in/mutual-funds/x, then check.", "Visit, then check."),
    # Decimals and "Rs." must survive URL removal.
    ("Minimum SIP is Rs. 100 and the ratio is 0.74%.",
     "Minimum SIP is Rs. 100 and the ratio is 0.74%."),
])
def test_url_removal_keeps_surrounding_text(text, expected):
    from app.validate import clean_sentence

    assert clean_sentence(text)[0] == expected


def test_answer_with_only_a_source_line_is_retried():
    r = check(answer("Source: https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth"))
    assert r.action is Action.RETRY


# ---------------------------------------------------------------- retry handling


def test_second_retry_becomes_not_found():
    r = resolve_after_retry(check(answer("The expense ratio is 0.80%.")))
    assert r.action is Action.NOT_FOUND and "retry limit reached" in r.issues


def test_accept_is_unchanged_by_resolve():
    r = check(answer("The expense ratio is 0.74%."))
    assert resolve_after_retry(r) is r


def test_retry_feedback_is_readable():
    r = check(answer("The expense ratio is 0.80%."))
    assert retry_feedback(r) == "numbers not in the cited passage: 0.8."
