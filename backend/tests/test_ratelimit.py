from types import SimpleNamespace

import groq
import pytest

from app.ratelimit import BudgetExceeded, RateLimitedClient, RateLimiter, estimate_tokens


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


def limiter(clock, **kw):
    params = dict(rpm=30, tpm=8000, rpd=1000, tpd=200000, max_wait=10.0)
    params.update(kw)
    return RateLimiter(**params, clock=clock, sleep=clock.sleep)


def test_requests_within_budget_pass_immediately():
    clock = Clock()
    lim = limiter(clock)
    for _ in range(8):
        lim.acquire(900)
    assert clock.slept == []
    assert lim.snapshot()["tokens_last_minute"] == 7200


def test_tokens_per_minute_is_enforced_with_a_short_wait():
    clock = Clock()
    lim = limiter(clock)
    for _ in range(8):  # 7,200 tokens
        lim.acquire(900)
    clock.t += 55  # oldest reservations expire in ~5 s
    lim.acquire(900)  # 8,100 > 8,000 -> waits for the oldest to expire
    assert 4.9 < clock.slept[0] < 5.2


def test_long_wait_fails_fast_instead_of_blocking():
    clock = Clock()
    lim = limiter(clock)
    for _ in range(8):
        lim.acquire(900)
    with pytest.raises(BudgetExceeded) as err:
        lim.acquire(900)  # would need ~60 s, more than max_wait
    assert err.value.scope == "per-minute" and isinstance(err.value, groq.RateLimitError)


def test_requests_per_minute_is_enforced():
    clock = Clock()
    lim = limiter(clock, rpm=3, max_wait=0)
    for _ in range(3):
        lim.acquire(10)
    with pytest.raises(BudgetExceeded):
        lim.acquire(10)


def test_daily_budget_fails_fast():
    clock = Clock()
    lim = limiter(clock, rpd=2)
    lim.acquire(10)
    clock.t += 120
    lim.acquire(10)
    clock.t += 120
    with pytest.raises(BudgetExceeded) as err:
        lim.acquire(10)
    assert err.value.scope == "daily" and clock.slept == []


def test_rolling_day_window_frees_up():
    clock = Clock()
    lim = limiter(clock, tpd=1000)
    lim.acquire(900)
    clock.t += 86401
    lim.acquire(900)  # yesterday's usage no longer counts


def test_settle_replaces_estimate_with_real_usage():
    clock = Clock()
    lim = limiter(clock)
    reservation = lim.acquire(2000)
    lim.settle(reservation, 850)
    assert lim.snapshot()["tokens_last_minute"] == 850


def test_wrapped_client_reserves_and_settles():
    clock = Clock()
    lim = limiter(clock)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(usage=SimpleNamespace(total_tokens=777))

    inner = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client = RateLimitedClient(inner, lim)
    client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
    assert len(calls) == 1 and lim.snapshot()["tokens_last_minute"] == 777


def test_estimate_includes_prompt_and_output():
    kwargs = {"messages": [{"role": "user", "content": "x" * 3000}],
              "max_completion_tokens": 1024}
    # ~3,030 JSON chars / 3.1 ≈ 977 prompt tokens + 150 expected output
    assert 1050 < estimate_tokens(kwargs) < 1200
