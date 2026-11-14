#!/bin/sh
# Container start-up: prepare the data volume, build the index on first boot, start the API.
set -eu

DATA_DIR="${DATA_DIR:-/data}"
mkdir -p "$DATA_DIR"

# Registries (sources, schemes, refusal links) are part of the code: always use the
# image's copy so a deploy that changes them takes effect.
cp /srv/seed/sources.yaml /srv/seed/schemes.yaml /srv/seed/refusal_links.yaml "$DATA_DIR/"
[ -f "$DATA_DIR/facts.json" ] || cp /srv/seed/facts.json "$DATA_DIR/"

cd /srv/backend

# Local index only (VECTOR_STORE=chroma): on the first start with an empty DATA_DIR, fetch
# the 5 pages and build the index (about a minute). With VECTOR_STORE=chroma_remote
# (Render + Chroma Cloud) the index lives in Chroma and is built by the daily refresh
# workflow; until then the app runs degraded and heals itself when the index appears.
if [ "${VECTOR_STORE:-chroma}" != "chroma_remote" ] && [ ! -f "$DATA_DIR/index_manifest.json" ]; then
  echo "entrypoint: no index in $DATA_DIR, building it now (first start on this volume)"
  if ! python -m app.ingest; then
    echo "entrypoint: WARNING: initial ingestion failed; starting degraded." \
         "POST /api/admin/refresh (or the daily refresh) will retry."
  fi
fi

# --proxy-headers: behind Render's proxy, take the client IP from X-Forwarded-For so the
# per-client rate limit applies per user, not to everyone at once.
exec python -m uvicorn app.main:app \
  --host 0.0.0.0 --port "${PORT:-8080}" \
  --proxy-headers --forwarded-allow-ips "*" \
  --timeout-keep-alive 30
