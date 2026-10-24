"""Deleting a sent message that no other participant has read."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import TextContent
from typer.testing import CliRunner

from ai_note_pair.cli import app
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
    attachment: str | None = None,
):
    args = ["send", "--room", "projectx", "--name", name, "--message", message, "--json"]
    if to is not None:
        args.extend(["--to", to])
    if attachment is not None:
        args.extend(["--attachment", attachment])
    return _invoke(runner, args, cli_env)


def _delete(
    runner: CliRunner,
    cli_env: dict[str, str],
    *,
    name: str,
    message_id: int,
    as_json: bool = False,
):
    args = ["delete-message", "--room", "projectx", "--name", name, "--id", str(message_id)]
    if as_json:
        args.append("--json")
    return _invoke(runner, args, cli_env)


def _read(runner: CliRunner, cli_env: dict[str, str], name: str):
    return _invoke(runner, ["read", "--room", "projectx", "--name", name, "--json"], cli_env)


def _cursors(storage_home: Path) -> dict[str, int]:
    database = storage_home / "rooms" / "projectx" / DATABASE_FILENAME
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute("SELECT name, last_read_message_id FROM agents").fetchall()
    finally:
        connection.close()
    return {name: int(cursor) for name, cursor in rows}


def _message_ids(storage_home: Path) -> list[int]:
    database = storage_home / "rooms" / "projectx" / DATABASE_FILENAME
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute("SELECT id FROM messages ORDER BY id").fetchall()
    finally:
        connection.close()
    return [int(row[0]) for row in rows]


def test_sender_deletes_an_unread_message_without_moving_cursors(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    first = _send(runner, cli_env, name="alice", message="keep", to="bob")
    second = _send(runner, cli_env, name="alice", message="drop", to="bob")
    assert first.exit_code == 0 and second.exit_code == 0
    before = _cursors(storage_home)

    deleted = _delete(runner, cli_env, name="alice", message_id=2, as_json=True)
    assert deleted.exit_code == 0, deleted.stderr
    payload = json.loads(deleted.stdout)
    assert payload["room"] == "projectx"
    assert payload["id"] == 2
    assert payload["sender"] == "alice"
    assert payload["recipient"] == "bob"
    assert payload["timestamp"]
    assert _cursors(storage_home) == before
    assert _message_ids(storage_home) == [1]

    text = _delete(runner, cli_env, name="alice", message_id=1)
    assert text.exit_code == 0, text.stderr
    assert text.stdout.strip() == "Deleted message 1 in room 'projectx' from 'alice' to 'bob'."
    assert _cursors(storage_home) == before

    read = _read(runner, cli_env, "bob")
    assert read.exit_code == 0, read.stderr
    assert json.loads(read.stdout)["messages"] == []


def test_deleted_id_is_not_reused(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    assert _send(runner, cli_env, name="alice", message="gone", to="bob").exit_code == 0
    database = storage_home / "rooms" / "projectx" / DATABASE_FILENAME
    connection = sqlite3.connect(database)
    try:
        connection.execute("DELETE FROM metadata WHERE key = 'next_message_id'")
        connection.commit()
    finally:
        connection.close()

    assert _delete(runner, cli_env, name="alice", message_id=1).exit_code == 0
    reply = _send(runner, cli_env, name="bob", message="still new")
    assert reply.exit_code == 0, reply.stderr
    assert json.loads(reply.stdout)["id"] == 2

    alice = _read(runner, cli_env, "alice")
    assert [item["id"] for item in json.loads(alice.stdout)["messages"]] == [2]


def test_delete_refuses_other_senders_and_messages_already_read(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    assert _send(runner, cli_env, name="alice", message="for bob", to="bob").exit_code == 0
    assert _send(runner, cli_env, name="alice", message="broadcast", to="all").exit_code == 0
    assert _send(runner, cli_env, name="carol", message="joining", to="dave").exit_code == 0

    stolen = _delete(runner, cli_env, name="bob", message_id=1)
    assert stolen.exit_code == 1
    assert "sent by 'alice'" in stolen.stderr
    assert stolen.stdout == ""
    assert _message_ids(storage_home) == [1, 2, 3]

    bystander = _read(runner, cli_env, "carol")
    assert bystander.exit_code == 0
    blocked = _delete(runner, cli_env, name="alice", message_id=1)
    assert blocked.exit_code == 1
    assert "already been read by 'carol'" in blocked.stderr
    assert _message_ids(storage_home) == [1, 2, 3]

    dave = _read(runner, cli_env, "dave")
    assert dave.exit_code == 0
    both = _delete(runner, cli_env, name="alice", message_id=2)
    assert both.exit_code == 1
    assert "'carol', 'dave'" in both.stderr

    missing = _delete(runner, cli_env, name="alice", message_id=9)
    assert missing.exit_code == 1
    assert "does not exist" in missing.stderr
    invalid = _delete(runner, cli_env, name="alice", message_id=0)
    assert invalid.exit_code == 1
    assert "Invalid message id" in invalid.stderr
    assert _message_ids(storage_home) == [1, 2, 3]


def test_delete_removes_only_that_messages_attachments(
    runner: CliRunner, tmp_path: Path, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    notes = tmp_path / "notes.txt"
    extra = tmp_path / "extra.md"
    notes.write_text("notes", encoding="utf-8")
    extra.write_text("extra", encoding="utf-8")
    assert (
        _send(
            runner,
            cli_env,
            name="alice",
            message="with notes",
            to="bob",
            attachment=str(notes),
        ).exit_code
        == 0
    )
    assert (
        _send(
            runner,
            cli_env,
            name="alice",
            message="with extra",
            to="bob",
            attachment=str(extra),
        ).exit_code
        == 0
    )
    room_attachments = storage_home / "rooms" / "projectx" / "attachments"

    refused = _read(runner, cli_env, "bob")
    assert refused.exit_code == 0
    kept = _delete(runner, cli_env, name="alice", message_id=2)
    assert kept.exit_code == 1
    assert (room_attachments / "extra.md").is_file()

    fresh = tmp_path / "fresh.txt"
    fresh.write_text("fresh", encoding="utf-8")
    assert (
        _send(
            runner,
            cli_env,
            name="alice",
            message="unread file",
            to="bob",
            attachment=str(fresh),
        ).exit_code
        == 0
    )
    deleted = _delete(runner, cli_env, name="alice", message_id=3)
    assert deleted.exit_code == 0, deleted.stderr
    assert not (room_attachments / "fresh.txt").exists()
    assert (room_attachments / "notes.txt").is_file()
    assert (room_attachments / "extra.md").is_file()
    assert _message_ids(storage_home) == [1, 2]


def test_delete_message_over_stdio(tmp_path: Path) -> None:
    home = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()
    script = Path(sys.executable).with_name("ai-note-pair-mcp")

    async def scenario() -> None:
        params = StdioServerParameters(
            command=str(script),
            cwd=outside,
            env={"AI_NOTE_PAIR_HOME": str(home)},
        )
        async with Client(params) as alice:
            assert (await alice.call_tool("create_room", {"name": "projectx"})).is_error is False
            sent = await alice.call_tool(
                "send_message",
                {"room": "projectx", "name": "alice", "to": "bob", "message": "withdraw"},
            )
            assert sent.structured_content is not None
            removed = await alice.call_tool(
                "delete_message",
                {
                    "room": "projectx",
                    "name": "alice",
                    "message_id": sent.structured_content["id"],
                },
            )
            assert removed.is_error is False
            assert removed.structured_content is not None
            assert removed.structured_content["id"] == 1
            assert removed.structured_content["sender"] == "alice"
            read = await alice.call_tool("read_messages", {"room": "projectx", "name": "bob"})
            assert read.structured_content is not None
            assert read.structured_content["messages"] == []

            again = await alice.call_tool(
                "delete_message",
                {"room": "projectx", "name": "alice", "message_id": 1},
            )
            assert again.is_error is True
            content = getattr(again, "content")
            text = "\n".join(block.text for block in content if isinstance(block, TextContent))
            assert "does not exist" in text

    anyio.run(scenario)
