"""Answer pipeline (implementation-plan.md, Phases 3-4):

    PII guard -> router -> template reply (no LLM)
                        -> or: follow-up rewrite -> retrieve -> gate -> LLM -> validate
                               (retry once) -> compose

    pipeline = build_pipeline()
    context = ConversationContext()
    r1 = pipeline.answer("What is the expense ratio of HDFC Mid Cap?", context=context)
    context.last_scheme = r1.scheme          # the caller keeps thread state (Phase 5: DB)
    r2 = pipeline.answer("And its exit load?", context=context)
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app import templates
from app.compose import (
    Response,
    SchemePage,
    compose_answer,
    compose_message,
    compose_not_found,
    compose_pii_blocked,
    compose_refusal,
    compose_unavailable,
)
from app.guards.pii import detect as detect_pii
from app.llm import ContextChunk, LLMResult
from app.retrieval import RetrievalResult
from app.router import ConversationContext, Route, Router
from app.validate import Action, resolve_after_retry, retry_feedback, validate

logger = logging.getLogger("mf_faq.pipeline")


class RetrieverLike(Protocol):
    metadatas: list[dict]

    def retrieve(self, query: str, scheme: str | None = None, k: int = 5) -> RetrievalResult: ...


class LLMLike(Protocol):
    def generate(self, question: str, chunks: list[ContextChunk],
                 feedback: str | None = None) -> LLMResult: ...


class RewriterLike(Protocol):
    def rewrite(self, question: str, context: ConversationContext,
                scheme_name: str | None) -> tuple[str, str]: ...


class Pipeline:
    def __init__(self, retriever: RetrieverLike, llm: LLMLike, education_url: str,
                 scheme_names: dict[str, str] | None = None, top_k: int = 5,
                 router: Router | None = None, rewriter: RewriterLike | None = None,
                 refusal_urls: dict[str, str] | None = None):
        self.retriever, self.llm = retriever, llm
        self.education_url, self.top_k = education_url, top_k
        self.router, self.rewriter = router, rewriter
        self.limiter = None  # set by build_pipeline (shared Groq budget, for /api/health)
        self.index_source: IndexSource | None = None  # set by build_pipeline (hot reload)
        # Refusal variant -> education link (prediction -> SEBI, others -> AMFI).
        self.refusal_urls = refusal_urls or {}
        self.scheme_names = scheme_names or {}
        self.pages, self.corpus_date = self._pages_for(retriever)

    def _pages_for(self, retriever) -> tuple[dict[str, SchemePage], str | None]:
        pages: dict[str, SchemePage] = {}
        for meta in retriever.metadatas:
            scheme = meta["scheme"]
            if scheme not in pages:
                pages[scheme] = SchemePage(
                    scheme=scheme,
                    name=self.scheme_names.get(scheme, meta.get("scheme_name", scheme)),
                    url=meta["url"], date=meta.get("doc_date") or None)
        dates = [p.date for p in pages.values() if p.date]
        return pages, (max(dates) if dates else None)

    def use_retriever(self, retriever: RetrieverLike) -> None:
        """Switch to a new index version (daily refresh). Requests already running finish
        on the old retriever; new ones use the new one and its dates."""
        pages, corpus_date = self._pages_for(retriever)
        self.pages, self.corpus_date = pages, corpus_date
        self.retriever = retriever

    def _page(self, scheme: str | None) -> SchemePage | None:
        return self.pages.get(scheme) if scheme else None

    def _message(self, kind, text: str, page: SchemePage | None = None) -> Response:
        return compose_message(kind, text, page, self.education_url, self.corpus_date)

    # ------------------------------------------------------------ entry point

    def answer(self, question: str, scheme: str | None = None,
               context: ConversationContext | None = None) -> Response:
        """`scheme` forces the scheme (skips scheme resolution, e.g. from the CLI);
        `context` carries the thread's last scheme and recent turns."""
        started = time.perf_counter()
        context = context or ConversationContext()
        response, debug = self._answer(question, scheme, context)
        debug["latency_ms"] = int((time.perf_counter() - started) * 1000)
        response.debug = debug
        # Kinds and IDs only; the question text isn't logged (privacy by default).
        logger.info("answer kind=%s intent=%s scheme=%s cited=%s latency_ms=%d",
                    response.kind, debug.get("intent"), response.scheme,
                    response.source_chunk_id, debug["latency_ms"])
        return response

    def _answer(self, question: str, forced_scheme: str | None,
                context: ConversationContext) -> tuple[Response, dict]:
        # 1. PII first: nothing below (router, rewrite, retrieval, LLM, logs) sees it.
        pii = detect_pii(question)
        if pii:
            response = compose_pii_blocked()
            response.scheme = context.last_scheme  # thread state unchanged
            return response, {"intent": "pii_blocked", "pii_types": [str(p) for p in pii],
                              "llm_calls": 0}

        # 2. Route on the ORIGINAL text (edge case FU-11).
        route = self._route(question, forced_scheme, context)
        debug: dict = {"intent": route.intent, "route_reason": route.reason,
                       "scheme": route.scheme, "scheme_from_context": route.from_context,
                       "llm_calls": 0}
        response = self._template_reply(route)
        if response is None:
            response = self._factual(question, route, context, debug)
        # Thread state: the scheme in focus, set even on refusals (edge case FU-03).
        response.scheme = route.scheme
        return response, debug

    def _route(self, question: str, forced_scheme: str | None,
               context: ConversationContext) -> Route:
        if self.router is None:
            return Route("factual", scheme=forced_scheme)
        route = self.router.route(question, context)
        if forced_scheme:
            route.scheme, route.from_context = forced_scheme, False
            if route.clarify in ("multiple_schemes", "no_scheme", "performance_no_scheme"):
                route.intent = "performance" if route.clarify == "performance_no_scheme" \
                    else "factual"
                route.clarify = None
        return route

    # ------------------------------------------------------------ template replies

    def _template_reply(self, route: Route) -> Response | None:
        page = self._page(route.scheme)
        if route.intent == "greeting":
            return self._message("greeting", templates.GREETING)
        if route.intent == "advisory":
            url = self.refusal_urls.get(route.variant or "advice", self.education_url)
            return compose_refusal(url, self.corpus_date, route.variant or "advice")
        if route.intent == "performance":
            if page is None:
                return self._message("clarify", templates.PERFORMANCE_WHICH_SCHEME)
            return self._message("performance", templates.performance(page.name), page)
        if route.intent == "out_of_scope":
            return self._message("out_of_scope", templates.OUT_OF_SCOPE)
        if route.intent in ("clarify", "unclear"):
            if route.clarify == "multiple_schemes":
                names = [self.pages[s].name for s in route.schemes_mentioned if s in self.pages]
                return self._message("clarify", templates.clarify_one_at_a_time(names))
            if route.clarify == "performance_no_scheme":
                return self._message("clarify", templates.PERFORMANCE_WHICH_SCHEME)
            if route.clarify == "no_scheme":
                return self._message("clarify", templates.CLARIFY_SCHEME)
            return self._message("clarify", templates.CLARIFY_UNCLEAR)
        return None

    # ------------------------------------------------------------ factual path

    def _factual(self, question: str, route: Route, context: ConversationContext,
                 debug: dict) -> Response:
        scheme = route.scheme
        query = question
        if route.needs_rewrite and self.rewriter is not None:
            page = self._page(scheme)
            query, method = self.rewriter.rewrite(question, context, page.name if page else None)
            debug["rewritten_query"], debug["rewrite_method"] = query, method
            debug["llm_calls"] += method == "llm"
            # Re-route the rewritten question: "What are the returns?" + "HDFC Mid Cap Fund"
            # becomes a returns question and must get the performance reply. Only stricter
            # outcomes are taken, so a rewrite can never loosen the original routing.
            if self.router is not None:
                again = self.router.route(query, context)
                if again.intent in ("advisory", "performance", "out_of_scope"):
                    again.scheme = again.scheme or scheme
                    route.scheme = again.scheme
                    debug["intent"] = f"{again.intent} (after rewrite)"
                    return self._template_reply(again)

        ret = self.retriever.retrieve(query, scheme=scheme, k=self.top_k)
        debug.update({
            "best_similarity": round(ret.best_similarity, 3),
            "retrieved": [c.chunk_id for c in ret.chunks],
            "ranking_query": ret.ranking_query,
        })
        page = self._page(scheme)
        if not ret.relevant or not ret.chunks:
            debug["reason"] = "below relevance threshold"
            return compose_not_found(page, self.education_url, self.corpus_date)

        # The question names a fact, no scheme is known, and that fact matched in several
        # schemes -> ask which scheme (edge case GAP-18). Questions that name no fact
        # ("how do I download my statement?") go to the LLM, which says not_found.
        if scheme is None and route.has_fact and self._ambiguous_across_schemes(ret):
            debug["reason"] = "same fact matched in several schemes"
            return self._message("clarify", templates.CLARIFY_SCHEME)

        result = self.llm.generate(query, ret.chunks)
        debug["llm_calls"] += result.attempts
        self._add_usage(debug, result)
        if result.status == "error":
            debug["reason"] = f"llm error: {result.error_kind}"
            return compose_unavailable(page, self.education_url, self.corpus_date)

        verdict = validate(result.answer, ret.chunks, query, expected_scheme=scheme)
        if verdict.action is Action.RETRY:
            debug["first_attempt_issues"] = verdict.issues
            result = self.llm.generate(query, ret.chunks, feedback=retry_feedback(verdict))
            debug["llm_calls"] += result.attempts
            self._add_usage(debug, result)
            if result.status == "error":
                debug["reason"] = f"llm error on retry: {result.error_kind}"
                return compose_unavailable(page, self.education_url, self.corpus_date)
            verdict = resolve_after_retry(
                validate(result.answer, ret.chunks, query, expected_scheme=scheme))

        debug["validation"] = {"action": verdict.action.value, "issues": verdict.issues,
                               "fixes": verdict.fixes}
        if verdict.action is Action.ACCEPT:
            cited = next(c for c in ret.chunks if c.chunk_id == verdict.source_chunk_id)
            if scheme is None:
                route.scheme = cited.metadata.get("scheme")  # now the thread's scheme
            return compose_answer(verdict.sentences, cited.chunk_id, cited.metadata)
        if verdict.action is Action.REFUSAL:
            return compose_refusal(self.refusal_urls.get("advice", self.education_url),
                                   self.corpus_date)

        # NOT_FOUND: link the scheme asked about, else the closest match's page (the gate
        # passed, so the top chunk is at least on-topic) without naming that scheme.
        if page:
            return compose_not_found(page, self.education_url, self.corpus_date)
        closest = self._page(ret.chunks[0].metadata.get("scheme"))
        return compose_not_found(closest, self.education_url, self.corpus_date,
                                 name_scheme=False)

    @staticmethod
    def _ambiguous_across_schemes(ret: RetrievalResult) -> bool:
        top = ret.chunks[:3]
        fields = [c.metadata.get("field") for c in top]
        return (len(top) >= 2 and fields[0] and fields.count(fields[0]) >= 2
                and len({c.metadata.get("scheme") for c in top
                         if c.metadata.get("field") == fields[0]}) >= 2)

    @staticmethod
    def _add_usage(debug: dict, result: LLMResult) -> None:
        debug["prompt_tokens"] = debug.get("prompt_tokens", 0) + result.prompt_tokens
        debug["completion_tokens"] = debug.get("completion_tokens", 0) + result.completion_tokens
        debug["llm_latency_ms"] = debug.get("llm_latency_ms", 0) + result.latency_ms


