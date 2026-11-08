"""Rewrites an elliptical follow-up into a standalone question.

    previous: "What is the exit load of HDFC Mid Cap Fund?"
    follow-up: "and small cap?"   ->   "What is the exit load of HDFC Small Cap Fund?"

Only used when the router flags `needs_rewrite` (the message names no fact). Routing has
already run on the ORIGINAL text, so a rewrite can never turn an advisory question into a
factual one (edge case FU-11). If the Groq call fails, a deterministic rewrite is used
instead and the answer still goes ahead (edge case FU-10).
"""

import json
import logging
import re
from typing import Any

import groq

from app.router import ConversationContext

logger = logging.getLogger("mf_faq.followup")

MAX_TURNS = 3  # last 3 exchanges (6 messages)
MAX_LENGTH = 300

REWRITE_SCHEMA = {
    "type": "object",
    "properties": {"standalone_query": {"type": "string"}},
    "required": ["standalone_query"],
    "additionalProperties": False,
}

REWRITE_PROMPT = """\
You rewrite a follow-up message from a chat about HDFC mutual fund schemes into ONE
standalone question that makes sense without the conversation.
- Keep the user's intent; take the missing fact (e.g. exit load, expense ratio) and/or the
  missing scheme from the conversation.
- If the follow-up names a scheme, use that scheme; otherwise use the scheme given below.
- Do not answer the question. Do not add facts, opinions or new requests.
Respond with JSON only: {"standalone_query": "..."}"""


def fallback_rewrite(question: str, context: ConversationContext, scheme_name: str | None,
                     scheme_terms: list[str]) -> str:
    """No-LLM rewrite: the previous question with its scheme swapped for this one."""
    from app.retrieval import strip_scheme_terms

    previous = context.last_user_question()
    if not previous or not scheme_name:
        return question
    base = strip_scheme_terms(previous, scheme_terms).rstrip(" ?.")
    if base == previous.rstrip(" ?."):  # nothing was stripped: can't swap the scheme safely
        return f"{previous.rstrip(' ?.')} ({scheme_name})?"
    base = re.sub(r"\b(?:of|for|in|on)$", "", base).strip()
    return f"{base} of {scheme_name}?"


class FollowUpRewriter:
    def __init__(self, client: Any, model: str, scheme_terms: list[str],
                 reasoning_effort: str = "low"):
        self.client, self.model = client, model
        self.scheme_terms = scheme_terms
        self.reasoning_effort = reasoning_effort

    def rewrite(self, question: str, context: ConversationContext,
                scheme_name: str | None) -> tuple[str, str]:
        """Returns (standalone question, method) with method 'llm' or 'fallback'."""
        turns = context.history[-2 * MAX_TURNS:]
        conversation = "\n".join(f"{role}: {text}" for role, text in turns)
        user = (f"Conversation:\n{conversation or '(none)'}\n\n"
                f"Scheme in focus: {scheme_name or 'unknown'}\n"
                f"Follow-up: {question.strip()}")
        kwargs = {
            "model": self.model, "temperature": 0, "max_completion_tokens": 400,
            "messages": [{"role": "system", "content": REWRITE_PROMPT},
                         {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "standalone_query", "strict": True, "schema": REWRITE_SCHEMA}},
        }
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        try:
            response = self.client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            if choice.finish_reason == "length":
                raise ValueError("truncated")
            rewritten = json.loads(choice.message.content)["standalone_query"].strip()
            if not rewritten or len(rewritten) > MAX_LENGTH:
                raise ValueError("empty or too long")
            return rewritten, "llm"
        except (groq.GroqError, ValueError, KeyError, TypeError) as exc:
            logger.warning("follow-up rewrite failed (%s); using fallback", type(exc).__name__)
            return fallback_rewrite(question, context, scheme_name, self.scheme_terms), \
                "fallback"
