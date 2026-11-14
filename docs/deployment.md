# Deployment (Fly.io)

> How Fund Facts is built, deployed and kept up to date in production. Related: [data-refresh.md](data-refresh.md) (daily refresh), [implementation-plan.md](implementation-plan.md).

## Architecture

```
GitHub (main) ──push──► Fly Deploy workflow
                          1. CI: lint + 516 tests + Docker image smoke test
                          2. ensure volume rag_data · set Fly secrets · flyctl deploy (1 machine)
                          3. smoke-test https://rag-chatbot-falling-dew-2905.fly.dev
                                   │
                                   ▼
            Fly machine (sin, shared-cpu-1x, 2 GB) ── volume rag_data → /data
              entrypoint: seed registries → first start: build index → uvicorn
              /            web UI (frontend/, no build step)
              /api/*       chat API (threads in SQLite on /data)
              /api/admin/refresh   daily refresh (bearer ADMIN_TOKEN)
                                   ▲
GitHub Daily data refresh (10:00 IST) ── POST /api/admin/refresh, poll until done
```

- **One image** ([Dockerfile](../Dockerfile)): Python 3.12, CPU-only PyTorch, dependencies from `backend/pyproject.toml`, the embedding model baked in (no download at start), code + UI + registries. Runs [deploy/entrypoint.sh](../deploy/entrypoint.sh).
- **One machine, one volume.** The Chroma index, chat database and page snapshots live on the `rag_data` volume at `/data`, so they survive restarts and deploys. Never run more than one machine: each would have its own volume and chats would "disappear" between them. The deploy workflow enforces this (`--ha=false`, `scale count 1`).
- **First start on an empty volume** fetches the 5 Groww pages and builds the index (~1 minute) before the server starts; the health check allows 180 s.
- **Auto stop/start:** the machine stops when idle and starts on the next request (cold start ~15–30 s while the model loads). Set `min_machines_running = 1` in [fly.toml](../fly.toml) for always-on.

## One-time setup

1. **Fly token:** `fly tokens create deploy -a rag-chatbot-falling-dew-2905` (or Fly dashboard → app → Tokens).
2. **GitHub repository secrets** (Settings → Secrets and variables → Actions):

   | Secret | Required | Purpose |
   |---|---|---|
   | `FLY_API_TOKEN` | yes | lets the workflow deploy |
   | `LLM_API_KEY` | yes | Groq API key; stored as a Fly secret, never in the image |
   | `ADMIN_TOKEN` | for the daily refresh | long random string; protects `/api/admin/refresh` |

3. Push to `main` (or Actions → **Fly Deploy** → Run workflow).

## Everyday operations

| Task | How |
|---|---|
| Deploy | Push to `main`. Deploys only if CI (tests + Docker smoke test) passes. |
| Check the live app | `GET /api/health`: `status`, `index_loaded`, `index_version`, `corpus_date`, `stale`, `last_refresh`, Groq budget |
| Refresh data now | Actions → **Daily data refresh** → Run workflow (tick *force* to rebuild even if nothing changed) |
| Logs | `fly logs -a rag-chatbot-falling-dew-2905` (JSON lines; no message text is logged) |
| Shell | `fly ssh console -a rag-chatbot-falling-dew-2905`, then `cd /srv/backend && python -m app.ingest --status` |
| Roll back data | `fly ssh console -a … -C "sh -c 'cd /srv/backend && python -m app.ingest --activate <version>'"`; the app picks it up within 5 minutes |
| Roll back code | Actions → Fly Deploy → re-run an earlier successful run, or `fly releases` + `fly deploy --image <previous image>` |

## Environment

Set in [fly.toml](../fly.toml) `[env]`: `DATA_DIR=/data`, `VECTOR_STORE=chroma`, `PORT=8080`, `LLM_MODEL`, `INDEX_REFRESH_SECONDS`, `CORS_ORIGINS`. Secrets: `LLM_API_KEY`, `ADMIN_TOKEN`. Everything else uses the defaults in `backend/app/config.py` (see `.env.example`).

## Troubleshooting

| Symptom | Check |
|---|---|
| Health check failing after deploy | `fly logs`: on first start the index build takes ~1 minute; look for `entrypoint:` lines |
| `index_loaded: false`, `status: degraded` | initial ingestion failed (e.g. Groww unreachable): run the daily refresh by hand; the app heals itself when it succeeds |
| `llm_configured: false` | `LLM_API_KEY` secret missing: add it in GitHub and redeploy |
| Chats disappear | more than one machine is running: `fly scale count 1` |
| Out of memory | `fly scale memory 2048` (the image runs PyTorch + the embedding model) |
