# Daily Data Refresh

> Operations guide for the scheduled refresh built in Phase 7 ([implementation-plan.md](implementation-plan.md)). The README (Phase 9) links here.

Every day at **10:00 IST** a GitHub Actions workflow ([`.github/workflows/data-refresh.yml`](../.github/workflows/data-refresh.yml)) fetches the 5 Groww pages again and rebuilds the index, so answers and their "Last updated from sources" dates stay current. The running app switches to the new data by itself, without a restart.

## How it works

```
GitHub Actions (10:00 IST)                      Shared ChromaDB              App server
──────────────────────────                      ───────────────              ──────────
fetch 5 pages → normalise → parse
  → quality gates (all 5 pages, required fields,
    sane values, fresh dates)
  → chunk → embed ──────────── stage ─────────► mf_chunks_<new>  (not live)
  → retrieval eval on <new>
  → activate ──────────────────────────────────► "active" = <new> ──► checks every 5 min,
  → prune old versions (keep 3)                                        loads <new>, swaps it in
```

- **Nothing is replaced until the new version passes every check.** If a page can't be fetched, a field is missing, a value looks wrong or the retrieval eval fails, the run stops, yesterday's data stays live, and a GitHub issue ("Daily data refresh failed") is opened or updated.
- **Unchanged pages → no new version.** The run reports "no changes" and stops.
- **No LLM calls.** The refresh doesn't use the Groq budget.
- **Why a shared ChromaDB:** the GitHub runner is a fresh machine that is wiped after every run, so the app and the workflow must use the same ChromaDB server: Chroma Cloud, or a Chroma server you host. Locally (`VECTOR_STORE=chroma`) everything stays in `data/` as before.

## One-time setup

1. **ChromaDB server:** create a Chroma Cloud database, or run a Chroma server reachable from both GitHub and your app server (`chroma run --path /data/chroma --port 8000` behind HTTPS + a token).
2. **GitHub Secrets** (repository → Settings → Secrets and variables → Actions):

   | Chroma Cloud | Self-hosted server |
   |---|---|
   | `CHROMA_API_KEY`, `CHROMA_TENANT`, `CHROMA_DATABASE` | `CHROMA_HOST`, `CHROMA_PORT` (default 443), `CHROMA_SSL` (default true), `CHROMA_AUTH_TOKEN` (optional) |

3. **App server `.env`:** `VECTOR_STORE=chroma_remote` plus the same Chroma settings (see `.env.example`). `INDEX_REFRESH_SECONDS` (default 300) sets how often the app checks for a new version.
4. **First index:** run the workflow once by hand (below), or from a machine with the same settings: `python -m app.ingest` (fetch → build → activate).

## Everyday operations

| Task | How |
|---|---|
| Run the refresh now | GitHub → **Actions** → **Daily data refresh** → **Run workflow** |
| Force a new version even if nothing changed | Run workflow with **force** ticked |
| See what happened | The run's page shows a summary: per-source fetch status, page data dates, chunk changes, **fact changes** (e.g. "expense ratio 0.74% → 0.77%"), quality-gate problems. Snapshots and parsed data are attached as an artifact for 14 days. |
| List versions | `python -m app.ingest --status` (with the Chroma settings in your environment) |
| Roll back | Run workflow with **rollback_to** = an earlier version from `--status` (or `python -m app.ingest --activate <version>`). The app picks it up within `INDEX_REFRESH_SECONDS`. Three versions are kept. |
| Check the live data | `GET /api/health` → `index_version`, `index_built_at`, `corpus_date`, `stale` (true if the data is more than `STALE_AFTER_DAYS` = 2 days old) |

## When a fact changes

Groww updates some values regularly (e.g. monthly expense ratios). The run summary lists every change and flags values that differ from the hand-checked eval data. These differences **don't block** the refresh. Re-check the live Groww page, then update `backend/tests/eval/data/facts_expected.json` so the evals keep matching reality (docs/eval.md §9).

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Issue "Daily data refresh failed", summary says a source `FAILED: HTTP 403` / "bot challenge" | Groww blocking GitHub's data-centre IP addresses | Re-run later; if it persists, run the same workflow on a self-hosted runner (`runs-on: self-hosted`) |
| `FAILED: HTTP 404` | A Groww URL changed | Update `data/sources.yaml` after checking the new page |
| Quality gate: "missing exit_load" / value out of range | Groww changed its page layout | Check the snapshot artifact; adjust `backend/app/ingest/parse.py` |
| Retrieval eval failed | Unusual content change | Check the eval output in the run; the old version stays live |
| `/api/health` shows `stale: true` | No successful run for 2+ days, or the schedule was paused | Check the Actions tab. GitHub disables schedules in public repositories after 60 days without activity; re-enable it there. |
| Run didn't start exactly at 10:00 | GitHub starts scheduled runs late at busy times | Normal; it usually starts within the hour |

## Changing the time

Edit the `cron` line in [`.github/workflows/data-refresh.yml`](../.github/workflows/data-refresh.yml). GitHub uses **UTC**: 10:00 IST = `30 4 * * *`.
