"""Structured JSON logs (one object per line).

Fields come from `logger.info("...", extra={...})`. Message TEXT is never logged by the
API (privacy by default): only ids, kinds, latency and token counts.
"""

import json
import logging
from datetime import UTC, datetime

_STANDARD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}
SECRET_HINTS = ("api_key", "authorization", "secret", "password")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                payload[key] = "[redacted]" if any(h in key.lower() for h in SECRET_HINTS) \
                    else value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "sentence_transformers", "huggingface_hub", "chromadb",
                  "urllib3", "groq"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
