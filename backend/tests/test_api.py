"""API integration tests (implementation-plan Phase 5): real FastAPI app, real Pipeline +
Router + SQLite, with an in-memory retriever and a deterministic fake LLM."""

import asyncio
import re
import sqlite3
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm import LLMAnswer, LLMResult
from app.main import create_app
from app.pipeline import Pipeline
from app.registry import load_schemes
from app.retrieval import RetrievalResult, RetrievedChunk
from app.router import Router

SCHEMES = {
    "hdfc-mid-cap": ("HDFC Mid Cap Fund Direct Growth", "0.74%", "1 year"),
    "hdfc-small-cap": ("HDFC Small Cap Fund Direct Growth", "0.79%", "1 year"),
    "hdfc-large-cap": ("HDFC Large Cap Fund Direct Growth", "1.01%", "1 year"),
    "hdfc-multi-cap": ("HDFC Multi Cap Fund Direct Growth", "0.96%", "1 year"),
    "hdfc-silver-etf-fof": ("HDFC Silver ETF FoF Direct Growth", "0.21%", "15 days"),
}
AMFI, SEBI = "https://www.amfiindia.com/investor", "https://investor.sebi.gov.in/"


def _url(scheme):
    return f"https://groww.in/mutual-funds/{scheme}-fund-direct-growth"


def _chunks():
    out = []
    for scheme, (name, er, exit_period) in SCHEMES.items():
        base = {"scheme": scheme, "scheme_name": name, "url": _url(scheme),
                "doc_date": "2026-11-06", "fetched_at": "2026-11-07T10:00:00+00:00"}
        out.append(RetrievedChunk(f"groww-{scheme}#expense_ratio",
                                  f"Expense ratio: The expense ratio of {name} is {er}.",
                                  {**base, "field": "expense_ratio", "as_on": "2026-11-04"}, 0))
        out.append(RetrievedChunk(f"groww-{scheme}#exit_load",
                                  f"Exit load: Exit load for {name}: 1% if redeemed within "
                                  f"{exit_period}.", {**base, "field": "exit_load", "as_on": ""},
                                  0))
    return out


class KeywordRetriever:
    """Scores chunks by shared words; 'relevant' if any fact word matches."""

    def __init__(self):
        self.chunks = _chunks()
        self.metadatas = [c.metadata for c in self.chunks]

    def retrieve(self, query, scheme=None, k=5):
        words = set(re.findall(r"[a-z]+", query.lower())) - {"of", "the", "is", "what", "and"}
        scored = []
        for c in self.chunks:
            if scheme and c.metadata["scheme"] != scheme:
                continue
            overlap = len(words & set(re.findall(r"[a-z]+", c.text.lower())))
            scored.append((overlap, c))
        scored.sort(key=lambda p: -p[0])
        top = [c for s, c in scored[:k]]
        best = scored[0][0] if scored else 0
        relevant = any(w in query.lower() for w in ("expense", "exit", "load", "ratio"))
        return RetrievalResult(query=query, scheme=scheme, ranking_query=query, chunks=top,
                               best_similarity=float(best), relevant=relevant)


class EchoLLM:
    """Answers by quoting the top chunk (so numbers are always grounded)."""

    def __init__(self, delay=0.02, hook=None):
        self.delay, self.hook, self.calls = delay, hook, 0

    def generate(self, question, chunks, feedback=None):
        self.calls += 1
        if self.hook:
            self.hook()
        time.sleep(self.delay)  # lets concurrent requests interleave
        top = chunks[0]
        sentence = top.text.split(": ", 1)[1]
        return LLMResult(status="ok", attempts=1, prompt_tokens=700, completion_tokens=50,
                         answer=LLMAnswer(kind="answer", sentences=[sentence],
                                          source_chunk_id=top.chunk_id))


def make_pipeline(llm=None):
    schemes = load_schemes()
    return Pipeline(KeywordRetriever(), llm or EchoLLM(), AMFI,
                    scheme_names={k: s.name for k, s in schemes.items()},
                    router=Router(schemes), rewriter=None,
                    refusal_urls={"advice": AMFI, "comparison": AMFI, "prediction": SEBI})


def make_settings(tmp_path, **kw):
    base = dict(_env_file=None, data_dir=tmp_path, llm_api_key="gsk_test",
                api_messages_per_minute=1000)
    return Settings(**{**base, **kw})


@pytest.fixture
def app_and_llm(tmp_path):
    llm = EchoLLM()
    return create_app(make_settings(tmp_path), pipeline=make_pipeline(llm)), llm


@pytest.fixture
async def client(app_and_llm):
    app, _ = app_and_llm
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def new_thread(client) -> str:
    r = await client.post("/api/threads")
    assert r.status_code == 201
    return r.json()["id"]


