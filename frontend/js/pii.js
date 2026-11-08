// Client-side PII pre-check: warns BEFORE sending. The server runs the authoritative check
// (backend/app/guards/pii.py) on every message regardless; these patterns mirror it.

const START = String.raw`(?<![\d,])(?<!\d\.)`;
const END = String.raw`(?![\d,])(?!\.\d)`;

const PATTERNS = [
  ["PAN", /(?<![A-Za-z0-9])[A-Za-z]{5}\s?\d{4}\s?[A-Za-z](?![A-Za-z0-9])/],
  ["email address", /[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}/],
  ["phone number", new RegExp(`${START}(?:\\+?91[\\s-]?|0)?[6-9](?:[\\s-]?\\d){9}${END}`)],
  ["OTP", /(?:otp|one[\s-]?time[\s-]?(?:password|pin|code)|verification\s+code|security\s+code)\D{0,25}\d{4,8}(?!\d)|(?<!\d)\d{4,8}(?!\d)\D{0,15}(?:otp|verification\s+code)/i],
  ["folio or account number", /\bfolio\b\D{0,15}\d{6,18}(?!\d)|\b(?:account|a\/c|acct|demat)\b\D{0,15}\d{9,18}(?!\d)/i],
];

const AADHAAR = new RegExp(`${START}([2-9]\\d{3})[\\s-]?(\\d{4})[\\s-]?(\\d{4})${END}`, "g");

// Verhoeff checksum (Aadhaar), so 12-digit amounts aren't flagged.
const D = [
  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
  [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
  [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
  [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
];
const P = [
  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
  [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
  [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
];
function verhoeffValid(num) {
  let c = 0;
  [...num].reverse().forEach((d, i) => { c = D[c][P[i % 8][Number(d)]]; });
  return c === 0;
}

/** Returns the kinds of personal data found, e.g. ["PAN", "phone number"]. */
export function findPII(text) {
  const found = PATTERNS.filter(([, re]) => re.test(text)).map(([name]) => name);
  for (const m of text.matchAll(AADHAAR)) {
    if (verhoeffValid(m[1] + m[2] + m[3])) { found.push("Aadhaar number"); break; }
  }
  return found;
}
