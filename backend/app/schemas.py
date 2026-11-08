"""Request and response models for the REST API (architecture §8.2)."""

from pydantic import BaseModel, Field, field_validator

from app.ingest.normalize import display_date


class MessageIn(BaseModel):
    text: str = Field(..., description="The user's question")

    @field_validator("text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message is empty")  # edge case IN-01
        return value


class ThreadOut(BaseModel):
    id: str
    title: str
    last_scheme: str | None
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    id: str | None  # None for a reply that was not stored (PII warning)
    role: str
    kind: str | None
    text: str
    source_url: str | None
    last_updated: str | None  # ISO date
    footer: str | None  # "Last updated from sources: DD Mon YYYY"
    created_at: str | None

    @classmethod
    def from_row(cls, row: dict) -> "MessageOut":
        date = row.get("source_date")
        footer = (f"Last updated from sources: {display_date(date) or 'unknown'}"
                  if row.get("source_url") else None)
        return cls(id=row["id"], role=row["role"], kind=row.get("kind"), text=row["content"],
                   source_url=row.get("source_url"), last_updated=date, footer=footer,
                   created_at=row.get("created_at"))


class PostMessageOut(BaseModel):
    user_message: MessageOut | None  # None when the question contained PII (not stored)
    message: MessageOut  # the assistant's reply
    thread: ThreadOut


class MetaOut(BaseModel):
    welcome: str
    example_questions: list[str]
    disclaimer: str
    schemes: list[dict]
    corpus_last_updated: str | None
    max_message_chars: int