async def ask(client, tid, text):
    return await client.post(f"/api/threads/{tid}/messages", json={"text": text})


def one_link_and_footer(msg: dict) -> bool:
    return bool(msg["source_url"]) and msg["source_url"].count("://") == 1 and \
        re.fullmatch(r"Last updated from sources: \d{2} [A-Z][a-z]{2} \d{4}", msg["footer"] or "")


# ---------------------------------------------------------------- endpoints


async def test_create_and_list_threads(client):
    a, b = await new_thread(client), await new_thread(client)
    r = await client.get("/api/threads", params={"ids": f"{a},{b},not-a-uuid,"
                                                        "00000000-0000-0000-0000-000000000000"})
    assert r.status_code == 200 and {t["id"] for t in r.json()} == {a, b}
    assert (await client.get("/api/threads")).json() == []


async def test_factual_question_is_answered_and_stored(client):
    tid = await new_thread(client)
    r = await ask(client, tid, "What is the expense ratio of HDFC Mid Cap?")
    assert r.status_code == 200
    body = r.json()
    msg = body["message"]
    assert msg["kind"] == "answer" and "0.74%" in msg["text"]
    assert msg["source_url"] == _url("hdfc-mid-cap") and one_link_and_footer(msg)
    assert body["user_message"]["text"] == "What is the expense ratio of HDFC Mid Cap?"
    assert body["thread"]["last_scheme"] == "hdfc-mid-cap"
    assert body["thread"]["title"].startswith("What is the expense ratio")
    history = (await client.get(f"/api/threads/{tid}/messages")).json()
    assert [(m["role"], m["kind"]) for m in history] == [("user", None), ("assistant", "answer")]


@pytest.mark.parametrize("question, kind", [
    ("What is the expense ratio of HDFC Mid Cap?", "answer"),
    ("Should I invest in HDFC Mid Cap?", "refusal"),
    ("3 year returns of HDFC Small Cap", "performance"),
    ("SBI Small Cap expense ratio", "out_of_scope"),
    ("exit load of mid cap and small cap", "clarify"),
    ("hi", "greeting"),
    ("Who is the auditor of HDFC Mid Cap?", "not_found"),
])
async def test_every_kind_has_one_link_and_footer(client, question, kind):
    tid = await new_thread(client)
    msg = (await ask(client, tid, question)).json()["message"]
    assert msg["kind"] == kind and one_link_and_footer(msg)


async def test_refusal_makes_no_llm_call(client, app_and_llm):
    _, llm = app_and_llm
    tid = await new_thread(client)
    await ask(client, tid, "Which is better, large cap or multi cap?")
    assert llm.calls == 0


# ---------------------------------------------------------------- PII


async def test_pii_message_is_not_stored_anywhere(client, app_and_llm):
    app, llm = app_and_llm
    tid = await new_thread(client)
    await ask(client, tid, "What is the expense ratio of HDFC Mid Cap?")
    r = await ask(client, tid, "My PAN is ABCDE1234F and phone 9876543210, help")
    body = r.json()
    assert r.status_code == 200 and body["user_message"] is None
    assert body["message"]["kind"] == "pii_blocked" and body["message"]["source_url"] is None
    assert llm.calls == 1  # only the first question
    history = (await client.get(f"/api/threads/{tid}/messages")).json()
    assert len(history) == 2
    with sqlite3.connect(app.state.db.path) as conn:
        dump = "\n".join(conn.iterdump())
    assert "ABCDE1234F" not in dump and "9876543210" not in dump


async def test_pii_logs_types_not_values(client, caplog):
    tid = await new_thread(client)
    with caplog.at_level("INFO", logger="mf_faq"):
        await ask(client, tid, "my email is rahul@gmail.com")
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "rahul@gmail.com" not in text and "email" in text


# ---------------------------------------------------------------- follow-ups & isolation


async def test_follow_up_uses_thread_scheme(client):
    tid = await new_thread(client)
    await ask(client, tid, "What is the expense ratio of HDFC Mid Cap?")
    msg = (await ask(client, tid, "And its exit load?")).json()["message"]
    assert msg["kind"] == "answer" and "HDFC Mid Cap" in msg["text"] and "1 year" in msg["text"]


async def test_threads_do_not_share_context(client):
    a, b = await new_thread(client), await new_thread(client)
    await ask(client, a, "expense ratio of HDFC Silver ETF FoF")
    msg = (await ask(client, b, "and its exit load?")).json()["message"]
    assert msg["kind"] == "clarify"  # thread B has no scheme yet (FU-12)


