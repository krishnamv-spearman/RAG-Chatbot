"""Checks on the real registry files in data/ (Phase 1 exit criteria)."""

from urllib.parse import urlparse

from app.registry import load_refusal_links, load_schemes, load_sources

EXPECTED_URLS = {
    "https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth",
    "https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth",
    "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
    "https://groww.in/mutual-funds/hdfc-silver-etf-fof-direct-growth",
    "https://groww.in/mutual-funds/hdfc-multi-cap-fund-direct-growth",
}


def test_corpus_is_exactly_the_five_groww_pages():
    sources = load_sources()
    assert len(sources) == 5
    assert {s.url for s in sources} == EXPECTED_URLS
    assert all(urlparse(s.url).hostname == "groww.in" for s in sources)


def test_every_source_has_a_scheme_and_back():
    sources, schemes = load_sources(), load_schemes()
    assert {s.scheme for s in sources} == set(schemes)
    by_id = {s.id: s for s in sources}
    for scheme in schemes.values():
        assert by_id[scheme.source_id].scheme == scheme.id


def test_aliases_are_lowercase_and_unique_across_schemes():
    seen = {}
    for scheme in load_schemes().values():
        for alias in scheme.aliases:
            assert alias == alias.lower()
            assert alias not in seen, f"{alias!r} used by {seen.get(alias)} and {scheme.id}"
            seen[alias] = scheme.id


def test_refusal_links_are_amfi_and_sebi_only():
    hosts = {urlparse(link["url"]).hostname for link in load_refusal_links().values()}
    assert hosts == {"www.amfiindia.com", "investor.sebi.gov.in"}
