"""FastAPI app: multi-thread chat API (architecture §8.2, implementation-plan Phase 5).

    uvicorn app.main:app --port 8000

Routes:
    POST   /api/threads                   create a thread
    GET    /api/threads?ids=a,b           threads the client already knows
    GET    /api/threads/{id}/messages     history (oldest first; ?limit=&before= to page)
    POST   /api/threads/{id}/messages     ask a question -> assistant reply
    DELETE /api/threads/{id}              delete a thread and its messages
    GET    /api/meta                      welcome text, examples, disclaimer, corpus date
    GET    /api/health                    status, index, LLM and Groq budget
    GET    /                              the web UI (frontend/, no build step)
"""

import asyncio
import logging
import sqlite3
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import templates
from app.config import Settings, get_settings
from app.db import Database, ThreadGone
from app.guards.pii import detect as detect_pii
from app.router import ConversationContext
from app.schemas import MessageIn, MessageOut, MetaOut, PostMessageOut, ThreadOut

logger = logging.getLogger("mf_faq.api")
HISTORY_MESSAGES = 6  # last 3 exchanges for follow-ups
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


class ClientRateLimiter:
    """Sliding one-minute window per client IP (edge case GAP-37)."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> float | None:
        """None if allowed, else seconds until the next request is allowed."""
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] >= 60:
            hits.popleft()
        if len(hits) >= self.per_minute:
            return 60 - (now - hits[0])
        hits.append(now)
        return None


def create_app(settings: Settings | None = None, pipeline=None, pipeline_factory=None) -> FastAPI:
    """`pipeline` (already built, e.g. a test double) is used as-is; otherwise
    `pipeline_factory` (default: the real build_pipeline) runs at startup."""
    settings = settings or get_settings()
    data_dir = settings.resolved_data_dir()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from app.logging_config import setup_logging

        setup_logging()
        if app.state.pipeline is None:
            factory = pipeline_factory
            if factory is None:
                from app.pipeline import build_pipeline

                def factory():
                    return build_pipeline(settings)
            try:
                app.state.pipeline = await run_in_threadpool(factory)
            except Exception as exc:  # edge case RET-10 / GAP-20: start in degraded mode
                app.state.pipeline_error = f"{type(exc).__name__}: {exc}"
                logger.error("pipeline not ready", extra={"error": app.state.pipeline_error})
        if not settings.llm_configured:
            logger.warning("LLM_API_KEY is not set: factual answers will be unavailable")
        watcher = None
        if settings.index_refresh_seconds > 0 and \
                getattr(app.state.pipeline, "index_source", None) is not None:
            watcher = asyncio.create_task(_watch_index(app, settings.index_refresh_seconds))
        yield
        if watcher:
            watcher.cancel()

    app = FastAPI(title="Mutual Fund FAQ Assistant", version="0.5.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = Database(data_dir / settings.db_file)
    app.state.pipeline = pipeline
    app.state.pipeline_error = None
    app.state.locks: dict[str, asyncio.Lock] = {}
    app.state.client_limiter = ClientRateLimiter(settings.api_messages_per_minute)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.api_max_request_bytes:
            return JSONResponse({"detail": "Request too large."}, status_code=413)
        return await call_next(request)

    @app.exception_handler(sqlite3.OperationalError)
    async def db_busy(request: Request, exc: sqlite3.OperationalError):
        logger.error("database error", extra={"error": str(exc)})
        return JSONResponse({"detail": "The service is busy. Please try again."},
                            status_code=503)

    db: Database = app.state.db

    def _thread_or_404(thread_id: str) -> dict:
        thread = db.get_thread(thread_id)
        if thread is None:
            raise HTTPException(404, "Thread not found.")
        return thread

    # ------------------------------------------------------------ threads

    @app.post("/api/threads", status_code=201, response_model=ThreadOut)
    def create_thread():
        return db.create_thread()

    @app.get("/api/threads", response_model=list[ThreadOut])
    def list_threads(ids: str = Query("", description="comma-separated thread ids")):
        wanted = [i for i in (s.strip() for s in ids.split(",")) if _is_uuid(i)][:100]
        return db.list_threads(wanted)

    @app.delete("/api/threads/{thread_id}", status_code=204)
    def delete_thread(thread_id: uuid.UUID):
        if not db.delete_thread(str(thread_id)):
            raise HTTPException(404, "Thread not found.")
        app.state.locks.pop(str(thread_id), None)
        return Response(status_code=204)

    @app.get("/api/threads/{thread_id}/messages", response_model=list[MessageOut])
    def get_messages(thread_id: uuid.UUID, limit: int = Query(100, ge=1, le=200),
                     before: str | None = None):
        _thread_or_404(str(thread_id))
        return [MessageOut.from_row(m) for m in db.get_messages(str(thread_id), limit, before)]

    # ------------------------------------------------------------ ask

    @app.post("/api/threads/{thread_id}/messages", response_model=PostMessageOut)
    async def post_message(thread_id: uuid.UUID, body: MessageIn, request: Request):
        tid = str(thread_id)
        if len(body.text) > settings.max_message_chars:
            raise HTTPException(422, f"Please keep questions under "
                                     f"{settings.max_message_chars} characters.")  # IN-02
        client = request.client.host if request.client else "unknown"
        wait = app.state.client_limiter.check(client)
        if wait is not None:
            raise HTTPException(429, "Too many questions. Please wait a moment.",
                                headers={"Retry-After": str(int(wait) + 1)})
        if app.state.pipeline is None:
            raise HTTPException(503, "The knowledge base isn't ready yet. Please try later.")
        # One request at a time per thread keeps message order; other threads run freely.
        lock = app.state.locks.setdefault(tid, asyncio.Lock())
        async with lock:
            return await run_in_threadpool(_handle_message, tid, body.text)

    def _handle_message(tid: str, text: str) -> PostMessageOut:
        started = time.perf_counter()
        thread = _thread_or_404(tid)

        # PII first: nothing is stored, sent to the LLM or logged except the types.
        pii = detect_pii(text)
        if pii:
            logger.info("message blocked", extra={"thread_id": tid, "kind": "pii_blocked",
                                                  "pii_types": [str(p) for p in pii]})
            reply = MessageOut(id=None, role="assistant", kind="pii_blocked",
                               text=templates.PII_WARNING, source_url=None, last_updated=None,
                               footer=None, created_at=None)
            return PostMessageOut(user_message=None, message=reply, thread=ThreadOut(**thread))

        context = ConversationContext(last_scheme=thread["last_scheme"],
                                      history=db.recent_history(tid, HISTORY_MESSAGES))
        first_question = db.count_user_messages(tid) == 0
        try:
            user_row = db.add_message(tid, "user", text)
        except ThreadGone as exc:
            raise HTTPException(404, "Thread not found.") from exc

        response = app.state.pipeline.answer(text, context=context)

        try:
            reply_row = db.add_message(tid, "assistant", response.text, response.kind,
                                       response.source_url, response.last_updated)
        except ThreadGone as exc:  # deleted while the answer was being generated (THR-03)
            raise HTTPException(410, "This chat was deleted.") from exc
        db.update_after_turn(tid, response.scheme, text if first_question else None)

        debug = response.debug
        logger.info("message answered", extra={
            "thread_id": tid, "kind": response.kind, "intent": debug.get("intent"),
            "scheme": response.scheme, "cited": response.source_chunk_id,
            "llm_calls": debug.get("llm_calls", 0),
            "prompt_tokens": debug.get("prompt_tokens", 0),
            "completion_tokens": debug.get("completion_tokens", 0),
            "latency_ms": int((time.perf_counter() - started) * 1000)})
        return PostMessageOut(user_message=MessageOut.from_row(user_row),
                              message=MessageOut.from_row(reply_row),
                              thread=ThreadOut(**db.get_thread(tid)))

    # ------------------------------------------------------------ meta / health

    @app.get("/api/meta", response_model=MetaOut)
    def meta():
        from app.registry import load_schemes

        pipeline = app.state.pipeline
        try:  # registry order (Mid, Small, Large, Multi, Silver)
            schemes = [{"id": k, "name": s.name} for k, s in load_schemes(data_dir).items()]
        except FileNotFoundError:
            schemes = [{"id": p.scheme, "name": p.name}
                       for p in (pipeline.pages.values() if pipeline else [])]
        return MetaOut(
            welcome=templates.WELCOME,
            example_questions=list(templates.EXAMPLE_QUESTIONS),
            disclaimer=templates.DISCLAIMER,
            schemes=schemes,
            corpus_last_updated=getattr(pipeline, "corpus_date", None),
            max_message_chars=settings.max_message_chars,
        )

    @app.get("/api/health")
    def health() -> dict:
        pipeline = app.state.pipeline
        limiter = getattr(pipeline, "limiter", None)
        retriever = getattr(pipeline, "retriever", None)
        manifest = getattr(retriever, "manifest", None) or {}
        corpus_date = getattr(pipeline, "corpus_date", None)
        return {
            "status": "ok" if pipeline is not None else "degraded",
            "llm_configured": settings.llm_configured,
            "llm_model": settings.llm_model,
            "index_loaded": pipeline is not None,
            "index_error": app.state.pipeline_error,
            "vector_store": settings.vector_store,
            "index_version": getattr(retriever, "version", None),
            "index_built_at": manifest.get("built_at"),
            "corpus_date": corpus_date,
            "stale": is_stale(corpus_date, settings.stale_after_days),
            "llm_budget": limiter.snapshot() if limiter else None,
        }

    # ------------------------------------------------------------ web UI (Phase 6)
    # Mounted last so /api/* routes win. No build step: frontend/ is served as-is.
    if FRONTEND_DIR.is_dir():
        from fastapi.staticfiles import StaticFiles

        @app.middleware("http")
        async def no_cache_ui(request: Request, call_next):
            response = await call_next(request)
            if not request.url.path.startswith("/api/"):
                response.headers["Cache-Control"] = "no-cache"
            return response

        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="ui")

    return app


def is_stale(corpus_date: str | None, max_age_days: int, today: date | None = None) -> bool:
    """True when the data is older than `max_age_days` (a missed or failing daily refresh),
    or when there is no data date at all."""
    if not corpus_date:
        return True
    return (today or date.today()) - date.fromisoformat(corpus_date) > timedelta(days=max_age_days)


async def _watch_index(app: FastAPI, every_seconds: int) -> None:
    """Pick up index versions activated by the daily refresh, without a restart."""
    from app.pipeline import reload_if_changed

    while True:
        await asyncio.sleep(every_seconds)
        pipeline = app.state.pipeline
        if pipeline is None:
            continue
        try:
            version = await run_in_threadpool(reload_if_changed, pipeline)
            if version:
                logger.info("index reloaded", extra={"index_version": version,
                                                     "corpus_date": pipeline.corpus_date})
        except Exception as exc:  # keep serving the current version
            logger.error("index reload failed", extra={"error": f"{type(exc).__name__}: {exc}"})


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


app = create_app()
