import sqlite3

import pytest

from app.db import DEFAULT_TITLE, Database, ThreadGone, make_title


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "app.db")


def test_wal_and_foreign_keys(db):
    conn = sqlite3.connect(db.path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_thread_lifecycle_and_cascade(db):
    t = db.create_thread()
    assert t["title"] == DEFAULT_TITLE and db.get_thread(t["id"])["id"] == t["id"]
    db.add_message(t["id"], "user", "q")
    db.add_message(t["id"], "assistant", "a", "answer", "https://x", "2026-11-06")
    assert db.delete_thread(t["id"]) is True
    assert db.get_thread(t["id"]) is None
    with sqlite3.connect(db.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert db.delete_thread(t["id"]) is False


def test_message_into_deleted_thread_raises(db):
    t = db.create_thread()
    db.delete_thread(t["id"])
    with pytest.raises(ThreadGone):
        db.add_message(t["id"], "assistant", "late answer")


def test_messages_are_ordered_and_pageable(db):
    t = db.create_thread()
    ids = [db.add_message(t["id"], "user", f"m{i}")["id"] for i in range(10)]
    assert [m["content"] for m in db.get_messages(t["id"])] == [f"m{i}" for i in range(10)]
    assert [m["content"] for m in db.get_messages(t["id"], limit=3)] == ["m7", "m8", "m9"]
    page = db.get_messages(t["id"], limit=3, before=ids[7])
    assert [m["content"] for m in page] == ["m4", "m5", "m6"]
    assert db.recent_history(t["id"], 2) == [("user", "m8"), ("user", "m9")]


def test_title_from_first_question_only(db):
    t = db.create_thread()
    db.update_after_turn(t["id"], "hdfc-mid-cap", "What is the expense ratio of HDFC Mid Cap?")
    db.update_after_turn(t["id"], "hdfc-small-cap", "a later question")
    thread = db.get_thread(t["id"])
    assert thread["title"].startswith("What is the expense ratio")
    assert thread["last_scheme"] == "hdfc-small-cap"


def test_make_title_truncates():
    assert make_title("short") == "short"
    long = make_title("x" * 80)
    assert len(long) == 40 and long.endswith("…")


def test_list_threads_only_known_ids(db):
    a, b = db.create_thread(), db.create_thread()
    assert {t["id"] for t in db.list_threads([a["id"], "missing"])} == {a["id"]}
    assert db.list_threads([]) == []
    assert len(db.list_threads([a["id"], b["id"]])) == 2
