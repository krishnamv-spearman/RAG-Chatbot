"""SQLite storage for chat threads and messages (architecture §8.1).

- WAL mode: many readers alongside one writer; busy_timeout 5 s (edge case THR-10).
- Foreign keys ON: deleting a thread deletes its messages; saving a message into a deleted
  thread fails with ThreadGone (edge case THR-03).
- One short-lived connection per call, so it's safe from FastAPI's thread pool.
- Never stores PII: the API runs the PII guard before anything is written.
"""

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  last_scheme TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id           TEXT PRIMARY KEY,
  thread_id    TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
  role         TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content      TEXT NOT NULL,
  kind         TEXT,
  source_url   TEXT,
  source_date  TEXT,
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, created_at);
"""

DEFAULT_TITLE = "New chat"
TITLE_CHARS = 40


class ThreadGone(Exception):
    """The thread was deleted (e.g. while an answer was being generated)."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def make_title(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= TITLE_CHARS else text[:TITLE_CHARS - 1].rstrip() + "…"


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------ threads

    def create_thread(self) -> dict:
        now = _now()
        row = {"id": str(uuid.uuid4()), "title": DEFAULT_TITLE, "last_scheme": None,
               "created_at": now, "updated_at": now}
        with self._connect() as conn:
            conn.execute("INSERT INTO threads VALUES (:id, :title, :last_scheme, :created_at, "
                         ":updated_at)", row)
        return row

    def get_thread(self, thread_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
        return dict(row) if row else None

    def list_threads(self, ids: list[str]) -> list[dict]:
        """Only the threads the client already knows (no listing of others' threads)."""
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM threads WHERE id IN ({marks}) "
                                "ORDER BY updated_at DESC", ids).fetchall()
        return [dict(r) for r in rows]

    def delete_thread(self, thread_id: str) -> bool:
        with self._connect() as conn:
            return conn.execute("DELETE FROM threads WHERE id = ?", (thread_id,)).rowcount > 0

    def update_after_turn(self, thread_id: str, last_scheme: str | None,
                          first_question: str | None) -> None:
        """Set the thread's scheme in focus; title it from the first stored question."""
        with self._connect() as conn:
            conn.execute("UPDATE threads SET last_scheme = ?, updated_at = ? WHERE id = ?",
                         (last_scheme, _now(), thread_id))
            if first_question:
                conn.execute("UPDATE threads SET title = ? WHERE id = ? AND title = ?",
                             (make_title(first_question), thread_id, DEFAULT_TITLE))

    # ------------------------------------------------------------ messages

    def add_message(self, thread_id: str, role: str, content: str, kind: str | None = None,
                    source_url: str | None = None, source_date: str | None = None) -> dict:
        row = {"id": str(uuid.uuid4()), "thread_id": thread_id, "role": role,
               "content": content, "kind": kind, "source_url": source_url,
               "source_date": source_date, "created_at": _now()}
        try:
            with self._connect() as conn:
                conn.execute("INSERT INTO messages VALUES (:id, :thread_id, :role, :content, "
                             ":kind, :source_url, :source_date, :created_at)", row)
        except sqlite3.IntegrityError as exc:  # FOREIGN KEY constraint failed
            raise ThreadGone(thread_id) from exc
        return row

    def get_messages(self, thread_id: str, limit: int = 100,
                     before: str | None = None) -> list[dict]:
        """Oldest first. `before` (a message id) pages backwards (edge case THR-08)."""
        query = "SELECT * FROM messages WHERE thread_id = ?"
        params: list = [thread_id]
        if before:
            query += " AND rowid < (SELECT rowid FROM messages WHERE id = ?)"
            params.append(before)
        query += " ORDER BY rowid DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in reversed(rows)]

    def recent_history(self, thread_id: str, n: int = 6) -> list[tuple[str, str]]:
        """Last n messages as (role, text), oldest first: the follow-up context."""
        return [(m["role"], m["content"]) for m in self.get_messages(thread_id, limit=n)]

    def count_user_messages(self, thread_id: str) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM messages WHERE thread_id = ? AND "
                                "role = 'user'", (thread_id,)).fetchone()[0]
