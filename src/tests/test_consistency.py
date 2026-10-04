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
    batch = collect_unread("projectx", "alice", storage_home)
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
    assert _cursor(new_database) == 0
    reread = _invoke(runner, ["read", "--room", "projectx", "--name", "alice", "--json"], cli_env)
    assert reread.exit_code == 0, reread.stderr
    assert '"fresh"' in reread.stdout

    archived = list((storage_home / "archived").iterdir())
    assert len(archived) == 1
    assert _cursor(archived[0] / DATABASE_FILENAME) == 3


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
