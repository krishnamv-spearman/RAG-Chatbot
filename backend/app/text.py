"""Keyword tokenisation and query synonym expansion, shared by indexing and retrieval."""

import re

STOP_WORDS = frozenset("""
a an the of for to in on at by with and or is are was were be been it its this that these those
what which who whom whose how when where why do does did can could would should will shall may
i me my we our you your he she they them their please tell about from as into than then there
""".split())

# Query-side expansion (edge case RET-09). Keys are matched as whole phrases, lowercase.
SYNONYMS = {
    "ter": "expense ratio",
    "total expense ratio": "expense ratio",
    "expense": "expense ratio",
    "fees": "expense ratio",
    "risk level": "riskometer",
    "risk category": "riskometer",
    "risk": "riskometer",
    "riskometer": "riskometer risk",
    "lumpsum": "lump sum",
    "one time": "lump sum",
    "one-time": "lump sum",
    "sip": "sip systematic",
    "fund size": "aum fund size",
    "assets under management": "aum fund size",
    "aum": "aum fund size",
    "manager": "fund manager managed",
    "managers": "fund manager managed",
    "managed": "fund manager managed",
    "manages": "fund manager managed",
    "manage": "fund manager managed",
    "managing": "fund manager managed",
    "index": "benchmark index",
    "benchmark": "benchmark index",
    "exit fee": "exit load",
    "redemption charge": "exit load",
    "rta": "registrar transfer agent",
    "objective": "investment objective",
    "portfolio": "holdings portfolio",
    "stocks": "holdings",
    "lock in": "lock-in lock",
    "lockin": "lock-in lock",
}

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?%?")


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOP_WORDS]


def expand_query(query: str) -> str:
    """Append synonym expansions for phrases found in the query."""
    lowered = f" {query.lower()} "
    extra = [
        expansion
        for phrase, expansion in SYNONYMS.items()
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", lowered)
    ]
    return query if not extra else f"{query} {' '.join(extra)}"
