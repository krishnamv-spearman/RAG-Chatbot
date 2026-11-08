"""Turns parsed documents into retrievable chunks.

Most sections from the parser are one short fact each, so they become one chunk each.
Long sections (e.g. investment objective) are split into ~350-token windows with
~50 tokens of overlap. Word counts approximate tokens (1 token ≈ 0.75 words).

Chunk IDs are `<source_id>#<field>` (plus `-<n>` for split sections). Using the field
name instead of a position keeps IDs stable when other fields appear or disappear.
"""

import hashlib
import re
from dataclasses import dataclass

MAX_WORDS = 260  # ≈ 350 tokens
OVERLAP_WORDS = 38  # ≈ 50 tokens


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str  # what is embedded / keyword-indexed / shown to the LLM
    metadata: dict  # flat str/int/float/bool values only (Chroma requirement)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "section"


def _windows(words: list[str]) -> list[list[str]]:
    if len(words) <= MAX_WORDS:
        return [words]
    step = MAX_WORDS - OVERLAP_WORDS
    windows = []
    for start in range(0, len(words), step):
        windows.append(words[start:start + MAX_WORDS])
        if start + MAX_WORDS >= len(words):
            break
    return windows


def chunk_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_document(doc: dict) -> list[Chunk]:
    chunks = []
    for section in doc["sections"]:
        key = section.get("field") or _slug(section["heading"])
        windows = _windows(section["text"].split())
        for i, words in enumerate(windows):
            body = " ".join(words)
            if i > 0:
                # Later windows lose the opening sentence that names the scheme; restore it.
                body = f"{doc['scheme_name']}, {section['heading']} (continued): {body}"
            text = f"{section['heading']}: {body}"
            chunk_id = f"{doc['source_id']}#{key}" + (f"-{i + 1}" if len(windows) > 1 else "")
            chunks.append(Chunk(
                chunk_id=chunk_id,
                text=text,
                metadata={
                    "source_id": doc["source_id"],
                    "url": doc["url"],
                    "scheme": doc["scheme"],
                    "scheme_name": doc["scheme_name"],
                    "source_type": doc["source_type"],
                    "section": section["heading"],
                    "field": section.get("field") or "",
                    "as_on": section.get("as_on") or "",
                    "doc_date": doc.get("doc_date") or "",
                    "fetched_at": doc.get("fetched_at") or "",
                    "volatile": bool(section.get("volatile")),
                    "chunk_hash": chunk_text_hash(text),
                },
            ))
    return chunks
