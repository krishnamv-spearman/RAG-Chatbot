# Deployment: Vercel (frontend) + Render (backend) + Chroma Cloud (vectors)

> How Fund Facts runs in production, how to set it up, and how to verify and troubleshoot it. Related: [data-refresh.md](data-refresh.md) (daily refresh details), [implementation-plan.md](implementation-plan.md).

## Architecture

```
 Browser
   │  https://<project>.vercel.app                 (static UI)
   ▼
 Vercel ── frontend/ ── build.sh writes js/config.js { apiBase: API_BASE_URL }
   │
   │  fetch https://fund-facts-api.onrender.com/api/*   (cross-origin, CORS)
   ▼
 Render (free web service, Docker) ── Dockerfile → deploy/entrypoint.sh → uvicorn app.main:app
   │   reads vectors ──────────────────────────────────┐
   │   chat threads: SQLite in /data (NOT persistent)  │
   │   checks for a new index version every 5 min      │
   ▼                                                   ▼
 Groq (LLM answers)                          Chroma Cloud (index versions + catalog)
                                                       ▲
 GitHub Actions ── CI (tests, Vercel build, Docker)    │ writes new versions
              └── Daily data refresh, 10:00 IST ───────┘ (fetch → gates → stage → eval → activate)
```

| Part | Where | Repository files |
|---|---|---|
| Frontend (static HTML/CSS/JS, no npm) | **Vercel**, Root Directory `frontend` | `frontend/` (`index.html`, `css/`, `js/`), `frontend/build.sh`, `frontend/vercel.json` |
| Backend (FastAPI, Docker) | **Render** web service, plan **Free**, region Singapore | `Dockerfile`, `deploy/entrypoint.sh`, `render.yaml`, `backend/` |
| Vector index | **Chroma Cloud** (`VECTOR_STORE=chroma_remote`) | `backend/app/vectorstore.py`, `backend/app/ingest/catalog.py` |
| Daily data refresh | **GitHub Actions** ("chroma" mode) | `.github/workflows/data-refresh.yml` |
| Checks | **GitHub Actions** | `.github/workflows/ci.yml` |

**Accepted free-tier limits:**
- The Render instance has 512 MB RAM and sleeps after ~15 minutes without traffic. The first request after that takes up to ~1 minute (model load), and the UI shows "Thinking…" meanwhile.
- **Chat threads are not persistent.** They are stored in SQLite on the instance's temporary disk and reset on every restart, sleep or deploy. The UI then quietly starts a fresh chat; the facts (Chroma Cloud) are unaffected.
- To make chats persistent later: a paid Render instance with a persistent disk mounted at `/data` (no code change), or a hosted database (code change).

## Environment variables

### Render (backend): set in the Render dashboard; `render.yaml` declares them

| Variable | Value | Notes |
|---|---|---|
| `VECTOR_STORE` | `chroma_remote` | set by `render.yaml` |
| `CHROMA_API_KEY` | Chroma Cloud API key | secret |
| `CHROMA_TENANT` | Chroma Cloud tenant ID | |
| `CHROMA_DATABASE` | Chroma Cloud database name | |
| `LLM_API_KEY` | Groq API key | secret |
| `LLM_MODEL` | `openai/gpt-oss-120b` | set by `render.yaml` |
| `CORS_ORIGINS` | Vercel production URL, e.g. `https://fund-facts.vercel.app` | comma-separate several; no trailing slash |
| `CORS_ORIGIN_REGEX` | optional, for Vercel previews, e.g. `^https://fund-facts-[a-z0-9-]+-<team>\.vercel\.app$` | leave empty to allow production only |
| `INDEX_REFRESH_SECONDS` | `300` | how often the backend checks Chroma Cloud for a new version |
| `STALE_AFTER_DAYS` | `2` | `/api/health` → `stale: true` after this |
| `PORT` | set by Render | the image listens on `$PORT` |

Optional: `API_MESSAGES_PER_MINUTE` (20), Groq limits (`LLM_REQUESTS_PER_MINUTE` etc.; see `.env.example`). Not used in this setup: `ADMIN_TOKEN` (the in-app refresh is for a local index on a persistent disk).

### Vercel (frontend)

| Variable | Value | Environments |
|---|---|---|
| `API_BASE_URL` | the Render URL, e.g. `https://fund-facts-api.onrender.com` (https, no trailing slash) | Production and Preview |

The build fails on Vercel if it is missing or not `https://` (so the UI can never silently call itself).

### GitHub (Settings → Secrets and variables → Actions): daily refresh

| Secret | Value |
|---|---|
| `CHROMA_API_KEY`, `CHROMA_TENANT`, `CHROMA_DATABASE` | the same Chroma Cloud values as on Render |

No Groq key is needed: the refresh makes no LLM calls.

## Setup (one time)

