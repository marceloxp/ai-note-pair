"""Sending, participant registration, routing, and room inspection."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from ai_note_pair.cli import app
from ai_note_pair.storage import DATABASE_FILENAME


def _invoke(runner: CliRunner, args: list[str], cli_env: dict[str, str]):
    return runner.invoke(app, args, env=cli_env)


def _create(runner: CliRunner, cli_env: dict[str, str], room: str = "projectx") -> None:
    result = _invoke(runner, ["create-room", "--name", room], cli_env)
    assert result.exit_code == 0, result.stderr


def _send(
    runner: CliRunner,
    cli_env: dict[str, str],
    *,
    room: str = "projectx",
    name: str,
    message: str,
    to: str | None = None,
    as_json: bool = False,
):
    args = ["send", "--room", room, "--name", name, "--message", message]
    if to is not None:
        args.extend(["--to", to])
    if as_json:
        args.append("--json")
    return _invoke(runner, args, cli_env)


def _info(
    runner: CliRunner, cli_env: dict[str, str], room: str = "projectx", *, as_json: bool = False
):
    args = ["info", "--room", room]
    if as_json:
        args.append("--json")
    return _invoke(runner, args, cli_env)


def _counts(storage_home: Path, room: str = "projectx") -> dict[str, int]:
    database = storage_home / "rooms" / room / DATABASE_FILENAME
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            """
            SELECT agents.name, COUNT(messages.id)
            FROM agents
            LEFT JOIN messages ON messages.sender_name = agents.name
            GROUP BY agents.name
            """
        ).fetchall()
    finally:
        connection.close()
    return {name: count for name, count in rows}


def _agent_state(storage_home: Path, room: str = "projectx") -> list[tuple[str, int, str | None]]:
    database = storage_home / "rooms" / room / DATABASE_FILENAME
    connection = sqlite3.connect(database)
    try:
        return connection.execute(
            "SELECT name, last_read_message_id, read_at FROM agents ORDER BY name"
        ).fetchall()
    finally:
        connection.close()


def test_first_direct_send_registers_both_participants(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    sent = _send(runner, cli_env, name="alice", message="hello", to="bob", as_json=True)
    assert sent.exit_code == 0, sent.stderr
    assert sent.stderr == ""
    payload = json.loads(sent.stdout)
    assert payload["sender"] == "alice"
    assert payload["recipient"] == "bob"
    assert payload["id"] == 1

    info = _info(runner, cli_env)
    assert info.exit_code == 0
    assert "Participants: 2" in info.stdout
    assert "Messages: 1" in info.stdout
    assert f"Path: {(storage_home / 'rooms' / 'projectx').resolve()}" in info.stdout
    assert "alice" in info.stdout
    assert "bob" in info.stdout
    assert _counts(storage_home) == {"alice": 1, "bob": 0}

    again = _send(runner, cli_env, name="alice", message="again", to="bob")
    assert again.exit_code == 0
    assert _counts(storage_home) == {"alice": 2, "bob": 0}


def test_two_agent_flow_infers_the_other_participant(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    assert _send(runner, cli_env, name="alice", message="hello", to="bob").exit_code == 0
    reply = _send(runner, cli_env, name="bob", message="hi there")
    assert reply.exit_code == 0
    assert "to 'alice'" in reply.stdout
    follow_up = _send(runner, cli_env, name="alice", message="ack")
    assert follow_up.exit_code == 0
    assert "to 'bob'" in follow_up.stdout
    assert _counts(storage_home) == {"alice": 2, "bob": 1}


def test_info_json_and_inspection_do_not_change_state(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    assert _send(runner, cli_env, name="alice", message="olá", to="bob").exit_code == 0
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

    before = _agent_state(storage_home)
    info = _info(runner, cli_env, as_json=True)
    assert info.exit_code == 0
    assert info.stderr == ""
    payload = json.loads(info.stdout)
    assert payload["participants"] == 2
    assert payload["messages"] == 1
    assert payload["path"] == str((storage_home / "rooms" / "projectx").resolve())
    assert payload["agents"] == [
        {"name": "alice", "messages_sent": 1},
        {"name": "bob", "messages_sent": 0},
    ]
    assert payload["created_at"].endswith("Z")
    assert _agent_state(storage_home) == before
    assert _counts(storage_home) == {"alice": 1, "bob": 0}


def test_growth_to_four_participants_requires_explicit_recipient(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    assert _send(runner, cli_env, name="alice", message="1", to="bob").exit_code == 0
    assert _send(runner, cli_env, name="carol", message="2", to="dave").exit_code == 0
    omitted = _send(runner, cli_env, name="alice", message="nope")
    assert omitted.exit_code == 1
    assert "more than two participants" in omitted.stderr
    assert _counts(storage_home) == {"alice": 1, "bob": 0, "carol": 1, "dave": 0}

    explicit = _send(runner, cli_env, name="alice", message="3", to="carol")
    assert explicit.exit_code == 0
    assert _counts(storage_home)["alice"] == 2


def test_new_sender_cannot_skip_recipient_by_using_prior_membership(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    assert _send(runner, cli_env, name="alice", message="1", to="bob").exit_code == 0
    rejected = _send(runner, cli_env, name="eve", message="join")
    assert rejected.exit_code == 1
    assert "recipient is required" in rejected.stderr
    assert "Created room" not in rejected.stdout
    assert set(_counts(storage_home)) == {"alice", "bob"}
    database = storage_home / "rooms" / "projectx" / DATABASE_FILENAME
    connection = sqlite3.connect(database)
    try:
        messages = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    finally:
        connection.close()
    assert messages == 1


def test_broadcast_is_one_message_and_reserves_all(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    first_broadcast = _send(runner, cli_env, name="alice", message="anyone?", to="all")
    assert first_broadcast.exit_code == 1
    assert "another known participant" in first_broadcast.stderr
    assert _counts(storage_home) == {}

    assert _send(runner, cli_env, name="alice", message="hello", to="bob").exit_code == 0
    broadcast = _send(runner, cli_env, name="alice", message="everyone", to="all")
    assert broadcast.exit_code == 0
    assert "to 'all'" in broadcast.stdout
    assert "all" not in _counts(storage_home)
    assert _counts(storage_home) == {"alice": 2, "bob": 0}

    reserved_sender = _send(runner, cli_env, name="all", message="nope", to="bob")
    assert reserved_sender.exit_code == 1
    assert "reserved" in reserved_sender.stderr
    assert _counts(storage_home) == {"alice": 2, "bob": 0}


def test_failed_sends_leave_the_room_unchanged(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    missing = _send(runner, cli_env, room="missing", name="alice", message="x", to="bob")
    assert missing.exit_code == 1
    assert "does not exist" in missing.stderr
    assert missing.stdout == ""
    assert not (storage_home / "rooms" / "missing").exists()

    _create(runner, cli_env)
    alone = _send(runner, cli_env, name="alice", message="hello")
    assert alone.exit_code == 1
    assert "recipient is required" in alone.stderr
    assert _counts(storage_home) == {}

    invalid = _send(runner, cli_env, name="alice", message="hello", to="bad name")
    assert invalid.exit_code == 1
    assert "Invalid recipient" in invalid.stderr
    assert _counts(storage_home) == {}
