# Stitch Prompt: Dark Theme UI

Paste the **main prompt** into Google Stitch first. Then use the **follow-up prompts** one at a time to refine screens. All copy below is the real text the backend returns (`app/templates.py`), so the design matches what Phase 6 will build.

---

## Main prompt

```
Design a dark-theme web app called "Fund Facts" — a facts-only chat assistant that answers
questions about five HDFC mutual fund schemes (Mid Cap, Small Cap, Large Cap, Multi Cap,
Silver ETF FoF). It never gives investment advice. Tone: calm, trustworthy, precise —
like a well-designed banking or research tool, not a playful chatbot. Do not use any real
company logos or brand colours.

LAYOUT (desktop, 1440px):
- Left sidebar (280px): app name "Fund Facts" with a small neutral icon, a primary
  "+ New chat" button, then a list of chat threads (title = first question, truncated
  with ellipsis, relative time below, delete icon on hover). The active thread is
  highlighted.
- Main area: a slim sticky top banner that is ALWAYS visible:
  "⚠ Facts-only. No investment advice." — subtle amber/warning tint, small text, not alarming.
- Chat pane: centered message column (max ~760px wide).
- Bottom: input bar with placeholder "Ask a factual question about HDFC mutual funds…",
  a send button, and a small hint below it: "Please don't share PAN, Aadhaar, account
  numbers, OTPs, email or phone numbers." Show a character counter "0/500".

EMPTY THREAD (welcome state):
- Greeting: "Hi, I answer factual questions about five HDFC Mutual Fund schemes
  (HDFC Mid Cap, Small Cap, Large Cap, Multi Cap and Silver ETF FoF) using their Groww pages."
- "Try asking:" followed by three clickable suggestion chips:
  1. "What is the expense ratio of HDFC Mid Cap Fund?"
  2. "What is the exit load of HDFC Small Cap Fund?"
  3. "What is the benchmark of HDFC Large Cap Fund?"
- Below, five small scheme pills (Mid Cap · Small Cap · Large Cap · Multi Cap · Silver ETF FoF).

MESSAGES:
- User message: right-aligned bubble, muted accent background.
- Assistant answer: left-aligned card (not a bubble), containing:
  1. The answer text (max 3 sentences), e.g. "The expense ratio of HDFC Mid Cap Fund
     Direct Growth is 0.74% (as on 04 Nov 2026)."
  2. A source row: link icon + "Source: groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth"
     (opens in new tab, truncated with ellipsis on small screens).
  3. A muted footer line: "Last updated from sources: 06 Nov 2026".
  Every assistant card has exactly ONE source link and this footer.
- Show a "Thinking…" state: assistant card skeleton with three animated dots.

DISTINCT ASSISTANT REPLY STYLES (same card shape, different left border colour + small
label chip at the top; never rely on colour alone — always include the label text and icon):
- Answer — default, no label (accent border).
- "Can't advise" (refusal) — info blue border, shield icon. Text: "I can only share factual
  information about these HDFC mutual fund schemes, so I can't give investment advice,
  recommendations or comparisons. For help with choosing funds, see the investor education
  resources linked below, or speak to a SEBI-registered investment adviser."
  Link: amfiindia.com/investor
- "Returns not shown" (performance) — neutral border, chart icon. Text: "I can't share,
  compare or calculate returns. You can see the latest performance figures for HDFC Small
  Cap Fund on its Groww page linked below."
- "Need a detail" (clarify) — violet border, question icon, with the five scheme names as
  quick-reply chips under the text.
- "Not found" — grey border, search icon.
- "Out of scope" — grey border.
- "Personal info blocked" (PII warning) — amber border, lock icon, NO source link and NO
  footer: "For your security, please don't share personal details like PAN, Aadhaar,
  account or folio numbers, OTPs, email addresses or phone numbers. I can only answer
  general factual questions about mutual fund schemes."
- "Temporarily unavailable" — muted red border, retry button: "I can't answer right now
  because the answering service is temporarily unavailable. Please try again in a moment."

COLOUR & TYPE (dark theme):
- Background #0B0F14, sidebar #0F141A, cards/surfaces #151B23, raised #1C2430,
  borders #263140.
- Text primary #E6EDF3, secondary #9AA7B4, muted #6B7785.
- Accent: calm teal #2DD4BF (buttons, links, focus rings); accent text on dark ≥ 4.5:1.
- Status: info #60A5FA, warning/amber #F5B544, violet #A78BFA, error #F87171.
- Font: Inter (or similar), 15–16px body, 1.55 line-height; numbers in tabular figures.
- Radius 12px cards, 999px chips; soft 1px borders instead of heavy shadows.

ACCESSIBILITY: WCAG AA contrast, visible focus rings, keyboard-friendly chips and buttons,
44px minimum touch targets, no information conveyed by colour alone.

Generate: (1) desktop welcome screen (empty thread), (2) desktop conversation showing a user
question, an answer card, a refusal card and a "Thinking…" state, (3) mobile 375px version
of the conversation.
```

---

## Follow-up prompts

**1. Mobile layout**

```
Create the mobile (375px) version. The sidebar becomes a slide-in drawer opened from a
hamburger icon in a top bar that shows "Fund Facts" and a "+" new-chat icon. The
"Facts-only. No investment advice." banner stays sticky under the top bar. Suggestion chips
wrap to multiple lines. Long source URLs truncate with an ellipsis; nothing scrolls
horizontally. Input bar is fixed to the bottom with safe-area padding.
```

**2. All reply states on one screen**

```
Create a single "reply states" reference screen in the same dark theme showing one card for
each assistant reply type stacked vertically: Answer, Can't advise (refusal), Returns not
shown (performance), Need a detail (clarify, with scheme quick-reply chips), Not found,
Out of scope, Personal info blocked (no link, no footer), Temporarily unavailable (with a
Retry button), and the Thinking… loading state. Label each card on the side for developers.
```

**3. Sidebar and thread management**

```
Refine the sidebar: show 6 example threads with titles like "What is the expense ratio of
HDFC Mid…", "Exit load of Small Cap", "Who manages HDFC Large Cap?", with relative times
("2 min ago", "Yesterday"). Show hover state with a delete (trash) icon, an active state, a
"Delete this chat?" confirmation popover, and an empty state: "No chats yet — start one with
New chat." Keep the "+ New chat" button prominent at the top.
```

---

## Notes for implementation (Phase 6)

- Reply type → `message.kind` from the API: `answer`, `refusal`, `performance`, `clarify`, `not_found`, `out_of_scope`, `greeting`, `pii_blocked`, `unavailable`.
- Source link → `message.source_url`; footer → `message.footer` (both `null` for `pii_blocked`).
- Welcome text, example questions and disclaimer come from `GET /api/meta`, so they don't need to be hard-coded.
- Character limit → `meta.max_message_chars` (500).
