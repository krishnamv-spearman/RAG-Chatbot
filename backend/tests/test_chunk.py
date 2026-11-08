from app.ingest.chunk import MAX_WORDS, OVERLAP_WORDS, chunk_document
from app.ingest.parse import parse_html
from tests.conftest import make_page


def _doc(mid_cap_data, mid_cap_source):
    meta = {"fetched_at": "2026-11-07T10:00:00+00:00"}
    return parse_html(make_page(mid_cap_data), mid_cap_source, meta, "HDFC Mid Cap Fund")


def test_one_chunk_per_short_section_with_field_ids(mid_cap_data, mid_cap_source):
    doc = _doc(mid_cap_data, mid_cap_source)
    chunks = chunk_document(doc)
    assert len(chunks) == len(doc["sections"])
    ids = {c.chunk_id for c in chunks}
    assert "groww-hdfc-mid-cap#expense_ratio" in ids
    assert "groww-hdfc-mid-cap#exit_load" in ids


def test_chunk_text_includes_heading_and_scheme(mid_cap_data, mid_cap_source):
    chunk = next(c for c in chunk_document(_doc(mid_cap_data, mid_cap_source))
                 if c.chunk_id.endswith("#exit_load"))
    assert chunk.text.startswith("Exit load: ")
    assert "HDFC Mid Cap Fund" in chunk.text
    assert "1% if redeemed within 1 year" in chunk.text


def test_metadata_is_flat_and_never_none(mid_cap_data, mid_cap_source):
    for chunk in chunk_document(_doc(mid_cap_data, mid_cap_source)):
        for key, value in chunk.metadata.items():
            assert isinstance(value, str | int | float | bool), key
        assert chunk.metadata["url"].startswith("https://groww.in/")
        assert len(chunk.metadata["chunk_hash"]) == 64


def test_long_section_is_split_with_overlap_and_context(mid_cap_data, mid_cap_source):
    mid_cap_data["description"] = " ".join(f"w{i}" for i in range(600))
    chunks = [c for c in chunk_document(_doc(mid_cap_data, mid_cap_source))
              if "#objective" in c.chunk_id]
    assert [c.chunk_id.rsplit("#", 1)[1] for c in chunks] == ["objective-1", "objective-2",
                                                              "objective-3"]
    # Continuation windows restate the scheme so each chunk stands alone.
    assert all("HDFC Mid Cap Fund" in c.text for c in chunks)
    first, second = chunks[0].text.split(), chunks[1].text.split()
    assert first[-OVERLAP_WORDS:] == second[-(MAX_WORDS):][:OVERLAP_WORDS]


def test_chunk_ids_stable_when_another_field_disappears(mid_cap_data, mid_cap_source):
    before = {c.chunk_id: c.text for c in chunk_document(_doc(mid_cap_data, mid_cap_source))}
    del mid_cap_data["stamp_duty"]
    after = {c.chunk_id: c.text for c in chunk_document(_doc(mid_cap_data, mid_cap_source))}
    assert set(before) - set(after) == {"groww-hdfc-mid-cap#stamp_duty"}
    assert all(before[k] == v for k, v in after.items())
