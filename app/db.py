"""SQLite store: one row per managed person, plus an audit log."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    username      TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    email         TEXT,
    key_comment   TEXT,
    status        TEXT NOT NULL,          -- active | offboarded
    linux         TEXT,                   -- ok | error text | skipped
    litellm       TEXT,
    portainer     TEXT,
    portainer_id  INTEGER,
    turnstone     TEXT,
    turnstone_id  TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    username  TEXT NOT NULL,
    detail    TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def list_people() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM people ORDER BY status, username"
        ).fetchall()


def get_person(username: str):
    with connect() as conn:
        return conn.execute("SELECT * FROM people WHERE username = ?", (username,)).fetchone()


def upsert_person(**fields) -> None:
    fields["updated_at"] = now()
    with connect() as conn:
        existing = conn.execute(
            "SELECT 1 FROM people WHERE username = ?", (fields["username"],)
        ).fetchone()
        if existing:
            cols = ", ".join(f"{k} = :{k}" for k in fields if k != "username")
            conn.execute(f"UPDATE people SET {cols} WHERE username = :username", fields)
        else:
            fields.setdefault("created_at", fields["updated_at"])
            cols = ", ".join(fields)
            vals = ", ".join(f":{k}" for k in fields)
            conn.execute(f"INSERT INTO people ({cols}) VALUES ({vals})", fields)


def log(actor: str, action: str, username: str, detail: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO audit (at, actor, action, username, detail) VALUES (?, ?, ?, ?, ?)",
            (now(), actor, action, username, detail),
        )


def recent_audit(limit: int = 50) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
