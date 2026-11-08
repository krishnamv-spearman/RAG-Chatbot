"""Groq LLM client: turns retrieved chunks + a question into a structured answer.

All provider-specific code lives here, behind `GroqLLM.generate(question, chunks)`, so the
provider or model can be swapped without touching the rest of the pipeline.

The model never writes links or footers: it returns JSON with up to 3 sentences and the ID
of the ONE chunk that supports them. Code validates that (app/validate.py) and adds the
link and date (composer, Phase 3 task 3).
"""

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import groq
from pydantic import BaseModel, ConfigDict, ValidationError

from app.config import Settings, get_settings
from app.ingest.normalize import display_date

logger = logging.getLogger("mf_faq.llm")

MAX_ATTEMPTS = 2  # one retry for unparseable / truncated output


class LLMAnswer(BaseModel):
    """The only shape the model may return."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["answer", "not_found", "refusal"]
    sentences: list[str]
    source_chunk_id: str | None


# Sent to Groq as a strict JSON schema. The 3-sentence limit is enforced in code
# (validator), not here, so the schema stays within what strict mode supports.
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["answer", "not_found", "refusal"]},
        "sentences": {"type": "array", "items": {"type": "string"}},
        "source_chunk_id": {"type": ["string", "null"]},
    },
    "required": ["kind", "sentences", "source_chunk_id"],
    "additionalProperties": False,
}

# Fixed text: identical on every request (no dates or IDs), so provider-side prompt
# caching can work and behaviour is reproducible.
SYSTEM_PROMPT = """\
You are a facts-only FAQ assistant for selected HDFC Mutual Fund schemes.

Rules:
1. Use ONLY the context passages provided in the user message. Never use outside knowledge.
2. Never give investment advice, opinions, recommendations, predictions, comparisons
   between funds, suitability judgements or return calculations. If the question asks
   for any of these, return kind "refusal" with an empty sentences list.
3. If the context does not contain the answer, return kind "not_found" with an empty
   sentences list and source_chunk_id null. Do not guess.
4. Otherwise return kind "answer" with at most 3 short, plain sentences.
   - Name the scheme.
   - Copy numbers, amounts, percentages, conditions and dates exactly as written in the
     context, including the "as on" date when the context gives one.
   - Keep conditions complete (e.g. "1% if redeemed within 1 year", not just "1%").
5. Set source_chunk_id to the ID (the text inside [ ]) of the single passage that supports
   the whole answer. Answer only what that one passage supports.
6. Do not include URLs, links, markdown, footers or "source:" lines. The system adds them.