async def test_ten_parallel_requests_across_five_threads(client):
    """Phase 5 exit criterion: no errors, no message in the wrong thread or order."""
    schemes = list(SCHEMES)
    tids = [await new_thread(client) for _ in schemes]
    first = await asyncio.gather(*[ask(client, t, f"expense ratio of {SCHEMES[s][0]}")
                                   for t, s in zip(tids, schemes, strict=True)])
    second = await asyncio.gather(*[ask(client, t, "and its exit load?") for t in tids])
    for tid, scheme, r1, r2 in zip(tids, schemes, first, second, strict=True):
        assert r1.status_code == r2.status_code == 200
        assert r1.json()["message"]["source_url"] == _url(scheme)
        assert r2.json()["message"]["source_url"] == _url(scheme)
        history = (await client.get(f"/api/threads/{tid}/messages")).json()
        assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
        assert all(m["source_url"] in (None, _url(scheme)) for m in history)


async def test_same_thread_parallel_messages_keep_order(client):
    tid = await new_thread(client)
    await asyncio.gather(ask(client, tid, "expense ratio of HDFC Mid Cap"),
                         ask(client, tid, "exit load of HDFC Mid Cap"))
    history = (await client.get(f"/api/threads/{tid}/messages")).json()
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]


# ---------------------------------------------------------------- deletion


async def test_delete_removes_thread_and_messages(client, app_and_llm):
    app, _ = app_and_llm
    tid = await new_thread(client)
    await ask(client, tid, "expense ratio of HDFC Mid Cap")
    assert (await client.delete(f"/api/threads/{tid}")).status_code == 204
    assert (await client.get(f"/api/threads/{tid}/messages")).status_code == 404
    assert (await client.delete(f"/api/threads/{tid}")).status_code == 404
    with sqlite3.connect(app.state.db.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE thread_id = ?",
                            (tid,)).fetchone()[0] == 0


async def test_thread_deleted_while_answering_returns_410(tmp_path):
    holder = {}
    llm = EchoLLM(hook=lambda: holder["app"].state.db.delete_thread(holder["tid"]))
    app = create_app(make_settings(tmp_path), pipeline=make_pipeline(llm))
    holder["app"] = app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as c:
        holder["tid"] = await new_thread(c)
        r = await ask(c, holder["tid"], "expense ratio of HDFC Mid Cap")
    assert r.status_code == 410
    with sqlite3.connect(app.state.db.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0  # no orphans


# ---------------------------------------------------------------- errors & limits


async def test_error_codes(client):
    tid = await new_thread(client)
    missing = "00000000-0000-0000-0000-000000000000"
    assert (await ask(client, missing, "hi")).status_code == 404
    assert (await client.get(f"/api/threads/{missing}/messages")).status_code == 404
    assert (await client.get("/api/threads/not-a-uuid/messages")).status_code == 422
    assert (await ask(client, tid, "   ")).status_code == 422  # IN-01
    r = await ask(client, tid, "x" * 501)  # IN-02
    assert r.status_code == 422 and "500 characters" in r.json()["detail"]
    r = await client.post(f"/api/threads/{tid}/messages", content=b"{" + b" " * 20000 + b"}",
                          headers={"content-type": "application/json"})
    assert r.status_code == 413  # GAP-38


async def test_per_client_rate_limit(tmp_path):
    app = create_app(make_settings(tmp_path, api_messages_per_minute=2), pipeline=make_pipeline())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as c:
        tid = await new_thread(c)
        codes = [(await ask(c, tid, "hi")).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_degraded_mode_when_index_is_missing(tmp_path):
    def broken():
        raise RuntimeError("no index found; run `python -m app.ingest` first")

    app = create_app(make_settings(tmp_path), pipeline_factory=broken)
    with TestClient(app) as c:  # runs the startup (lifespan)
        health = c.get("/api/health").json()
        assert health["status"] == "degraded" and "no index" in health["index_error"]
        tid = c.post("/api/threads").json()["id"]
        r = c.post(f"/api/threads/{tid}/messages", json={"text": "hi"})
        assert r.status_code == 503 and "isn't ready" in r.json()["detail"]


async def test_web_ui_is_served_alongside_the_api(client):
    page = await client.get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert "Facts-only. No investment advice." in page.text
    assert page.headers["cache-control"] == "no-cache"
    for asset in ("/css/app.css", "/js/app.js", "/js/api.js", "/js/pii.js", "/js/icons.js"):
        assert (await client.get(asset)).status_code == 200, asset
    # API routes still win over the static mount.
    assert (await client.get("/api/health")).json()["status"] == "ok"
    assert (await client.get("/api/nope")).status_code == 404


async def test_meta_and_health(client):
    meta = (await client.get("/api/meta")).json()
    assert meta["disclaimer"] == "Facts-only. No investment advice."
    assert len(meta["example_questions"]) == 3 and len(meta["schemes"]) == 5
    assert meta["corpus_last_updated"] == "2026-11-06"
    health = (await client.get("/api/health")).json()
    assert health["status"] == "ok" and health["index_loaded"] is True
