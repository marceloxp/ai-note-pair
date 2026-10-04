"""File input, stdin, and room attachments."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from ai_note_pair.cli import app
from ai_note_pair.storage import DATABASE_FILENAME


def _invoke(
    runner: CliRunner, args: list[str], cli_env: dict[str, str], input_text: str | None = None
):
    return runner.invoke(app, args, env=cli_env, input=input_text)


def _create(runner: CliRunner, cli_env: dict[str, str]) -> None:
    result = _invoke(runner, ["create-room", "--name", "projectx"], cli_env)
    assert result.exit_code == 0, result.stderr


def _message_count(storage_home: Path) -> int:
    database = storage_home / "rooms" / "projectx" / DATABASE_FILENAME
    connection = sqlite3.connect(database)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
    finally:
        connection.close()


def _attachment_names(storage_home: Path) -> list[str]:
    directory = storage_home / "rooms" / "projectx" / "attachments"
    return sorted(path.name for path in directory.iterdir() if path.is_file())


def test_message_sources_are_mutually_exclusive(
    runner: CliRunner, tmp_path: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    missing = _invoke(
        runner,
        ["send", "--room", "projectx", "--name", "alice", "--to", "bob"],
        cli_env,
    )
    assert missing.exit_code == 1
    assert "exactly one" in missing.stderr

    source = tmp_path / "note.txt"
    source.write_text("from file", encoding="utf-8")
    both = _invoke(
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
            "inline",
            "--message-file",
            str(source),
        ],
        cli_env,
    )
    assert both.exit_code == 1
    assert "exactly one" in both.stderr
    assert _message_count(cli_env and Path(cli_env["AI_NOTE_PAIR_HOME"])) == 0


def test_file_and_stdin_round_trip(
    runner: CliRunner, tmp_path: Path, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    source = tmp_path / "note.txt"
    source.write_text("linha com ação e emoji 🤖\n", encoding="utf-8")
    from_file = _invoke(
        runner,
        [
            "send",
            "--room",
            "projectx",
            "--name",
            "alice",
            "--to",
            "bob",
            "--message-file",
            str(source),
        ],
        cli_env,
    )
    assert from_file.exit_code == 0, from_file.stderr
    from_stdin = _invoke(
        runner,
        [
            "send",
            "--room",
            "projectx",
            "--name",
            "bob",
            "--message-file",
            "-",
        ],
        cli_env,
        input_text="via stdin",
    )
    assert from_stdin.exit_code == 0, from_stdin.stderr

    read = _invoke(runner, ["read", "--room", "projectx", "--name", "alice", "--json"], cli_env)
    messages = json.loads(read.stdout)["messages"]
    assert [item["content"] for item in messages] == ["linha com ação e emoji 🤖\n", "via stdin"]


def test_large_unicode_message_is_not_truncated(
    runner: CliRunner, tmp_path: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    body = ("á" * 1_000_000) + "END"
    source = tmp_path / "large.txt"
    source.write_text(body, encoding="utf-8")
    sent = _invoke(
        runner,
        [
            "send",
            "--room",
            "projectx",
            "--name",
            "alice",
            "--to",
            "bob",
            "--message-file",
            str(source),
        ],
        cli_env,
    )
    assert sent.exit_code == 0, sent.stderr
    read = _invoke(runner, ["read", "--room", "projectx", "--name", "bob", "--json"], cli_env)
    assert read.exit_code == 0, read.stderr
    content = json.loads(read.stdout)["messages"][0]["content"]
    assert len(content) == len(body)
    assert content.endswith("END")
    assert content == body


def test_multiple_attachments_and_filename_collisions(
    runner: CliRunner, tmp_path: Path, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    first_dir = tmp_path / "a"
    second_dir = tmp_path / "b"
    first_dir.mkdir()
    second_dir.mkdir()
    (first_dir / "notes.txt").write_text("first", encoding="utf-8")
    (second_dir / "notes.txt").write_text("second", encoding="utf-8")
    (tmp_path / "extra.md").write_text("extra", encoding="utf-8")

    sent = _invoke(
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
            "see files",
            "--attachment",
            str(first_dir / "notes.txt"),
            "--attachment",
            str(second_dir / "notes.txt"),
            "--attachment",
            str(tmp_path / "extra.md"),
        ],
        cli_env,
    )
    assert sent.exit_code == 0, sent.stderr
    assert _attachment_names(storage_home) == ["extra.md", "notes-2.txt", "notes.txt"]
    room_attachments = storage_home / "rooms" / "projectx" / "attachments"
    assert (room_attachments / "notes.txt").read_text(encoding="utf-8") == "first"
    assert (room_attachments / "notes-2.txt").read_text(encoding="utf-8") == "second"

    read = _invoke(runner, ["read", "--room", "projectx", "--name", "bob", "--json"], cli_env)
    attachments = json.loads(read.stdout)["messages"][0]["attachments"]
    assert attachments == [
        "attachments/notes.txt",
        "attachments/notes-2.txt",
        "attachments/extra.md",
    ]
    assert (
        "Attachment: attachments/notes.txt"
        in _invoke(runner, ["read", "--room", "projectx", "--name", "alice"], cli_env).stdout
    )


def test_failed_attachment_copy_leaves_no_message(
    runner: CliRunner, tmp_path: Path, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    good = tmp_path / "good.txt"
    good.write_text("keep me out", encoding="utf-8")
    missing = tmp_path / "missing.txt"
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
            "nope",
            "--attachment",
            str(good),
            "--attachment",
            str(missing),
        ],
        cli_env,
    )
    assert result.exit_code == 1
    assert "does not exist" in result.stderr
    assert result.stdout == ""
    assert _message_count(storage_home) == 0
    assert _attachment_names(storage_home) == []

    blocked = tmp_path / "blocked.txt"
    blocked.write_text("secret", encoding="utf-8")
    blocked.chmod(0)
    try:
        denied = _invoke(
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
                "nope",
                "--attachment",
                str(blocked),
            ],
            cli_env,
        )
    finally:
        blocked.chmod(0o644)
    assert denied.exit_code == 1
    assert "Could not copy attachment" in denied.stderr
    assert _message_count(storage_home) == 0
    assert _attachment_names(storage_home) == []


def test_missing_and_invalid_message_files(
    runner: CliRunner, tmp_path: Path, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    missing = _invoke(
        runner,
        [
            "send",
            "--room",
            "projectx",
            "--name",
            "alice",
            "--to",
            "bob",
            "--message-file",
            str(tmp_path / "absent.txt"),
        ],
        cli_env,
    )
    assert missing.exit_code == 1
    assert "does not exist" in missing.stderr

    binary = tmp_path / "binary.txt"
    binary.write_bytes(b"ok\xffno")
    invalid = _invoke(
        runner,
        [
            "send",
            "--room",
            "projectx",
            "--name",
            "alice",
            "--to",
            "bob",
            "--message-file",
            str(binary),
        ],
        cli_env,
    )
    assert invalid.exit_code == 1
    assert "UTF-8" in invalid.stderr
    assert _message_count(storage_home) == 0
