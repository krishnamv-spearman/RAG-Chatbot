"""Client-side budget for the Groq API, shared by every LLM call in the process.

Groq limits for openai/gpt-oss-120b on this account:
    30 requests/minute · 1,000 requests/day · 8,000 tokens/minute · 200,000 tokens/day

Tokens per minute is the binding limit: one factual answer costs ~800-900 tokens
(prompt + completion), so only ~8-9 answers fit in a minute. Before each call we reserve
an estimate; afterwards we replace it with the real usage. If the per-minute budget frees
up within `max_wait` seconds the call waits; otherwise (or when a daily budget is used
up) it fails fast with a RateLimitError, which the pipeline turns into the friendly
"temporarily unavailable" reply instead of a Groq 429.

Windows are rolling (last 60 s / last 24 h) and in memory: a restart forgets usage, so
the daily caps are best effort.
"""

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import groq
import httpx

MINUTE, DAY = 60.0, 86400.0


@dataclass
class Reservation:
    at: float
    tokens: int


class BudgetExceeded(groq.RateLimitError):
    """Raised locally (no request sent). Subclasses groq.RateLimitError so callers treat
    it exactly like a Groq 429."""

    def __init__(self, scope: str, retry_after: float):
        self.scope, self.retry_after = scope, retry_after
        request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        super().__init__(
            f"local {scope} budget exhausted; retry in ~{int(retry_after) + 1}s",
            response=httpx.Response(429, request=request), body=None)


class RateLimiter:
    def __init__(self, rpm: int, tpm: int, rpd: int, tpd: int, max_wait: float = 10.0,
                 clock=time.monotonic, sleep=time.sleep):
        self.rpm, self.tpm, self.rpd, self.tpd = rpm, tpm, rpd, tpd
        self.max_wait = max_wait
        self._clock, self._sleep = clock, sleep
        self._lock = threading.Lock()
        self._events: deque[Reservation] = deque()

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0].at >= DAY:
            self._events.popleft()

    def _usage(self, now: float) -> tuple[int, int, int, int]:
        minute = [e for e in self._events if now - e.at < MINUTE]
        return (len(minute), sum(e.tokens for e in minute),
                len(self._events), sum(e.tokens for e in self._events))

    def _minute_wait(self, now: float, tokens: int) -> float:
        """Seconds until enough of the last minute's usage expires for this request."""
        minute = [e for e in self._events if now - e.at < MINUTE]
        req_over = len(minute) + 1 - self.rpm
        tok_over = sum(e.tokens for e in minute) + tokens - self.tpm
        wait = 0.0
        freed_req = freed_tok = 0
        for e in minute:  # oldest first
            if freed_req >= req_over and freed_tok >= tok_over:
                break
            freed_req += 1
            freed_tok += e.tokens
            wait = MINUTE - (now - e.at)
        return max(wait, 0.0)

    def acquire(self, tokens: int) -> Reservation:
        tokens = min(tokens, self.tpm)  # a single huge request must still be possible
        deadline = self._clock() + self.max_wait
        while True:
            with self._lock:
                now = self._clock()
                self._prune(now)
                req_m, tok_m, req_d, tok_d = self._usage(now)
                if req_d + 1 > self.rpd or tok_d + tokens > self.tpd:
                    oldest = self._events[0].at if self._events else now
                    raise BudgetExceeded("daily", DAY - (now - oldest))
                if req_m + 1 <= self.rpm and tok_m + tokens <= self.tpm:
                    reservation = Reservation(now, tokens)
                    self._events.append(reservation)
                    return reservation
                wait = self._minute_wait(now, tokens)
            if self._clock() + wait > deadline:
                raise BudgetExceeded("per-minute", wait)
            self._sleep(wait + 0.05)

    def settle(self, reservation: Reservation, actual_tokens: int | None) -> None:
        if actual_tokens is not None:
            with self._lock:
                reservation.tokens = int(actual_tokens)

    def snapshot(self) -> dict:
        with self._lock:
            now = self._clock()
            self._prune(now)
            req_m, tok_m, req_d, tok_d = self._usage(now)
        return {
            "requests_last_minute": req_m, "tokens_last_minute": tok_m,
            "requests_last_day": req_d, "tokens_last_day": tok_d,
            "limits": {"rpm": self.rpm, "tpm": self.tpm, "rpd": self.rpd, "tpd": self.tpd},
        }


# Calibrated on live calls (08 Nov 2026, gpt-oss-120b): a 2,309-character prompt was ~742
# tokens (≈3.1 chars/token); completions (incl. reasoning at effort "low") were 87-139.
CHARS_PER_TOKEN = 3.1
EXPECTED_COMPLETION_TOKENS = 150


def estimate_tokens(kwargs: dict) -> int:
    """Prompt size plus expected output, slightly above real usage (~895 estimated vs ~830
    actual per answer). Replaced by the real usage once the response arrives, so the
    estimate only matters while calls are in flight."""
    prompt = int(len(json.dumps(kwargs.get("messages", []), ensure_ascii=False))
                 / CHARS_PER_TOKEN)
    completion = min(int(kwargs.get("max_completion_tokens") or 400),
                     EXPECTED_COMPLETION_TOKENS)
    return prompt + completion


class _Completions:
    def __init__(self, inner: Any, limiter: RateLimiter):
        self._inner, self._limiter = inner, limiter

    def create(self, **kwargs):
        reservation = self._limiter.acquire(estimate_tokens(kwargs))
        response = self._inner.create(**kwargs)
        usage = getattr(response, "usage", None)
        self._limiter.settle(reservation, getattr(usage, "total_tokens", None))
        return response


class RateLimitedClient:
    """Wraps a Groq client so every `chat.completions.create` goes through the budget."""

    def __init__(self, client: Any, limiter: RateLimiter):
        self._client, self.limiter = client, limiter
        self.chat = type("Chat", (), {})()
        self.chat.completions = _Completions(client.chat.completions, limiter)