@dataclass
class IndexSource:
    """How the app finds and loads index versions, for reloading after a refresh."""

    catalog: Any  # app.ingest.catalog.Catalog
    make_retriever: Callable[[str], Any]  # version -> Retriever


def reload_if_changed(pipeline: Pipeline) -> str | None:
    """If the catalog's live version differs from the one being served, load it and swap
    it in. Returns the new version, or None. A failed load keeps the current version."""
    source = pipeline.index_source
    if source is None:
        return None
    live = source.catalog.active()
    current = getattr(pipeline.retriever, "version", None)
    if live is None or live["version"] == current:
        return None
    retriever = source.make_retriever(live["version"])
    pipeline.use_retriever(retriever)
    logger.info("switched to index version %s (was %s)", live["version"], current)
    return live["version"]


class UnavailableLLM:
    """Stand-in when no LLM is configured: factual questions get the friendly
    'unavailable' reply, while PII, refusal and template replies keep working (LLM-11)."""

    def generate(self, question, chunks, feedback=None) -> LLMResult:
        return LLMResult(status="error", error_kind="auth", error="LLM not configured")


def build_limiter(settings):
    from app.ratelimit import RateLimiter

    return RateLimiter(rpm=settings.llm_requests_per_minute,
                       tpm=settings.llm_tokens_per_minute,
                       rpd=settings.llm_requests_per_day,
                       tpd=settings.llm_tokens_per_day,
                       max_wait=settings.llm_max_wait_seconds)


