"""Room creation, listing, isolation, and representative failures."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_note_pair.cli import app
from ai_note_pair.storage import DATABASE_FILENAME, SCHEMA_VERSION, schema_version


def _invoke(runner: CliRunner, args: list[str], cli_env: dict[str, str]):
    return runner.invoke(app, args, env=cli_env)


def _open_room(storage_home: Path, name: str) -> sqlite3.Connection:
    database = storage_home / "rooms" / name / DATABASE_FILENAME
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    return connection


def test_cli_help(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "create-room" in result.stdout
    assert "list-rooms" in result.stdout

    create_help = runner.invoke(app, ["create-room", "--help"])
    assert create_help.exit_code == 0
    assert "--name" in create_help.stdout


def test_create_room_persists_empty_schema(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    created = _invoke(runner, ["create-room", "--name", "projectx"], cli_env)
    assert created.exit_code == 0
    assert "Created room 'projectx'." in created.stdout
    assert created.stderr == ""

    listed = _invoke(runner, ["list-rooms"], cli_env)
    assert listed.exit_code == 0
    assert listed.stdout.splitlines() == ["projectx"]

    room = storage_home / "rooms" / "projectx"
    assert (room / "attachments").is_dir()
    with _open_room(storage_home, "projectx") as connection:
        assert schema_version(connection) == SCHEMA_VERSION
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 0
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    assert set(metadata) == {"created_at", "updated_at"}
    assert metadata["created_at"].endswith("Z")
    assert metadata["updated_at"] == metadata["created_at"]


def test_rooms_are_isolated(runner: CliRunner, storage_home: Path, cli_env: dict[str, str]) -> None:
    assert _invoke(runner, ["create-room", "--name", "alpha"], cli_env).exit_code == 0
    assert _invoke(runner, ["create-room", "--name", "beta"], cli_env).exit_code == 0

    alpha_db = storage_home / "rooms" / "alpha" / DATABASE_FILENAME
    beta_db = storage_home / "rooms" / "beta" / DATABASE_FILENAME
    assert alpha_db.is_file()
    assert beta_db.is_file()
    assert alpha_db.resolve() != beta_db.resolve()

    with _open_room(storage_home, "alpha") as connection:
        connection.execute("INSERT INTO metadata (key, value) VALUES ('marker', 'alpha-only')")
        connection.commit()

    with _open_room(storage_home, "beta") as connection:
        markers = connection.execute("SELECT value FROM metadata WHERE key = 'marker'").fetchall()
    assert markers == []

    listed = _invoke(runner, ["list-rooms"], cli_env)
    assert listed.stdout.splitlines() == ["alpha", "beta"]


def test_duplicate_creation_does_not_overwrite(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    assert _invoke(runner, ["create-room", "--name", "projectx"], cli_env).exit_code == 0
    sentinel = storage_home / "rooms" / "projectx" / "attachments" / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with _open_room(storage_home, "projectx") as connection:
        connection.execute("INSERT INTO metadata (key, value) VALUES ('marker', 'original')")
        connection.commit()

    duplicate = _invoke(runner, ["create-room", "--name", "projectx"], cli_env)
    assert duplicate.exit_code == 1
    assert "already exists" in duplicate.stderr
    assert "Created room" not in duplicate.stdout
    assert sentinel.read_text(encoding="utf-8") == "keep"
    with _open_room(storage_home, "projectx") as connection:
        marker = connection.execute("SELECT value FROM metadata WHERE key = 'marker'").fetchone()[0]
    assert marker == "original"


@pytest.mark.parametrize(
    "room_name",
    [
        "",
        ".",
        "..",
        "../outside",
        "foo/bar",
        "foo\\bar",
        "/tmp/projectx",
        "has space",
        "-leading-hyphen",
    ],
)
def test_invalid_room_names_do_not_escape_storage(
    runner: CliRunner,
    tmp_path: Path,
    storage_home: Path,
    cli_env: dict[str, str],
    room_name: str,
) -> None:
    outside = tmp_path / "outside"
    result = _invoke(runner, ["create-room", "--name", room_name], cli_env)
    assert result.exit_code == 1
    assert "Invalid room name" in result.stderr
    assert not outside.exists()
    rooms = storage_home / "rooms"
    if rooms.exists():
        assert list(rooms.iterdir()) == []


def test_storage_path_failure_is_reported(
    runner: CliRunner, tmp_path: Path, cli_env: dict[str, str]
) -> None:
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("file", encoding="utf-8")
    cli_env = {**cli_env, "AI_NOTE_PAIR_HOME": str(blocked)}
    result = _invoke(runner, ["create-room", "--name", "projectx"], cli_env)
    assert result.exit_code == 1
    assert "Could not prepare storage" in result.stderr
    assert not (tmp_path / "rooms").exists()


def test_list_rooms_is_empty_when_storage_is_missing(
    runner: CliRunner, cli_env: dict[str, str]
) -> None:
    result = _invoke(runner, ["list-rooms"], cli_env)
    assert result.exit_code == 0
    assert result.stdout == ""
