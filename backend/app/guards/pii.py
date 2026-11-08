"""Detects personal data the assistant must never collect, store or process
(problemStatement §5): PAN, Aadhaar, phone, email, OTP, account / folio numbers.

Runs FIRST on every message, before anything is stored, logged, embedded or sent to the
LLM (architecture §6.1). Returns only the TYPES found, never the matched values, so
callers can't accidentally log them.
"""

import re
from enum import StrEnum


class PIIType(StrEnum):
    PAN = "pan"
    AADHAAR = "aadhaar"
    PHONE = "phone"
    EMAIL = "email"
    OTP = "otp"
    ACCOUNT = "account"


# PAN: 5 letters, 4 digits, 1 letter. Lowercase and spaces allowed (edge case PII-02).
_PAN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{5}\s?\d{4}\s?[A-Za-z](?![A-Za-z0-9])")

# Aadhaar: 12 digits (first 2-9), optionally grouped 4-4-4; confirmed with Verhoeff.
# Boundaries: no digit (or "1,"/"1." number continuation) on either side, but a sentence-
# ending full stop is fine ("...1096.").
_START = r"(?<![\d,])(?<!\d\.)"
_END = r"(?![\d,])(?!\.\d)"
_AADHAAR = re.compile(rf"{_START}([2-9]\d{{3}})[\s-]?(\d{{4}})[\s-]?(\d{{4}}){_END}")

# Indian mobile: optional +91 / 91 / 0, then 10 digits starting 6-9, with an optional
# space or hyphen between any digits ("98765 43210", "8123 456 789", "7 0 1 2 ...").
_PHONE = re.compile(rf"{_START}(?:\+?91[\s-]?|0)?[6-9](?:[\s-]?\d){{9}}{_END}")

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")

# OTP: a 4-8 digit number near an OTP-type keyword, in either order. Bare "code" is not a
# keyword: scheme codes like "118989" are public facts. A bare "pin" only counts with a
# 4-digit number: "pin 560001" / "pin code" / "pincode" are postal codes (edge case PII-10).
_OTP_WORD = (r"(?:otp|one[\s-]?time[\s-]?(?:password|pin|code)|verification\s+code|"
             r"security\s+code|auth(?:entication)?\s+code)")
_OTP = re.compile(
    rf"{_OTP_WORD}\D{{0,25}}(?<!\d)\d{{4,8}}(?!\d)|(?<!\d)\d{{4,8}}(?!\d)\D{{0,15}}{_OTP_WORD}"
    r"|\bpin\b(?!\s*-?\s*code)\D{0,12}(?<!\d)\d{4}(?!\d)",
    re.IGNORECASE)

# Folio numbers are often 6-12 digits; bank / demat account numbers 9-18.
_ACCOUNT = re.compile(
    r"\bfolio\b(?:\s*(?:no\.?|number|num|#|:))?\D{0,12}(?<!\d)\d{6,18}(?!\d)"
    r"|\b(?:account|a/c|acct|demat)\b(?:\s*(?:no\.?|number|num|#|:))?\D{0,12}"
    r"(?<!\d)\d{9,18}(?!\d)",
    re.IGNORECASE)

# Verhoeff checksum tables (used by UIDAI for Aadhaar).
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_valid(number: str) -> bool:
    check = 0
    for i, digit in enumerate(reversed(number)):
        check = _D[check][_P[i % 8][int(digit)]]
    return check == 0


def detect(text: str) -> list[PIIType]:
    """Types of personal data found in `text` (empty list = clean)."""
    if not text:
        return []
    found: set[PIIType] = set()
    if _EMAIL.search(text):
        found.add(PIIType.EMAIL)
    if _PAN.search(text):
        found.add(PIIType.PAN)
    if any(verhoeff_valid("".join(m.groups())) for m in _AADHAAR.finditer(text)):
        found.add(PIIType.AADHAAR)
    if _PHONE.search(text):
        found.add(PIIType.PHONE)
    if _OTP.search(text):
        found.add(PIIType.OTP)
    if _ACCOUNT.search(text):
        found.add(PIIType.ACCOUNT)
    return sorted(found)
