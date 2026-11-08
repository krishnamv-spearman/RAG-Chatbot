# Problem Statement: Mutual Fund FAQ Assistant (Facts-Only Q&A)

> Source of truth: [ProblemStatement.txt](ProblemStatement.txt). This file restates it in structured form as project context.

## 1. Overview

Build a **facts-only FAQ assistant** for mutual fund schemes, using **Groww** as the reference product context. The assistant answers objective, verifiable questions about mutual funds by retrieving information from a curated corpus of official public sources (AMC websites, AMFI, SEBI).

It must **never** give investment advice, opinions, or recommendations. Every answer carries exactly one source link and a "last updated" footer.

**Guiding principle:** accuracy over intelligence. Being trustworthy, transparent and compliant matters more than being clever.

## 2. Objective

Design and implement a lightweight **Retrieval-Augmented Generation (RAG)** assistant that:

- Answers factual queries about mutual fund schemes
- Uses a curated corpus of official documents
- Gives concise, source-backed responses

## 3. Target Users

- Retail investors comparing mutual fund schemes
- Customer support and content teams handling repetitive mutual fund queries

## 4. Scope of Work

### 4.1 Corpus Definition

- **AMC:** HDFC Mutual Fund (one AMC only)
- **Schemes (Groww pages):**

| Scheme | URL |
|---|---|
| HDFC Mid Cap Fund – Direct Growth | https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth |
| HDFC Small Cap Fund – Direct Growth | https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth |
| HDFC Large Cap Fund – Direct Growth | https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth |
| HDFC Silver ETF FoF – Direct Growth | https://groww.in/mutual-funds/hdfc-silver-etf-fof-direct-growth |
| HDFC Multi Cap Fund – Direct Growth | https://groww.in/mutual-funds/hdfc-multi-cap-fund-direct-growth |

**Source URLs (raw list for ingestion):**

```
https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth
https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth
https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth
https://groww.in/mutual-funds/hdfc-silver-etf-fof-direct-growth
https://groww.in/mutual-funds/hdfc-multi-cap-fund-direct-growth
```

### 4.2 FAQ Assistant Requirements

**Supported factual questions** (examples):

- Expense ratio of a scheme
- Exit load details
- Minimum SIP amount
- ELSS lock-in period
- Riskometer classification
- Benchmark index
- How to download statements or capital-gains reports

**Every response must:**

- Be **at most 3 sentences**
- Include **exactly one** citation link
- End with the footer: `Last updated from sources: <date>`

### 4.3 Refusal Handling

The assistant must refuse non-factual or advisory questions, for example:

- "Should I invest in this fund?"
- "Which fund is better?"

**Refusals must:**

- Be polite and clearly worded
- Restate that the assistant is facts-only
- Include a relevant educational link (e.g. an AMFI or SEBI investor-education page)

### 4.4 User Interface (Minimal)

- A welcome message
- Three example questions
- A visible disclaimer: **"Facts-only. No investment advice."**

### 4.5 Multiple Chat Threads

- Support multiple independent conversations/threads at the same time, each with its own history.

## 5. Constraints

### Data and Sources
- Use only official public sources (AMC, AMFI, SEBI).
- Do not use third-party blogs or aggregator websites.

### Privacy and Security
Do **not** collect, store, or process:
- PAN or Aadhaar numbers
- Account numbers
- OTPs
- Email addresses or phone numbers

### Content Restrictions
- No investment advice or recommendations.
- No performance comparisons or return calculations.
- For performance questions, return only a link to the official factsheet.

### Transparency
- Responses are short, factual and verifiable.
- Every answer includes a source link and a last-updated date.

## 6. Expected Deliverables

1. **README** covering:
   - Setup instructions
   - Selected AMC and schemes
   - Architecture overview (RAG approach)
   - Known limitations
2. **Disclaimer snippet:** "Facts-only. No investment advice."
3. **RAG chatbot with multiple chat-thread support**

## 7. Success Criteria

- Accurate retrieval of factual mutual fund information
- Strict adherence to facts-only responses
- Consistent inclusion of valid source citations
- Correct refusal of advisory queries
- Clean, minimal, user-friendly interface

## 8. Open Questions

- **Groww vs. "official sources only":** the corpus URLs are Groww pages, but the constraints rule out aggregator sites. Decide whether Groww pages are used as-is (as the reference product context), or only to identify schemes, with facts and citations taken from HDFC AMC documents (SID/KIM/factsheets), AMFI and SEBI.
- **ELSS lock-in:** none of the five listed schemes is an ELSS fund. Decide whether to add an HDFC ELSS scheme or answer this as a general fact from AMFI/SEBI.
- **"Last updated" date:** define whether it is the scrape/ingest date or the date printed on the source document.
