import re

from app import templates
from app.compose import (
    Response,
    SchemePage,
    compose_answer,
    compose_not_found,
    compose_refusal,
    compose_unavailable,
    source_date,
)
from app.validate import split_sentences

URL = "https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth"
AMFI = "https://www.amfiindia.com/investor"
META = {"url": URL, "doc_date": "2026-11-06", "fetched_at": "2026-11-07T10:00:00+00:00"}
PAGE = SchemePage("hdfc-mid-cap", "HDFC Mid Cap Fund", URL, "2026-11-06")


def assert_contract(response: Response):
    """problemStatement §4.2: ≤3 sentences, exactly one link, the footer."""
    rendered = response.render()
    body, _, tail = rendered.partition("\n\nSource: ")
    assert len(split_sentences(body)) <= 3
    assert len(re.findall(r"https?://", rendered)) == 1
    assert re.search(r"\nLast updated from sources: \d{2} [A-Z][a-z]{2} \d{4}$", rendered)
    assert tail.startswith(response.source_url)


def test_answer_render_format():
    r = compose_answer(["The expense ratio is 0.74% (as on 04 Nov 2026)."],
                       "groww-hdfc-mid-cap#expense_ratio", META)
    assert r.render() == ("The expense ratio is 0.74% (as on 04 Nov 2026).\n\n"
                          f"Source: {URL}\nLast updated from sources: 06 Nov 2026")
    assert r.kind == "answer" and r.source_chunk_id == "groww-hdfc-mid-cap#expense_ratio"
    assert_contract(r)


def test_footer_falls_back_to_fetch_date():
    assert source_date({"doc_date": "", "fetched_at": "2026-11-07T10:00:00+00:00"}) == \
        "2026-11-07"
    assert source_date({}) is None


def test_refusal_uses_education_link_and_corpus_date():
    r = compose_refusal(AMFI, "2026-11-06")
    assert r.kind == "refusal" and r.source_url == AMFI and r.text == templates.REFUSAL
    assert r.footer == "Last updated from sources: 06 Nov 2026"
    assert_contract(r)


def test_not_found_links_the_scheme_page():
    r = compose_not_found(PAGE, AMFI, "2026-11-06")
    assert r.source_url == URL and "HDFC Mid Cap Fund" in r.text
    assert_contract(r)


def test_not_found_without_scheme_uses_fallback_link():
    r = compose_not_found(None, AMFI, "2026-11-06")
    assert r.source_url == AMFI and "scheme information I have" in r.text
    assert_contract(r)


def test_unavailable_is_friendly_and_still_has_link_and_footer():
    r = compose_unavailable(PAGE, AMFI, "2026-11-06")
    assert r.kind == "unavailable" and "try again" in r.text
    assert_contract(r)


def test_unknown_date_never_leaves_footer_empty():
    r = compose_refusal(AMFI, None)
    assert r.footer == "Last updated from sources: unknown"
