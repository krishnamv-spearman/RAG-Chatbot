"""Application settings, loaded from environment variables and the project-root .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # the shared .env may hold variables for other tools
    )

    # LLM (Groq)
    llm_api_key: SecretStr | None = None
    llm_model: str = "openai/gpt-oss-120b"
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2  # SDK-level retries for 429 / 5xx / connection errors
    # Reasoning models spend tokens thinking before the JSON; leave room for both.
    llm_max_completion_tokens: int = 1024
    # For reasoning models (gpt-oss): "low" keeps latency down for short factual answers.
    # Empty string = don't send the parameter (for models that don't support it).
    llm_reasoning_effort: str = "low"
    # "json_schema" (strict, preferred) or "json_object" (schema described in the prompt).
    llm_response_format: str = "json_schema"
    # Groq account limits for the model (shared by all LLM calls in the process).
    llm_requests_per_minute: int = 30
    llm_tokens_per_minute: int = 8000
    llm_requests_per_day: int = 1000
    llm_tokens_per_day: int = 200000
    # How long a call may wait for the per-minute budget before giving the user the
    # friendly "try again" reply.
    llm_max_wait_seconds: float = 10.0

    # Retrieval (used from Phase 2)
    embed_model: str = "BAAI/bge-small-en-v1.5"
    retrieval_top_k: int = 5
    # Minimum best vector cosine similarity for a question to count as answerable.
    # Tuned on tests/eval/data/retrieval.jsonl (2026-11-07): off-domain max 0.503,
    # in-scope min 0.632. Re-tune with `python -m tests.eval.retrieval_eval`.
    relevance_threshold: float = 0.57

    # Vector store: "chroma" (default, local files), "chroma_remote" (shared Chroma server or
    # Chroma Cloud, used by the daily GitHub Actions refresh) or "pinecone" (optional)
    vector_store: str = "chroma"
    # chroma_remote, self-hosted server:
    chroma_host: str = "localhost"
    chroma_port: int = 8000
    chroma_ssl: bool = False
    chroma_auth_token: SecretStr | None = None
    # chroma_remote, Chroma Cloud (used when CHROMA_API_KEY is set):
    chroma_tenant: str | None = None
    chroma_database: str | None = None
    chroma_api_key: SecretStr | None = None
    # How often the running app checks for a newly activated index version (0 = never).
    index_refresh_seconds: int = 300
    # /api/health reports stale=true when the corpus data is older than this.
    stale_after_days: int = 2
    pinecone_api_key: SecretStr | None = None
    pinecone_index: str = "mf-faq"
    pinecone_namespace_prefix: str = "dev"  # namespaces are "<prefix>-<build version>"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"

    # Paths
    data_dir: Path = Field(default=PROJECT_ROOT / "data")

    # API: comma-separated list of allowed browser origins
    cors_origins: str = "http://localhost:5173"
    # Messages per client IP per minute (edge case GAP-37), counting every message. The
    # Groq budget separately caps factual answers (~8-9/minute in total), so this mainly
    # stops scripted flooding.
    api_messages_per_minute: int = 20
    api_max_request_bytes: int = 16384  # edge case GAP-38
    max_message_chars: int = 500
    db_file: str = "app.db"  # inside DATA_DIR

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_api_key.get_secret_value().strip())

    def resolved_data_dir(self) -> Path:
        """DATA_DIR may be relative; resolve it against the project root."""
        return self.data_dir if self.data_dir.is_absolute() else PROJECT_ROOT / self.data_dir


@lru_cache
def get_settings() -> Settings:
    return Settings()
