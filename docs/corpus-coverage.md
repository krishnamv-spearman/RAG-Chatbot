# Corpus Coverage (Phase 1 Output)

> Records what the corpus (the 5 Groww HDFC pages in [`data/sources.yaml`](../data/sources.yaml)) can and can't answer, as required by the Phase 1 exit criteria in [implementation-plan.md](implementation-plan.md). Based on the ingestion run of **07 Nov 2026** (page data dated 06 Nov 2026).

## How the pages are read

Each Groww scheme page embeds its fund data as JSON (`<script id="__NEXT_DATA__">`, key `props.pageProps.mfServerSideData`). The parser reads this structured data instead of the visible text, because it's more reliable. All 5 pages were fetched over plain HTTP; no headless browser was needed. If the embedded data ever disappears (a layout change), the parser falls back to visible text and the coverage report marks every required field as missing (`python -m app.ingest --report` exits with code 1).

## Supported question types (problemStatement §4.2)

| Question type | Covered? | Source field | Notes |
|---|---|---|---|
| Expense ratio | ✅ all 5 | `expense_ratio`, date from `historic_fund_expense[].as_on_date` | e.g. Mid Cap 0.74% (as on 04 Nov 2026) |
| Exit load | ✅ all 5 | `exit_load` | Full condition kept, e.g. "1% if redeemed within 1 year"; Silver ETF FoF: 15 days |
| Minimum SIP amount | ✅ all 5 | `min_sip_investment` | ₹100 for all 5 |
| Riskometer | ✅ all 5 | `return_stats[].risk` | All "Very High". `nfo_risk` is **not** used: it is the level at launch and is out of date ("Moderately High" for Mid Cap) |
| Benchmark index | ✅ all 5 | `benchmark_name` | |
| ELSS lock-in period | ❌ | — | None of the 5 schemes is an ELSS fund, and `lock_in` is empty for all 5. Answer: `not_found` |
| Download statements / capital-gains reports | ❌ | — | Scheme pages don't describe these processes. Answer: `not_found` |

## Extra facts available

Also extracted for every scheme: minimum lump sum and additional investment, fund manager(s) with start dates, category, plan/option (all Direct – Growth), fund house, registrar (CAMS), launch date, ISIN, stamp duty, investment objective, fund size (AUM), NAV and the top 10 holdings (with portfolio date).

NAV and AUM change daily. They're marked `volatile` and left out of the content hash, so a daily NAV change doesn't count as a content change (edge case ING-06).

## Deliberately excluded

| Data on the page | Why excluded |
|---|---|
| Returns (1Y/3Y/5Y…), SIP returns, category returns, Sharpe ratio, beta, standard deviation | Performance data (problemStatement §5: no performance comparisons or return calculations) |
| Groww rating, "pros/cons" analysis | Opinions, not facts |
| Peer comparison | Other AMCs' funds; comparison data |
| `category_info` (incl. tax impact text) | Unreliable: labels HDFC Mid Cap Fund as "Contra". Tax questions are therefore `not_found` |
| AMC contact phone/email | Not needed; avoids handling contact details |
| `lock_in` when all values are empty | Empty ≠ "no lock-in"; the page doesn't state one, so nothing is inferred |

## Groww terms and fetching

- `https://groww.in/robots.txt` (checked 07 Nov 2026) doesn't block `/mutual-funds/<scheme>` pages. It blocks paths such as `/mutual-funds/compare/*`, `/mutual-funds/filter?*` and `/v1/api/*`, none of which are fetched.
- The fetcher sends a descriptive User-Agent, waits ~1 second between requests and only fetches the 5 registered URLs.
- Groww's terms of use haven't been reviewed for automated access. **Add this to the README's known limitations in Phase 9**, along with the two uncovered question types above.

## Refusal links

[`data/refusal_links.yaml`](../data/refusal_links.yaml) holds the AMFI and SEBI education links required in refusal messages (problemStatement §4.3). They are used **only** in refusal templates; they are never fetched, indexed or used to answer questions. Both returned HTTP 200 on 07 Nov 2026.
