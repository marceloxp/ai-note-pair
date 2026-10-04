"""Room identity, storage containment, and busy-database errors."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_note_pair.archive import archive_room
from ai_note_pair.attachments import copy_attachment
from ai_note_pair.cli import app
from ai_note_pair.messaging import acknowledge_read, collect_unread, publish_message
from ai_note_pair.storage import DATABASE_FILENAME


def _invoke(runner: CliRunner, args: list[str], cli_env: dict[str, str]):
    return runner.invoke(app, args, env=cli_env)


def _create(runner: CliRunner, cli_env: dict[str, str]) -> None:
    result = _invoke(runner, ["create-room", "--name", "projectx"], cli_env)
    assert result.exit_code == 0, result.stderr


def _cursor(database: Path, name: str = "alice") -> int | None:
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT last_read_message_id FROM agents WHERE name = ?",
            (name,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return int(row[0])


def test_stale_read_does_not_mark_the_replacement_room(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    for text in ("one", "two", "three"):
        sent = _invoke(
            runner,
            ["send", "--room", "projectx", "--name", "alice", "--to", "bob", "--message", text],
            cli_env,
        )
        assert sent.exit_code == 0, sent.stderr
    batch = collect_unread("projectx", "bob", storage_home)
    assert [message.id for message in batch.messages] == [1, 2, 3]

    archive_room("projectx", storage_home)
    assert _invoke(runner, ["create-room", "--name", "projectx"], cli_env).exit_code == 0
    replacement = _invoke(
        runner,
        ["send", "--room", "projectx", "--name", "alice", "--to", "bob", "--message", "fresh"],
        cli_env,
    )
    assert replacement.exit_code == 0, replacement.stderr

    acknowledge_read(batch, storage_home)

    new_database = storage_home / "rooms" / "projectx" / DATABASE_FILENAME
    assert _cursor(new_database, "bob") == 0
    assert _cursor(new_database, "alice") == 1
    reread = _invoke(runner, ["read", "--room", "projectx", "--name", "bob", "--json"], cli_env)
    assert reread.exit_code == 0, reread.stderr
    assert '"fresh"' in reread.stdout

    archived = list((storage_home / "archived").iterdir())
    assert len(archived) == 1
    assert _cursor(archived[0] / DATABASE_FILENAME, "bob") == 3


def test_archive_waits_until_send_finishes_with_its_attachment(
    storage_home: Path,
    tmp_path: Path,
    runner: CliRunner,
    cli_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(runner, cli_env)
    source = tmp_path / "note.txt"
    source.write_text("payload", encoding="utf-8")
    copied = threading.Event()
    release = threading.Event()

    def blocking_copy(room: Path, attachment: Path) -> str:
        relative = copy_attachment(room, attachment)
        copied.set()
        assert release.wait(timeout=5)
        return relative

    monkeypatch.setattr("ai_note_pair.messaging.copy_attachment", blocking_copy)
    outcome: dict[str, object] = {}

    def send() -> None:
        try:
            outcome["sent"] = publish_message(
                "projectx",
                "alice",
                "with file",
                "bob",
                storage_home,
                [source],
            )
        except Exception as exc:  # pragma: no cover - surfaced by assertion
            outcome["error"] = exc

    sender = threading.Thread(target=send)
    sender.start()
    assert copied.wait(timeout=5)
    active = storage_home / "rooms" / "projectx"
    assert (active / "attachments" / "note.txt").is_file()
    connection = sqlite3.connect(active / DATABASE_FILENAME)
    try:
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    finally:
        connection.close()

    archived_name: dict[str, object] = {}

    def archive() -> None:
        try:
            archived_name["name"] = archive_room("projectx", storage_home)
        except Exception as exc:  # pragma: no cover - surfaced by assertion
            archived_name["error"] = exc

    archiver = threading.Thread(target=archive)
    archiver.start()
    time.sleep(0.3)
    assert active.exists()
    connection = sqlite3.connect(active / DATABASE_FILENAME)
    try:
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    finally:
        connection.close()
    release.set()
    sender.join(timeout=5)
    archiver.join(timeout=5)

    assert "error" not in outcome
    assert "error" not in archived_name
    assert not active.exists()
    archived = storage_home / "archived" / str(archived_name["name"])
    assert (archived / "attachments" / "note.txt").read_text(encoding="utf-8") == "payload"
    connection = sqlite3.connect(archived / DATABASE_FILENAME)
    try:
        row = connection.execute("SELECT content, attachment_paths FROM messages").fetchone()
    finally:
        connection.close()
    assert row[0] == "with file"
    assert "attachments/note.txt" in row[1]


def test_locked_database_returns_a_clear_error(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    database = storage_home / "rooms" / "projectx" / DATABASE_FILENAME
    holder = sqlite3.connect(database)
    holder.isolation_level = None
    holder.execute("BEGIN IMMEDIATE")
    try:
        result = _invoke(
            runner,
            ["send", "--room", "projectx", "--name", "alice", "--to", "bob", "--message", "x"],
            cli_env,
        )
    finally:
        holder.rollback()
        holder.close()
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "busy" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout


def test_room_symlink_cannot_escape_storage(
    runner: CliRunner, tmp_path: Path, storage_home: Path, cli_env: dict[str, str]
) -> None:
    outside = tmp_path / "outside-room"
    outside.mkdir()
    external = sqlite3.connect(outside / DATABASE_FILENAME)
    external.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, content TEXT)")
    external.execute("INSERT INTO messages (content) VALUES ('external')")
    external.commit()
    external.close()

    rooms = storage_home / "rooms"
    rooms.mkdir()
    (rooms / "projectx").symlink_to(outside, target_is_directory=True)

    sent = _invoke(
        runner,
        ["send", "--room", "projectx", "--name", "alice", "--to", "bob", "--message", "escape"],
        cli_env,
    )
    assert sent.exit_code == 1
    assert sent.stdout == ""
    assert "symbolic link" in sent.stderr
    connection = sqlite3.connect(outside / DATABASE_FILENAME)
    try:
        rows = connection.execute("SELECT content FROM messages").fetchall()
    finally:
        connection.close()
    assert rows == [("external",)]

    created = _invoke(runner, ["create-room", "--name", "projectx"], cli_env)
    assert created.exit_code == 1
    assert (rooms / "projectx").is_symlink()


def test_legacy_room_without_instance_id_keeps_messages_and_cursors(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    room = storage_home / "rooms" / "projectx"
    (room / "attachments").mkdir(parents=True)
    database = room / DATABASE_FILENAME
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
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
        INSERT INTO metadata (key, value) VALUES ('created_at', '2026-01-01T00:00:00Z');
        INSERT INTO metadata (key, value) VALUES ('updated_at', '2026-01-01T00:00:00Z');
        INSERT INTO agents (name, last_read_message_id, read_at)
        VALUES ('alice', 0, NULL);
        INSERT INTO agents (name, last_read_message_id, read_at)
        VALUES ('bob', 1, '2026-01-02T00:00:00Z');
        INSERT INTO messages (
            timestamp, sender_name, recipient_name, content, attachment_paths
        ) VALUES ('2026-01-01T00:00:01Z', 'alice', 'bob', 'legacy note', '[]');
        """
    )
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()

    read = _invoke(runner, ["read", "--room", "projectx", "--name", "alice", "--json"], cli_env)
    assert read.exit_code == 0, read.stderr
    assert "legacy note" in read.stdout
    assert "Traceback" not in read.stderr

    connection = sqlite3.connect(database)
    try:
        instance_id = connection.execute(
            "SELECT value FROM metadata WHERE key = 'instance_id'"
        ).fetchone()[0]
        content = connection.execute("SELECT content FROM messages").fetchone()[0]
        bob = connection.execute(
            "SELECT last_read_message_id, read_at FROM agents WHERE name = 'bob'"
        ).fetchone()
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()
    assert instance_id
    assert content == "legacy note"
    assert bob == (1, "2026-01-02T00:00:00Z")
    assert version == 1
    assert _cursor(database, "alice") == 1


def test_unwritable_attachment_directory_is_a_clear_error(
    runner: CliRunner, tmp_path: Path, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    source = tmp_path / "note.txt"
    source.write_text("payload", encoding="utf-8")
    attachments = storage_home / "rooms" / "projectx" / "attachments"
    attachments.chmod(0o555)
    try:
        result = _invoke(
            runner,
            [
                "send",
                "--room",
                "projectx",
                "--name",
                "alice",
                "--to",
                "bob",
                "--message",
                "with file",
                "--attachment",
                str(source),
            ],
            cli_env,
        )
    finally:
        attachments.chmod(0o755)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Could not copy attachment" in result.stderr
    assert "Permission denied" in result.stderr
    assert "Traceback" not in result.stderr
    database = storage_home / "rooms" / "projectx" / DATABASE_FILENAME
    connection = sqlite3.connect(database)
    try:
        count = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    finally:
        connection.close()
    assert count == 0
    assert list(attachments.iterdir()) == []
