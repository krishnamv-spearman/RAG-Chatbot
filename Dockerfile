# Fund Facts backend: FastAPI API (+ the web UI, also served at / for local use).
# Production: Render web service (render.yaml), vectors in Chroma Cloud
# (VECTOR_STORE=chroma_remote), frontend on Vercel (frontend/vercel.json).
#
# Layout mirrors the repository (/srv/backend, /srv/frontend) because the app locates the
# UI and the eval data relative to its own code. Local runtime data (chat database,
# snapshots, and the Chroma index when VECTOR_STORE=chroma) lives in DATA_DIR=/data.

FROM python:3.12-slim

# OMP/MKL threads and MALLOC_ARENA_MAX keep PyTorch's memory small enough for 512 MB
# instances (one request's embedding at a time doesn't need many threads).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/opt/hf-cache \
    TOKENIZERS_PARALLELISM=false \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    MALLOC_ARENA_MAX=2 \
    ANONYMIZED_TELEMETRY=False \
    DATA_DIR=/data \
    PORT=8080

WORKDIR /srv

# 1) CPU-only PyTorch. The default Linux wheel bundles CUDA (several GB) that this
#    CPU-only app never uses.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch

# 2) Dependencies straight from pyproject.toml (core + ingest + rag): one source of truth.
#    Copied on their own so this layer is cached while only code changes.
COPY backend/pyproject.toml backend/pyproject.toml
RUN python -c "import tomllib; p = tomllib.load(open('backend/pyproject.toml', 'rb'))['project']; o = p['optional-dependencies']; print('\n'.join(p['dependencies'] + o['ingest'] + o['rag']))" > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt

# 3) Bake the embedding model into the image: no download at start-up, works offline.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"
ENV HF_HUB_OFFLINE=1

# 4) Code, UI, registries (seeded into the volume at start) and the start-up script.
COPY backend/ backend/
COPY frontend/ frontend/
COPY data/sources.yaml data/schemes.yaml data/refusal_links.yaml data/facts.json seed/
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /data

WORKDIR /srv/backend
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