Respond with JSON only: {"kind": ..., "sentences": [...], "source_chunk_id": ...}"""

ErrorKind = Literal["rate_limit", "timeout", "connection", "auth", "bad_request", "api"]


class ContextChunk(Protocol):
    """What the LLM needs from a retrieved chunk (matches app.retrieval.RetrievedChunk)."""

    chunk_id: str
    text: str
    metadata: dict


@dataclass
class LLMResult:
    status: Literal["ok", "invalid", "error"]
    answer: LLMAnswer | None = None
    error_kind: ErrorKind | None = None
    error: str | None = None
    finish_reason: str | None = None
    attempts: int = 0
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str | None = None
    raw: list[str] = field(default_factory=list)  # raw model outputs, for debugging/evals


def format_context(chunks: Sequence[ContextChunk]) -> str:
    """`[chunk_id] (scheme, as on DD Mon YYYY)` header, then the chunk text."""
    blocks = []
    for c in chunks:
        meta = c.metadata
        date = display_date(meta.get("as_on") or meta.get("doc_date") or None)
        label = meta.get("scheme_name") or meta.get("scheme") or "source"
        header = f"[{c.chunk_id}] ({label}" + (f", as on {date})" if date else ")")
        blocks.append(f"{header}\n{c.text}")
    return "\n\n".join(blocks)


def build_user_message(question: str, chunks: Sequence[ContextChunk],
                       feedback: str | None = None) -> str:
    parts = ["Context passages:", format_context(chunks) or "(none)", "",
             f"Question: {question.strip()}"]
    if feedback:
        parts += ["", f"Your previous reply was rejected: {feedback} Reply again, following "
                      "all rules."]
    return "\n".join(parts)


def _classify_error(exc: Exception) -> ErrorKind:
    # Most specific first: RateLimitError/AuthenticationError/BadRequestError are
    # APIStatusError subclasses; APITimeoutError is an APIConnectionError subclass.
    if isinstance(exc, groq.RateLimitError):
        return "rate_limit"
    if isinstance(exc, groq.APITimeoutError):
        return "timeout"
    if isinstance(exc, groq.APIConnectionError):
        return "connection"
    if isinstance(exc, groq.AuthenticationError | groq.PermissionDeniedError):
        return "auth"
    if isinstance(exc, groq.BadRequestError):
        return "bad_request"
    return "api"


def _is_response_format_rejection(exc: groq.BadRequestError) -> bool:
    text = str(exc).lower()
    return "json_schema" in text or "response_format" in text or "response format" in text


class GroqLLM:
    def __init__(self, settings: Settings | None = None, client: Any | None = None):
        self.settings = settings or get_settings()
        if client is None:
            if not self.settings.llm_configured:
                raise RuntimeError("LLM_API_KEY is not set (see .env.example)")
            client = groq.Groq(
                api_key=self.settings.llm_api_key.get_secret_value(),
                timeout=self.settings.llm_timeout_seconds,
                max_retries=self.settings.llm_max_retries,
            )
        self.client = client
        self.model = self.settings.llm_model
        self.response_format_mode = self.settings.llm_response_format

    def _response_format(self) -> dict:
        if self.response_format_mode == "json_object":
            return {"type": "json_object"}
        return {"type": "json_schema", "json_schema": {
            "name": "faq_answer", "strict": True, "schema": ANSWER_SCHEMA}}

    def _request_kwargs(self, messages: list[dict]) -> dict:
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_completion_tokens": self.settings.llm_max_completion_tokens,
            "response_format": self._response_format(),
        }
        if self.settings.llm_reasoning_effort:
            kwargs["reasoning_effort"] = self.settings.llm_reasoning_effort
        return kwargs

    @staticmethod
    def parse(content: str | None) -> LLMAnswer | None:
        if not content:
            return None
        try:
            return LLMAnswer.model_validate(json.loads(content))
        except (ValueError, ValidationError):
            return None

    def generate(self, question: str, chunks: Sequence[ContextChunk],
                 feedback: str | None = None) -> LLMResult:
        """One logical generation: up to MAX_ATTEMPTS calls if the output is unparseable
        or truncated. API errors are returned as status="error" (never raised), so the
        caller can show a friendly fallback."""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_message(question, chunks, feedback)},
        ]
        result = LLMResult(status="invalid", model=self.model)
        started = time.perf_counter()
        switched_format = False

        while result.attempts < MAX_ATTEMPTS:
            result.attempts += 1
            try:
                response = self.client.chat.completions.create(**self._request_kwargs(messages))
            except groq.BadRequestError as exc:
                if (not switched_format and self.response_format_mode == "json_schema"
                        and _is_response_format_rejection(exc)):
                    # Model doesn't support strict schemas: fall back to JSON mode once,
                    # and keep using it for this client.
                    logger.warning("json_schema rejected by %s; using json_object", self.model)
                    self.response_format_mode, switched_format = "json_object", True
                    result.attempts -= 1
                    continue
                return self._error(result, exc, started)
            except groq.GroqError as exc:
                return self._error(result, exc, started)

            choice = response.choices[0]
            content = choice.message.content
            result.finish_reason = choice.finish_reason
            result.raw.append(content or "")
            usage = getattr(response, "usage", None)
            if usage:
                result.prompt_tokens += usage.prompt_tokens or 0
                result.completion_tokens += usage.completion_tokens or 0

            # Truncated output is never trusted, even if it happens to parse.
            answer = None if choice.finish_reason == "length" else self.parse(content)
            if answer is not None:
                result.status, result.answer = "ok", answer
                break

        result.latency_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "llm status=%s attempts=%d finish=%s prompt_tokens=%d completion_tokens=%d "
            "latency_ms=%d", result.status, result.attempts, result.finish_reason,
            result.prompt_tokens, result.completion_tokens, result.latency_ms)
        return result

    def _error(self, result: LLMResult, exc: Exception, started: float) -> LLMResult:
        result.status = "error"
        result.error_kind = _classify_error(exc)
        result.error = f"{type(exc).__name__}: {exc}"[:300]
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        logger.warning("llm error kind=%s %s", result.error_kind, result.error)
        return result