1. **Chroma Cloud:** create a database; note the tenant ID, database name and an API key.
2. **GitHub secrets:** add the three `CHROMA_*` secrets (above).
3. **First index:** GitHub → Actions → **Daily data refresh** → **Run workflow**. Check the run summary: 5 sources fetched, new version staged, retrieval eval passed, version activated.
4. **Render:** Dashboard → **New → Blueprint** → select this repository (`render.yaml` is found automatically) → fill in the `sync: false` variables (`CHROMA_*`, `LLM_API_KEY`, `CORS_ORIGINS`; leave `CORS_ORIGIN_REGEX` empty for now) → **Apply**. Note the service URL (e.g. `https://fund-facts-api.onrender.com`).
5. **Vercel:** **Add New → Project** → import the repository → **Root Directory: `frontend`** (Framework Preset "Other"; `vercel.json` supplies the build command and output directory) → Environment Variable `API_BASE_URL` = the Render URL → **Deploy**. Note the production URL.
6. **CORS:** in Render, set `CORS_ORIGINS` to the Vercel production URL (and optionally `CORS_ORIGIN_REGEX` for previews) → the service redeploys.
7. **Verify** (next section).

Deploys afterwards: push to `main` → Vercel deploys the frontend; Render deploys the backend after the GitHub checks (CI) pass (`autoDeployTrigger: checksPass`).

## Verification

| Check | How | Expected |
|---|---|---|
| Backend up | `curl https://<render-url>/api/health` | `"status": "ok"`, `"index_loaded": true`, `"vector_store": "chroma_remote"`, `"llm_configured": true`, `"stale": false` |
| Data version | same response | `index_version` = the version from the refresh run summary; `corpus_date` recent |
| CORS | `curl -i -X OPTIONS https://<render-url>/api/threads -H "Origin: https://<vercel-url>" -H "Access-Control-Request-Method: POST"` | `access-control-allow-origin: https://<vercel-url>` |
| Frontend config | open `https://<vercel-url>/js/config.js` | `apiBase: "https://<render-url>"` |
| End to end | open the Vercel URL; ask "What is the expense ratio of HDFC Mid Cap Fund?" | an answer with a Groww source link and "Last updated from sources" |
| Refusal (no LLM) | ask "Should I invest in HDFC Mid Cap?" | "Can't advise" reply |
| Daily refresh | Actions → Daily data refresh → latest run | green; summary lists sources, fact changes, the activated version |

Automated (every push, `.github/workflows/ci.yml`):
- **Lint and tests:** ruff + the full pytest suite.
- **Frontend build (Vercel):** `build.sh` as Vercel runs it; it must fail without `API_BASE_URL`.
- **Docker image smoke tests:**
  - *Local mode:* builds the index on first start, serves the UI and API, and keeps chats across a restart.
  - *Render mode:* a remote Chroma server and the API capped at 512 MB. The API starts degraded, the daily refresh fills Chroma, the API heals itself and answers, CORS works for the Vercel origin, and nothing is out-of-memory killed. Memory use is reported as a run annotation.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| UI shows "Service unreachable" | `API_BASE_URL` wrong, or the backend is asleep or failing | Open `<vercel-url>/js/config.js` and check the URL; open `<render-url>/api/health` (wait ~1 min on a cold start) |
| Browser console: "blocked by CORS policy" | Vercel URL not in `CORS_ORIGINS` (or a preview URL not matching `CORS_ORIGIN_REGEX`) | Add the exact origin (scheme + host, no trailing slash) in Render → Environment; redeploy |
| `/api/health`: `"status": "degraded"`, `index_error: "no index found"` | Chroma Cloud has no live version yet | Run **Daily data refresh** by hand; the backend heals itself within `INDEX_REFRESH_SECONDS` |
| `index_error` mentions Chroma or auth | wrong `CHROMA_*` values on Render | Check the tenant, database and API key in Render → Environment |
| `"llm_configured": false`, factual questions say "temporarily unavailable" | `LLM_API_KEY` missing on Render | Set it in Render → Environment |
| "temporarily unavailable" while `llm_configured` is true | Groq rate budget used up (≈9 factual answers/min, 200k tokens/day) | Wait; see `llm_budget` in `/api/health` |
| Render logs: "Out of memory" / instance restarts | 512 MB exceeded | Check the CI "Render-mode memory" annotation; upgrade the instance size (paid) |
| Chats vanished | Free instance restarted or slept (expected, see limits) | Nothing to fix on Free; a persistent disk on a paid plan keeps them |
| Daily refresh fails at "Choose the refresh mode" | `CHROMA_*` GitHub secrets missing | Add them (Setup step 2) |
| Daily refresh fails at a fetch | Groww blocked the runner or a URL changed | See [data-refresh.md](data-refresh.md); the live version stays in place |
| Vercel build error "API_BASE_URL is not set" | Variable missing for that environment | Vercel → Settings → Environment Variables → add it for Production and Preview |
| Render deploy didn't start after a push | CI failed (`checksPass`) | Fix the failing GitHub check, or deploy by hand in Render |

## Local development (unchanged)

```powershell
cd backend
.\.venv\Scripts\python -m app.ingest          # first time: build the local index (VECTOR_STORE=chroma)
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

Open http://127.0.0.1:8000: the backend also serves the UI, with `frontend/js/config.js` set to the same origin. To try the Vercel-style split locally, run `API_BASE_URL=http://127.0.0.1:8000 sh build.sh` in `frontend/`, serve `frontend/dist` on another port, and add that origin to `CORS_ORIGINS`.

## Retired: Fly.io

The Fly.io configuration (`fly.toml`, `.github/workflows/fly-deploy.yml`) was removed when moving to Vercel + Render. The Fly app `rag-chatbot-falling-dew-2905` may still exist in your Fly account; delete it there if it's no longer needed.
