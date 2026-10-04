"""Filesystem layout and SQLite setup for local rooms."""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
DATABASE_FILENAME = "ai-note-pair.db"
ATTACHMENTS_DIRNAME = "attachments"

_SCHEMA_SQL = """
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE agents (
    name TEXT PRIMARY KEY,
    last_read_message_id INTEGER NOT NULL DEFAULT 0,
    read_at TEXT
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    sender_name TEXT NOT NULL,
    recipient_name TEXT NOT NULL,
    content TEXT NOT NULL,
    attachment_paths TEXT NOT NULL DEFAULT '[]'
);
"""


def utc_now() -> str:
    """Return the current time as a UTC timestamp with a Z suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def home_dir(explicit: Path | None = None) -> Path:
    """Resolve the storage root, honoring an explicit path or AI_NOTE_PAIR_HOME."""
    if explicit is not None:
        return explicit.expanduser()
    override = os.environ.get("AI_NOTE_PAIR_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "ai-note-pair"


def rooms_dir(home: Path | None = None) -> Path:
    return home_dir(home) / "rooms"


def archived_dir(home: Path | None = None) -> Path:
    return home_dir(home) / "archived"


def connect(database: Path) -> sqlite3.Connection:
    """Open a room database with settings suitable for local concurrent CLIs."""
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.isolation_level = None
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 1000")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def initialize_database(database: Path) -> None:
    """Create an empty schema version 1 database with room metadata."""
    created_at = utc_now()
    with connect(database) as connection:
        connection.executescript(_SCHEMA_SQL)
        connection.executemany(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            (
                ("created_at", created_at),
                ("updated_at", created_at),
                ("instance_id", uuid.uuid4().hex),
            ),
        )
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()


def ensure_instance_id(connection: sqlite3.Connection) -> None:
    """Assign instance_id on a version 1 room created before that field existed."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute("SELECT value FROM metadata WHERE key = 'instance_id'").fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO metadata (key, value) VALUES ('instance_id', ?)",
                (uuid.uuid4().hex,),
            )
        connection.commit()
    except sqlite3.Error:
        try:
            connection.rollback()
        except sqlite3.Error:
            pass
        raise


def schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0])
