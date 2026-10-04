"""Archiving, archived listing, and storage failures."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from ai_note_pair.archive import archive_room
from ai_note_pair.cli import app
from ai_note_pair.errors import AiNotePairError
from ai_note_pair.storage import DATABASE_FILENAME, SCHEMA_VERSION


def _invoke(runner: CliRunner, args: list[str], cli_env: dict[str, str]):
    return runner.invoke(app, args, env=cli_env)


def _create(runner: CliRunner, cli_env: dict[str, str], room: str = "projectx") -> None:
    result = _invoke(runner, ["create-room", "--name", room], cli_env)
    assert result.exit_code == 0, result.stderr


def test_archive_preserves_room_and_frees_the_name(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    sent = _invoke(
        runner,
        ["send", "--room", "projectx", "--name", "alice", "--to", "bob", "--message", "keep"],
        cli_env,
    )
    assert sent.exit_code == 0, sent.stderr
    attachment = storage_home / "rooms" / "projectx" / "attachments" / "note.txt"
    attachment.write_text("attached", encoding="utf-8")
    database = storage_home / "rooms" / "projectx" / DATABASE_FILENAME
    connection = sqlite3.connect(database)
    connection.execute(
        """
        UPDATE agents
        SET last_read_message_id = 1, read_at = '2026-01-01T00:00:00Z'
        WHERE name = 'alice'
        """
    )
    connection.commit()
    connection.close()

    archived = _invoke(runner, ["archive", "--room", "projectx"], cli_env)
    assert archived.exit_code == 0, archived.stderr
    archive_name = archived.stdout.strip().split(" as ")[1].strip("'.")
    assert archive_name.startswith("projectx-")
    assert not (storage_home / "rooms" / "projectx").exists()

    archived_room = storage_home / "archived" / archive_name
    assert (archived_room / "attachments" / "note.txt").read_text(encoding="utf-8") == "attached"
    connection = sqlite3.connect(archived_room / DATABASE_FILENAME)
    try:
        content = connection.execute("SELECT content FROM messages").fetchone()[0]
        cursor = connection.execute(
            "SELECT last_read_message_id, read_at FROM agents WHERE name = 'alice'"
        ).fetchone()
    finally:
        connection.close()
    assert content == "keep"
    assert cursor == (1, "2026-01-01T00:00:00Z")

    listed = _invoke(runner, ["list-rooms"], cli_env)
    assert listed.stdout == ""
    archived_list = _invoke(runner, ["list-rooms", "--archived"], cli_env)
    assert archived_list.stdout.splitlines() == [archive_name]

    recreated = _invoke(runner, ["create-room", "--name", "projectx"], cli_env)
    assert recreated.exit_code == 0, recreated.stderr
    info = _invoke(runner, ["info", "--room", "projectx", "--json"], cli_env)
    assert '"messages": 0' in info.stdout
    assert '"participants": 0' in info.stdout
    assert (archived_room / DATABASE_FILENAME).is_file()


def test_timestamp_collision_preserves_the_existing_archive(
    storage_home: Path, runner: CliRunner, cli_env: dict[str, str]
) -> None:
    moment = datetime(2026, 10, 3, 20, 37, 15, tzinfo=timezone.utc)
    _create(runner, cli_env)
    (storage_home / "rooms" / "projectx" / "attachments" / "first.txt").write_text(
        "first", encoding="utf-8"
    )
    first = archive_room("projectx", storage_home, moment)
    _create(runner, cli_env)
    (storage_home / "rooms" / "projectx" / "attachments" / "second.txt").write_text(
        "second", encoding="utf-8"
    )
    second = archive_room("projectx", storage_home, moment)
    assert first == "projectx-20261003203715"
    assert second == "projectx-20261003203715-2"
    archived = storage_home / "archived"
    assert (archived / first / "attachments" / "first.txt").read_text(encoding="utf-8") == "first"
    assert (archived / second / "attachments" / "second.txt").read_text(
        encoding="utf-8"
    ) == "second"
    listed = _invoke(runner, ["list-rooms", "--archived"], cli_env)
    assert listed.stdout.splitlines() == [first, second]


def test_concurrent_archives_keep_a_single_copy(
    storage_home: Path, runner: CliRunner, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    marker = storage_home / "rooms" / "projectx" / "attachments" / "marker.txt"
    marker.write_text("only-copy", encoding="utf-8")
    outcomes: list[tuple[str, str]] = []
    lock = threading.Lock()

    def archive_once() -> None:
        try:
            name = archive_room("projectx", storage_home)
            with lock:
                outcomes.append(("ok", name))
        except AiNotePairError as exc:
            with lock:
                outcomes.append(("err", str(exc)))

    threads = [threading.Thread(target=archive_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not (storage_home / "rooms" / "projectx").exists()
    successes = [item for item in outcomes if item[0] == "ok"]
    failures = [item for item in outcomes if item[0] == "err"]
    assert len(successes) == 1
    assert len(failures) == 1
    assert "does not exist" in failures[0][1]
    archived_files = list((storage_home / "archived").rglob("marker.txt"))
    assert len(archived_files) == 1
    assert archived_files[0].read_text(encoding="utf-8") == "only-copy"


def test_archive_failure_leaves_the_active_room(
    storage_home: Path, runner: CliRunner, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    marker = storage_home / "rooms" / "projectx" / "attachments" / "marker.txt"
    marker.write_text("still here", encoding="utf-8")
    blocked = storage_home / "archived"
    blocked.write_text("not a directory", encoding="utf-8")
    result = _invoke(runner, ["archive", "--room", "projectx"], cli_env)
    assert result.exit_code == 1
    assert "Could not prepare archive storage" in result.stderr
    assert "Archived room" not in result.stdout
    assert marker.read_text(encoding="utf-8") == "still here"

    missing = _invoke(runner, ["archive", "--room", "missing"], cli_env)
    assert missing.exit_code == 1
    assert "does not exist" in missing.stderr


def test_corrupt_and_unsupported_databases_are_rejected(
    storage_home: Path, runner: CliRunner, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    database = storage_home / "rooms" / "projectx" / DATABASE_FILENAME
    connection = sqlite3.connect(database)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
    connection.commit()
    connection.close()

    unsupported = _invoke(runner, ["info", "--room", "projectx"], cli_env)
    assert unsupported.exit_code == 1
    assert "Unsupported room schema version" in unsupported.stderr
    assert unsupported.stdout == ""

    for suffix in ("-wal", "-shm"):
        Path(f"{database}{suffix}").unlink(missing_ok=True)
    database.write_bytes(b"this is not a sqlite database")
    corrupt = _invoke(
        runner,
        ["send", "--room", "projectx", "--name", "alice", "--to", "bob", "--message", "x"],
        cli_env,
    )
    assert corrupt.exit_code == 1
    assert "Could not open room database" in corrupt.stderr
    assert corrupt.stdout == ""
    assert database.read_bytes().startswith(b"this is not")
