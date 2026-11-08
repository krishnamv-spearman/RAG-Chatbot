"""Builds the final response. Code, not the model, adds the one link and the footer, so
every response has exactly one link and a "Last updated from sources" date.

    <up to 3 sentences>

    Source: <url>
    Last updated from sources: <DD Mon YYYY>

The one exception is the PII warning, which is a security notice rather than an answer and
carries no link or footer (edge case CIT-08 / GAP-29).
"""

from dataclasses import dataclass, field
from typing import Literal

from app import templates
from app.ingest.normalize import display_date

Kind = Literal["answer", "refusal", "not_found", "unavailable", "performance", "clarify",
               "out_of_scope", "greeting", "pii_blocked"]
FOOTER = "Last updated from sources: {date}"


@dataclass(frozen=True)
class SchemePage:
    """A scheme's Groww page: the link used when there's no specific fact to cite."""

    scheme: str
    name: str
    url: str
    date: str | None  # ISO date of the page data


@dataclass
class Response:
    kind: Kind
    text: str  # body only (no link / footer)
    source_url: str | None
    last_updated: str | None  # ISO date
    source_chunk_id: str | None = None
    scheme: str | None = None  # scheme the conversation is now about (for thread state)
    debug: dict = field(default_factory=dict)

    @property
    def footer(self) -> str:
        return FOOTER.format(date=display_date(self.last_updated) or "unknown")

    def render(self) -> str:
        if self.source_url is None:
            return self.text
        return f"{self.text}\n\nSource: {self.source_url}\n{self.footer}"


def source_date(metadata: dict) -> str | None:
    """Arch D3: the date printed on the source (page data date), else the fetch date."""
    return metadata.get("doc_date") or (metadata.get("fetched_at") or "")[:10] or None


def compose_answer(sentences: list[str], chunk_id: str, metadata: dict) -> Response:
    return Response(kind="answer", text=" ".join(sentences), source_url=metadata["url"],
                    last_updated=source_date(metadata), source_chunk_id=chunk_id,
                    scheme=metadata.get("scheme"))


def compose_message(kind: Kind, text: str, page: SchemePage | None, fallback_url: str,
                    corpus_date: str | None) -> Response:
    """Template reply: links the scheme's page when there is one, else the fallback
    (education) link with the corpus data date (edge case GAP-27)."""
    if page:
        return Response(kind=kind, text=text, source_url=page.url, last_updated=page.date,
                        scheme=page.scheme)
    return Response(kind=kind, text=text, source_url=fallback_url, last_updated=corpus_date)


def compose_refusal(education_url: str, corpus_date: str | None,
                    variant: str = "advice") -> Response:
    # The link isn't a corpus page, so the footer shows the corpus data date (GAP-27).
    return Response(kind="refusal", text=templates.REFUSALS.get(variant, templates.REFUSAL),
                    source_url=education_url, last_updated=corpus_date)


def compose_not_found(page: SchemePage | None, fallback_url: str,
                      corpus_date: str | None, name_scheme: bool = True) -> Response:
    """`name_scheme=False` when the page is only the closest match: link it, but don't
    say "for <scheme>" about a scheme the user never asked about."""
    name = page.name if page and name_scheme else None
    return compose_message("not_found", templates.not_found(name), page, fallback_url,
                           corpus_date)


def compose_unavailable(page: SchemePage | None, fallback_url: str,
                        corpus_date: str | None) -> Response:
    return compose_message("unavailable", templates.UNAVAILABLE, page, fallback_url,
                           corpus_date)


def compose_pii_blocked() -> Response:
    return Response(kind="pii_blocked", text=templates.PII_WARNING, source_url=None,
                    last_updated=None)
