FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TOKENIZERS_PARALLELISM=false \
    HF_HOME=/app/.cache/huggingface

# Keep the repo layout (/app/backend, /app/data, /app/frontend):
# config.py finds data/ and frontend/ two folders above backend/app/.
WORKDIR /app/backend

# CPU-only PyTorch first (the default wheel bundles CUDA and is several GB).
COPY backend/requirements.txt .
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch \
 && pip install -r requirements.txt

# Download the embedding model at build time so startup doesn't have to.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

COPY backend/ /app/backend/
COPY data/ /app/data/
COPY frontend/ /app/frontend/

# Render passes the port in $PORT (default 10000). Shell form so $PORT is expanded.
# --forwarded-allow-ips trusts Render's proxy, so the per-user rate limit sees each
# visitor's real IP instead of the proxy's (otherwise all visitors share one limit).
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips="*"
