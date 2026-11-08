"""Validates every LLM answer before it reaches the user (architecture §6.5).

The prompt asks the model to follow the rules; this module makes sure it did. Outcomes:

- ACCEPT     answer is safe to show (possibly after small fixes: markdown/URLs removed,
             truncated to 3 sentences)
- RETRY      fixable by asking again (bad citation, ungrounded number, empty answer)
- NOT_FOUND  the model said it can't answer from the context (or retries ran out)
- REFUSAL    the model refused, or the answer contains advice language

The caller (pipeline, Phase 3 task 4) retries at most once and then calls
`resolve_after_retry()`, which turns a second RETRY into NOT_FOUND.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache

from app.llm import ContextChunk, LLMAnswer

MAX_SENTENCES = 3


class Action(StrEnum):
    ACCEPT = "accept"
    RETRY = "retry"
    NOT_FOUND = "not_found"
    REFUSAL = "refusal"


@dataclass
class ValidationResult:
    action: Action
    sentences: list[str] = field(default_factory=list)
    source_chunk_id: str | None = None
    issues: list[str] = field(default_factory=list)  # why it was rejected
    fixes: list[str] = field(default_factory=list)  # what was changed but accepted

    @property
    def text(self) -> str:
        return " ".join(self.sentences)


# ---------------------------------------------------------------- sentence splitting


@lru_cache
def _segmenter():
    import pysbd

    return pysbd.Segmenter(language="en", clean=False)


def split_sentences(text: str) -> list[str]:
    """Sentence split that doesn't break on decimals ("0.74%"), "Rs.", "e.g." or "Mr."."""
    text = text.strip()
    if not text:
        return []
    return [s.strip() for s in _segmenter().segment(text) if s.strip()]


# ---------------------------------------------------------------- cleaning

# Lazy match + lookahead so trailing punctuation after a URL (")." etc.) is kept.
_URL_END = r"(?=[)\].,;:!?'\"]*(?:\s|$))"
_URL = re.compile(
    rf"(?:https?://|www\.)\S+?{_URL_END}"
    rf"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|in|org|net|co|io|gov)(?:/\S*?)?{_URL_END}",
    re.IGNORECASE,
)
_MARKDOWN = [
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
    (re.compile(r"__(.+?)__"), r"\1"),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),  # [text](link) -> text
    (re.compile(r"^\s*(?:[-*•]|#+|\d+[.)])\s+"), ""),  # list markers / headings
]
_SOURCE_LINE = re.compile(r"^\s*(source|sources|last updated)\b.*$", re.IGNORECASE)


def clean_sentence(sentence: str) -> tuple[str, list[str]]:
    fixes = []
    cleaned = sentence
    for pattern, repl in _MARKDOWN:
        cleaned = pattern.sub(repl, cleaned)
    if cleaned != sentence:
        fixes.append("removed markdown")
    without_urls = _URL.sub("", cleaned)
    if without_urls != cleaned:
        fixes.append("removed URL")
        cleaned = without_urls
    cleaned = re.sub(r"\(\s*\)", "", cleaned)  # "( )" left behind by a removed URL
    cleaned = re.sub(r"\s+([.,;:)])", r"\1", re.sub(r"\s{2,}", " ", cleaned)).strip()
    return cleaned, fixes


# ---------------------------------------------------------------- advice detection

# Phrases, not single words: "better" or "vs" alone also appear in factual text
# (edge case ADV-08).
ADVISORY_PATTERNS = [
    r"\brecommend(?:ed|s|ation)?\b",
    r"\byou (?:should|must|could|might want to)\b",
    r"\b(?:should|must) (?:invest|buy|sell|redeem|switch|hold|consider)\b",
    r"\bi (?:suggest|advise|would)\b",
    r"\b(?:good|great|bad|poor|smart|wise|safe|excellent|solid|attractive) "
    r"(?:choice|option|investment|fund|pick|bet)\b",
    r"\bworth (?:investing|buying|it)\b",
    r"\b(?:better|worse) (?:than|choice|option|fund)\b",
    r"\b(?:out|under)perform(?:s|ed|ing)?\b",
    r"\b(?:ideal|suitable|perfect|right) (?:for you|for investors|choice|fund)\b",
    r"\bbest (?:fund|option|choice)\b",
    r"\b(?:will|likely to|expected to) (?:rise|fall|grow|increase|decrease|go up|go down)\b",
]
_ADVISORY = re.compile("|".join(ADVISORY_PATTERNS), re.IGNORECASE)