def build_pipeline(settings=None, limiter=None) -> Pipeline:
    """Wire the real components from settings and the data/ registries. Every Groq call
    (answers and follow-up rewrites) goes through one shared rate limiter."""
    import groq

    from app.config import get_settings
    from app.embeddings import get_embedder
    from app.followup import FollowUpRewriter
    from app.llm import GroqLLM
    from app.ratelimit import RateLimitedClient
    from app.registry import load_refusal_links, load_schemes
    from app.retrieval import Retriever, scheme_terms
    from app.vectorstore import get_vector_store

    settings = settings or get_settings()
    data_dir = settings.resolved_data_dir()
    schemes = load_schemes(data_dir)
    from app.ingest.catalog import get_catalog

    store = get_vector_store(settings, data_dir)
    catalog = get_catalog(store, data_dir)

    def make_retriever(version: str | None = None) -> Retriever:
        return Retriever(data_dir, get_embedder(settings.embed_model),
                         settings.relevance_threshold, store=store, schemes=schemes,
                         catalog=catalog, version=version)

    retriever = make_retriever()
    links = load_refusal_links(data_dir)
    amfi = links["amfi_investor_education"]["url"]
    sebi = links.get("sebi_investor_education", {}).get("url", amfi)
    limiter = limiter or build_limiter(settings)
    if settings.llm_configured:
        client = RateLimitedClient(
            groq.Groq(api_key=settings.llm_api_key.get_secret_value(),
                      timeout=settings.llm_timeout_seconds,
                      max_retries=settings.llm_max_retries),
            limiter)
        llm = GroqLLM(settings, client=client)
        rewriter = FollowUpRewriter(client, settings.llm_model, scheme_terms(schemes),
                                    settings.llm_reasoning_effort)
    else:
        logger.warning("LLM_API_KEY not set: factual answers unavailable")
        llm, rewriter = UnavailableLLM(), None
    pipeline = Pipeline(retriever, llm, amfi,
                        scheme_names={k: s.name for k, s in schemes.items()},
                        top_k=settings.retrieval_top_k, router=Router(schemes),
                        rewriter=rewriter,
                        refusal_urls={"advice": amfi, "comparison": amfi, "prediction": sebi})
    pipeline.limiter = limiter
    pipeline.index_source = IndexSource(catalog=catalog, make_retriever=make_retriever)
    return pipeline
