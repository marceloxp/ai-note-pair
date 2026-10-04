"""Sender synchronization performed by a successful send."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_note_pair.archive import archive_room
from ai_note_pair.cli import app
from ai_note_pair.rooms import create_room
from ai_note_pair.storage import DATABASE_FILENAME


def _invoke(runner: CliRunner, args: list[str], cli_env: dict[str, str]):
    return runner.invoke(app, args, env=cli_env)


def _create(runner: CliRunner, cli_env: dict[str, str]) -> None:
    result = _invoke(runner, ["create-room", "--name", "projectx"], cli_env)
    assert result.exit_code == 0, result.stderr


def _send(
    runner: CliRunner,
    cli_env: dict[str, str],
    *,
    name: str,
    message: str,
    to: str | None = None,
    as_json: bool = False,
):
    args = ["send", "--room", "projectx", "--name", name, "--message", message]
    if to is not None:
        args.extend(["--to", to])
    if as_json:
        args.append("--json")
    return _invoke(runner, args, cli_env)


def _read(runner: CliRunner, cli_env: dict[str, str], name: str, *, as_json: bool = False):
    args = ["read", "--room", "projectx", "--name", name]
    if as_json:
        args.append("--json")
    return _invoke(runner, args, cli_env)


def _cursors(storage_home: Path) -> dict[str, int]:
    database = storage_home / "rooms" / "projectx" / DATABASE_FILENAME
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute("SELECT name, last_read_message_id FROM agents").fetchall()
    finally:
        connection.close()
    return {name: cursor for name, cursor in rows}


def _cursor_in(database: Path, name: str) -> int | None:
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


def test_send_without_pending_context_does_not_echo_the_new_message(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    sent = _send(runner, cli_env, name="alice", message="hello", to="bob", as_json=True)
    assert sent.exit_code == 0, sent.stderr
    payload = json.loads(sent.stdout)
    assert payload["id"] == 1
    assert payload["pending"] == []
    assert "hello" not in sent.stdout
    assert _cursors(storage_home) == {"alice": 1, "bob": 0}

    follow_up = _read(runner, cli_env, "alice")
    assert follow_up.stdout.strip() == "No unread messages."
    assert _cursors(storage_home)["bob"] == 0


def test_send_returns_pending_context_and_then_marks_it_read(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    assert _send(runner, cli_env, name="alice", message="one", to="bob").exit_code == 0
    assert _send(runner, cli_env, name="alice", message="two", to="carol").exit_code == 0
    assert _read(runner, cli_env, "bob").exit_code == 0
    assert _cursors(storage_home)["bob"] == 2

    assert _send(runner, cli_env, name="carol", message="for dave", to="dave").exit_code == 0
    assert _send(runner, cli_env, name="alice", message="broadcast", to="all").exit_code == 0
    before = _cursors(storage_home)
    assert before["bob"] == 2
    assert before["dave"] == 0

    sent = _send(runner, cli_env, name="bob", message="reply", to="alice", as_json=True)
    assert sent.exit_code == 0, sent.stderr
    payload = json.loads(sent.stdout)
    assert payload["id"] == 5
    assert payload["recipient"] == "alice"
    assert [item["id"] for item in payload["pending"]] == [3, 4]
    assert [item["recipient"] for item in payload["pending"]] == ["dave", "all"]
    assert payload["pending"][1]["content"] == "broadcast"
    assert all(item["id"] != 5 for item in payload["pending"])
    assert _cursors(storage_home)["bob"] == 5
    assert _cursors(storage_home)["alice"] == before["alice"]
    assert _cursors(storage_home)["carol"] == before["carol"]
    assert _cursors(storage_home)["dave"] == 0

    reread = _read(runner, cli_env, "bob")
    assert reread.stdout.strip() == "No unread messages."

    human = _send(runner, cli_env, name="dave", message="seen", to="bob")
    assert human.exit_code == 0, human.stderr
    assert "Sent message 6" in human.stdout
    assert "[1]" in human.stdout
    assert "[5]" in human.stdout
    assert "for dave" in human.stdout
    assert "broadcast" in human.stdout
    assert "[6]" not in human.stdout
    assert "seen" not in human.stdout
    assert _cursors(storage_home)["dave"] == 6
    assert _cursors(storage_home)["bob"] == 5

    dave_again = _read(runner, cli_env, "dave", as_json=True)
    assert json.loads(dave_again.stdout)["messages"] == []


def test_two_and_four_agent_rooms_keep_independent_cursors(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    assert _send(runner, cli_env, name="alice", message="direct", to="bob").exit_code == 0
    reply = _send(runner, cli_env, name="bob", message="back")
    assert reply.exit_code == 0, reply.stderr
    assert "[1]" in reply.stdout
    assert _cursors(storage_home) == {"alice": 1, "bob": 2}

    assert _send(runner, cli_env, name="carol", message="elsewhere", to="dave").exit_code == 0
    assert _cursors(storage_home)["alice"] == 1
    assert _cursors(storage_home)["bob"] == 2
    assert _cursors(storage_home)["carol"] == 3
    assert _cursors(storage_home)["dave"] == 0

    alice = _read(runner, cli_env, "alice", as_json=True)
    assert [item["id"] for item in json.loads(alice.stdout)["messages"]] == [2, 3]
    assert _cursors(storage_home)["dave"] == 0
    assert _cursors(storage_home)["carol"] == 3


def test_failed_send_does_not_consume_pending_messages(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    assert _send(runner, cli_env, name="alice", message="keep", to="bob").exit_code == 0
    assert _send(runner, cli_env, name="carol", message="also", to="dave").exit_code == 0
    rejected = _send(runner, cli_env, name="bob", message="which?")
    assert rejected.exit_code == 1
    assert rejected.stdout == ""
    assert _cursors(storage_home)["bob"] == 0

    read = _read(runner, cli_env, "bob", as_json=True)
    assert [item["id"] for item in json.loads(read.stdout)["messages"]] == [1, 2]


def test_failed_output_leaves_the_sender_cursor_unchanged(
    runner: CliRunner,
    storage_home: Path,
    cli_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(runner, cli_env)
    assert _send(runner, cli_env, name="alice", message="pending", to="bob").exit_code == 0
    import ai_note_pair.cli as cli_module

    original = cli_module.typer.echo

    def broken_output(*args: object, **kwargs: object) -> None:
        raise OSError("broken pipe")

    monkeypatch.setattr(cli_module.typer, "echo", broken_output)
    failed = _send(runner, cli_env, name="bob", message="stored")
    monkeypatch.setattr(cli_module.typer, "echo", original)

    assert failed.exit_code != 0
    assert _cursors(storage_home)["bob"] == 0
    database = storage_home / "rooms" / "projectx" / DATABASE_FILENAME
    connection = sqlite3.connect(database)
    try:
        count = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    finally:
        connection.close()
    assert count == 2
    recovered = _read(runner, cli_env, "bob", as_json=True)
    assert [item["content"] for item in json.loads(recovered.stdout)["messages"]] == [
        "pending",
        "stored",
    ]


def test_later_arrivals_stay_unread_for_the_sender(
    runner: CliRunner,
    storage_home: Path,
    cli_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(runner, cli_env)
    assert _send(runner, cli_env, name="alice", message="first", to="bob").exit_code == 0
    import ai_note_pair.cli as cli_module

    original = cli_module.typer.echo
    state = {"arrived": False}

    def echo_and_arrive(message: object = "", *args: object, **kwargs: object) -> None:
        if not state["arrived"] and str(message).startswith("Sent message"):
            state["arrived"] = True
            late = threading.Thread(
                target=lambda: _send(runner, cli_env, name="alice", message="late", to="bob")
            )
            late.start()
            late.join(timeout=3)
            assert not late.is_alive()
        original(message, *args, **kwargs)

    monkeypatch.setattr(cli_module.typer, "echo", echo_and_arrive)
    sent = _send(runner, cli_env, name="bob", message="reply")
    monkeypatch.setattr(cli_module.typer, "echo", original)

    assert sent.exit_code == 0, sent.stderr
    assert "first" in sent.stdout
    assert "late" not in sent.stdout
    assert "[1]" in sent.stdout
    assert "[2]" not in sent.stdout
    assert _cursors(storage_home)["bob"] == 2
    follow_up = _read(runner, cli_env, "bob", as_json=True)
    messages = json.loads(follow_up.stdout)["messages"]
    assert [item["content"] for item in messages] == ["late"]
    assert _cursors(storage_home)["alice"] == 3


def test_send_ack_stays_on_the_room_instance_that_was_sent(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(runner, cli_env)
    assert _send(runner, cli_env, name="alice", message="old", to="bob").exit_code == 0
    import ai_note_pair.cli as cli_module

    original = cli_module.typer.echo
    archived: dict[str, str] = {}

    def echo_and_archive(message: object = "", *args: object, **kwargs: object) -> None:
        if not archived and str(message).startswith("Sent message"):
            archived["name"] = archive_room("projectx", storage_home)
            create_room("projectx", storage_home)
        original(message, *args, **kwargs)

    monkeypatch.setattr(cli_module.typer, "echo", echo_and_archive)
    sent = _send(runner, cli_env, name="bob", message="from the old room")
    monkeypatch.setattr(cli_module.typer, "echo", original)

    assert sent.exit_code == 0, sent.stderr
    assert "[1]" in sent.stdout
    archived_db = storage_home / "archived" / archived["name"] / DATABASE_FILENAME
    assert _cursor_in(archived_db, "bob") == 2
    assert _cursor_in(storage_home / "rooms" / "projectx" / DATABASE_FILENAME, "bob") is None