def find_advice(text: str) -> str | None:
    match = _ADVISORY.search(text)
    return match.group(0) if match else None


# ---------------------------------------------------------------- number grounding

_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def normalize_number(token: str) -> str:
    """'1,00,000' -> '100000', '1.00' -> '1', '04' -> '4', '0.740' -> '0.74'."""
    token = token.replace(",", "")
    integer, _, decimals = token.partition(".")
    integer = integer.lstrip("0") or "0"
    decimals = decimals.rstrip("0")
    return f"{integer}.{decimals}" if decimals else integer


def numbers_in(text: str) -> set[str]:
    return {normalize_number(m) for m in _NUMBER.findall(text)}


def ungrounded_numbers(answer_text: str, chunk_text: str, question: str) -> set[str]:
    """Numbers in the answer that appear neither in the cited chunk nor in the question
    (edge case FMT-07: repeating the user's number back is allowed)."""
    return numbers_in(answer_text) - numbers_in(chunk_text) - numbers_in(question)


# ---------------------------------------------------------------- main entry point


def validate(answer: LLMAnswer | None, chunks: Sequence[ContextChunk], question: str,
             expected_scheme: str | None = None) -> ValidationResult:
    if answer is None:
        return ValidationResult(Action.RETRY, issues=["reply was not valid JSON for the schema"])
    if answer.kind == "refusal":
        return ValidationResult(Action.REFUSAL, issues=["model refused"])
    if answer.kind == "not_found":
        return ValidationResult(Action.NOT_FOUND, issues=["model found no answer in context"])

    raw_sentences = [s for item in answer.sentences for s in split_sentences(item)]
    if not raw_sentences:
        return ValidationResult(Action.RETRY, issues=["answer has no sentences"])

    by_id = {c.chunk_id: c for c in chunks}
    cited = by_id.get(answer.source_chunk_id or "")
    if cited is None:
        issue = ("answer has no source_chunk_id" if not answer.source_chunk_id else
                 f"source_chunk_id {answer.source_chunk_id!r} is not one of the passages")
        return ValidationResult(Action.RETRY, issues=[issue])
    if expected_scheme and cited.metadata.get("scheme") != expected_scheme:
        return ValidationResult(Action.RETRY, source_chunk_id=cited.chunk_id, issues=[
            f"cited passage is for {cited.metadata.get('scheme')}, not {expected_scheme}"])

    fixes: list[str] = []
    sentences = []
    for s in raw_sentences:
        if _SOURCE_LINE.match(s):
            fixes.append("removed source/footer line")
            continue
        cleaned, sentence_fixes = clean_sentence(s)
        fixes += sentence_fixes
        if cleaned:
            sentences.append(cleaned)
    if not sentences:
        return ValidationResult(Action.RETRY, issues=["answer was empty after cleaning"])

    full_text = " ".join(sentences)
    advice = find_advice(full_text)
    if advice:
        return ValidationResult(Action.REFUSAL, source_chunk_id=cited.chunk_id,
                                issues=[f"advice language: {advice!r}"])

    missing = ungrounded_numbers(full_text, cited.text, question)
    if missing:
        return ValidationResult(Action.RETRY, source_chunk_id=cited.chunk_id, issues=[
            f"numbers not in the cited passage: {', '.join(sorted(missing))}"])

    if len(sentences) > MAX_SENTENCES:
        fixes.append(f"truncated {len(sentences)} sentences to {MAX_SENTENCES}")
        sentences = sentences[:MAX_SENTENCES]

    return ValidationResult(Action.ACCEPT, sentences=sentences,
                            source_chunk_id=cited.chunk_id, fixes=sorted(set(fixes)))


def resolve_after_retry(result: ValidationResult) -> ValidationResult:
    """Call on the second (last) attempt: a RETRY becomes NOT_FOUND, never a bad answer."""
    if result.action is Action.RETRY:
        return ValidationResult(Action.NOT_FOUND, issues=result.issues + ["retry limit reached"])
    return result


def retry_feedback(result: ValidationResult) -> str:
    """Short explanation passed back to the model on the retry (GroqLLM `feedback`)."""
    return "; ".join(result.issues) + "."
