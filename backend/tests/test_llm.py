import json
from dataclasses import dataclass, field
from types import SimpleNamespace

import groq
import httpx
import pytest

from app.config import Settings
from app.llm import SYSTEM_PROMPT, GroqLLM, build_user_message, format_context


@dataclass
class FakeChunk:
    chunk_id: str
    text: str
    metadata: dict = field(default_factory=dict)


CHUNKS = [
    FakeChunk("groww-hdfc-mid-cap#expense_ratio",
              "Expense ratio: The expense ratio of HDFC Mid Cap Fund Direct Growth is 0.74% "
              "(as on 04 Nov 2026).",
              {"scheme": "hdfc-mid-cap", "scheme_name": "HDFC Mid Cap Fund Direct Growth",
               "as_on": "2026-11-04", "doc_date": "2026-11-06"}),
    FakeChunk("groww-hdfc-mid-cap#exit_load",
              "Exit load: Exit load for HDFC Mid Cap Fund Direct Growth: Exit load of 1% if "
              "redeemed within 1 year.",
              {"scheme": "hdfc-mid-cap", "scheme_name": "HDFC Mid Cap Fund Direct Growth",
               "as_on": "", "doc_date": "2026-11-06"}),
]

GOOD = {"kind": "answer", "sentences": ["The expense ratio is 0.74%."],
        "source_chunk_id": "groww-hdfc-mid-cap#expense_ratio"}


def completion(content, finish_reason="stop", prompt_tokens=100, completion_tokens=20):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                 finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


class FakeClient:
    """Returns (or raises) queued responses and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_llm(*responses, **settings):
    base = dict(_env_file=None, llm_api_key="gsk_test", llm_model="openai/gpt-oss-120b")
    client = FakeClient(*responses)
    return GroqLLM(Settings(**{**base, **settings}), client=client), client


_REQ = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def status_error(cls, code, message="error"):
    return cls(message, response=httpx.Response(code, request=_REQ), body=None)


# ---------------------------------------------------------------- prompt


def test_context_has_chunk_ids_scheme_and_dates():
    text = format_context(CHUNKS)
    assert "[groww-hdfc-mid-cap#expense_ratio] (HDFC Mid Cap Fund Direct Growth, " \
           "as on 04 Nov 2026)" in text
    # Falls back to the page date when a fact has no date of its own.
    assert "[groww-hdfc-mid-cap#exit_load] (HDFC Mid Cap Fund Direct Growth, " \
           "as on 06 Nov 2026)" in text


def test_user_message_contains_question_and_optional_feedback():
    msg = build_user_message("  What is the expense ratio?  ", CHUNKS)
    assert msg.endswith("Question: What is the expense ratio?")
    with_feedback = build_user_message("q", CHUNKS, feedback="cited the wrong passage.")
    assert "rejected: cited the wrong passage." in with_feedback


def test_system_prompt_is_static():
    # No timestamps or per-request data, so it is identical on every call.
    assert "{" not in SYSTEM_PROMPT.replace('{"kind": ..., "sentences": [...], '
                                            '"source_chunk_id": ...}', "")


# ---------------------------------------------------------------- requests


def test_request_uses_strict_schema_zero_temperature_and_reasoning_effort():
    llm, client = make_llm(completion(json.dumps(GOOD)))
    llm.generate("expense ratio?", CHUNKS)
    req = client.requests[0]
    assert req["model"] == "openai/gpt-oss-120b"
    assert req["temperature"] == 0
    assert req["reasoning_effort"] == "low"
    assert req["response_format"]["type"] == "json_schema"
    assert req["response_format"]["json_schema"]["strict"] is True
    assert req["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}


def test_reasoning_effort_can_be_disabled():
    llm, client = make_llm(completion(json.dumps(GOOD)), llm_reasoning_effort="")
    llm.generate("q", CHUNKS)
    assert "reasoning_effort" not in client.requests[0]


# ---------------------------------------------------------------- parsing & retries


def test_valid_reply_is_parsed():
    llm, _ = make_llm(completion(json.dumps(GOOD)))
    result = llm.generate("expense ratio?", CHUNKS)
    assert result.status == "ok" and result.attempts == 1
    assert result.answer.source_chunk_id == "groww-hdfc-mid-cap#expense_ratio"
    assert (result.prompt_tokens, result.completion_tokens) == (100, 20)


def test_invalid_json_is_retried_once():
    llm, client = make_llm(completion("not json"), completion(json.dumps(GOOD)))
    result = llm.generate("q", CHUNKS)
    assert result.status == "ok" and result.attempts == 2 and len(client.requests) == 2
    assert result.prompt_tokens == 200  # usage summed across attempts


def test_schema_violation_counts_as_invalid():
    bad = {"kind": "maybe", "sentences": [], "source_chunk_id": None}
    extra = {**GOOD, "url": "https://example.com"}
    llm, _ = make_llm(completion(json.dumps(bad)), completion(json.dumps(extra)))
    result = llm.generate("q", CHUNKS)
    assert result.status == "invalid" and result.answer is None and result.attempts == 2


def test_truncated_output_is_never_trusted():
    # LLM-02: finish_reason "length" -> retry, even if the text happens to parse.
    llm, _ = make_llm(completion(json.dumps(GOOD), finish_reason="length"),
                      completion(json.dumps(GOOD)))
    result = llm.generate("q", CHUNKS)
    assert result.status == "ok" and result.attempts == 2


def test_json_schema_rejection_falls_back_to_json_object():
    rejection = status_error(groq.BadRequestError, 400,
                             "response_format json_schema is not supported by this model")
    llm, client = make_llm(rejection, completion(json.dumps(GOOD)))
    result = llm.generate("q", CHUNKS)
    assert result.status == "ok" and result.attempts == 1
    assert client.requests[1]["response_format"] == {"type": "json_object"}
    assert llm.response_format_mode == "json_object"  # sticky for later calls


# ---------------------------------------------------------------- errors (LLM-10, LLM-11)


@pytest.mark.parametrize("exc, kind", [
    (status_error(groq.RateLimitError, 429, "rate limited"), "rate_limit"),
    (groq.APITimeoutError(request=_REQ), "timeout"),
    (groq.APIConnectionError(request=_REQ), "connection"),
    (status_error(groq.AuthenticationError, 401, "invalid api key"), "auth"),
    (status_error(groq.BadRequestError, 400, "messages too long"), "bad_request"),
    (status_error(groq.InternalServerError, 500, "boom"), "api"),
])
def test_api_errors_are_returned_not_raised(exc, kind):
    llm, _ = make_llm(exc)
    result = llm.generate("q", CHUNKS)
    assert result.status == "error" and result.error_kind == kind and result.answer is None


def test_missing_api_key_fails_fast():
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        GroqLLM(Settings(_env_file=None, llm_api_key=None))
