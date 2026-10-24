"""Joining a room and choosing whether to catch up on a large backlog."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from typer.testing import CliRunner

from ai_note_pair.cli import app
from ai_note_pair.messaging import LARGE_UNREAD_CHARACTER_COUNT, send_message
from ai_note_pair.rooms import create_room
from ai_note_pair.storage import DATABASE_FILENAME


def _invoke(runner: CliRunner, args: list[str], cli_env: dict[str, str]):
    return runner.invoke(app, args, env=cli_env)


def _create(runner: CliRunner, cli_env: dict[str, str]) -> None:
    result = _invoke(runner, ["create-room", "--name", "projectx"], cli_env)
    assert result.exit_code == 0, result.stderr


def _cursor(storage_home: Path, name: str) -> int | None:
    database = storage_home / "rooms" / "projectx" / DATABASE_FILENAME
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


def test_join_registers_without_reading_and_counts_as_a_participant(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    send_message("projectx", "alice", "hello", "bob", storage_home)
    joined = _invoke(runner, ["join", "--room", "projectx", "--name", "carol", "--json"], cli_env)
    assert joined.exit_code == 0, joined.stderr
    assert json.loads(joined.stdout) == {"room": "projectx", "name": "carol", "joined": True}
    assert _cursor(storage_home, "carol") == 0

    again = _invoke(runner, ["join", "--room", "projectx", "--name", "carol"], cli_env)
    assert again.exit_code == 0, again.stderr
    assert "already in room 'projectx'" in again.stdout
    assert _cursor(storage_home, "carol") == 0

    omitted = _invoke(
        runner,
        ["send", "--room", "projectx", "--name", "alice", "--message", "later"],
        cli_env,
    )
    assert omitted.exit_code == 1
    assert "more than two participants" in omitted.stderr

    read = _invoke(runner, ["read", "--room", "projectx", "--name", "carol", "--json"], cli_env)
    assert read.exit_code == 0, read.stderr
    payload = json.loads(read.stdout)
    assert payload["choice_required"] is False
    assert payload["notice"] == ""
    assert [item["content"] for item in payload["messages"]] == ["hello"]
    assert _cursor(storage_home, "carol") == 1


def test_many_messages_are_withheld_until_the_reader_chooses(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    for number in range(21):
        send_message("projectx", "alice", "x", "bob", storage_home)
    withheld_read = _invoke(
        runner, ["read", "--room", "projectx", "--name", "bob", "--json"], cli_env
    )
    assert withheld_read.exit_code == 0, withheld_read.stderr
    withheld = json.loads(withheld_read.stdout)
    assert withheld["choice_required"] is True
    assert withheld["messages"] == []
    assert withheld["unread"] == 21
    assert withheld["characters"] == 21
    assert "Nothing was marked read" in withheld["notice"]
    assert _cursor(storage_home, "bob") == 0

    text = _invoke(runner, ["read", "--room", "projectx", "--name", "bob"], cli_env)
    assert text.exit_code == 0
    assert "21 unread messages, 21 characters." in text.stdout
    assert _cursor(storage_home, "bob") == 0


def test_twenty_short_messages_are_returned(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    for number in range(20):
        send_message("projectx", "alice", "x", "bob", storage_home)
    read = _invoke(runner, ["read", "--room", "projectx", "--name", "bob", "--json"], cli_env)
    assert read.exit_code == 0, read.stderr
    payload = json.loads(read.stdout)
    assert payload["choice_required"] is False
    assert len(payload["messages"]) == 20
    assert _cursor(storage_home, "bob") == 20


def test_long_text_is_withheld_until_read_all_or_mark_read(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    send_message("projectx", "alice", "x" * LARGE_UNREAD_CHARACTER_COUNT, "bob", storage_home)
    included = _invoke(runner, ["read", "--room", "projectx", "--name", "bob", "--json"], cli_env)
    assert json.loads(included.stdout)["choice_required"] is False
    assert _cursor(storage_home, "bob") == 1

    send_message("projectx", "alice", "y" * (LARGE_UNREAD_CHARACTER_COUNT + 1), "bob", storage_home)
    withheld = _invoke(runner, ["read", "--room", "projectx", "--name", "bob", "--json"], cli_env)
    payload = json.loads(withheld.stdout)
    assert payload["choice_required"] is True
    assert payload["messages"] == []
    assert payload["characters"] == LARGE_UNREAD_CHARACTER_COUNT + 1
    assert _cursor(storage_home, "bob") == 1

    caught_up = _invoke(
        runner, ["mark-read", "--room", "projectx", "--name", "bob", "--json"], cli_env
    )
    assert caught_up.exit_code == 0, caught_up.stderr
    marked = json.loads(caught_up.stdout)
    assert marked["skipped"] == 1
    assert marked["marked_through"] == 2
    assert _cursor(storage_home, "bob") == 2

    send_message("projectx", "alice", "after", "bob", storage_home)
    later = _invoke(runner, ["read", "--room", "projectx", "--name", "bob", "--json"], cli_env)
    assert [item["content"] for item in json.loads(later.stdout)["messages"]] == ["after"]
    assert _cursor(storage_home, "bob") == 3


def test_read_all_returns_a_large_backlog_and_marks_it_read(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    send_message("projectx", "alice", "z" * (LARGE_UNREAD_CHARACTER_COUNT + 1), "bob", storage_home)
    read = _invoke(
        runner, ["read", "--room", "projectx", "--name", "bob", "--all", "--json"], cli_env
    )
    assert read.exit_code == 0, read.stderr
    payload = json.loads(read.stdout)
    assert payload["choice_required"] is False
    assert payload["messages"][0]["content"].startswith("z")
    assert len(payload["messages"][0]["content"]) == LARGE_UNREAD_CHARACTER_COUNT + 1
    assert _cursor(storage_home, "bob") == 1

    empty = _invoke(runner, ["mark-read", "--room", "projectx", "--name", "bob"], cli_env)
    assert empty.stdout.strip() == "No unread messages."


def test_join_and_choice_over_stdio(tmp_path: Path) -> None:
    home = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()
    script = Path(sys.executable).with_name("ai-note-pair-mcp")
    create_room("projectx", home)
    for number in range(21):
        send_message("projectx", "alice", f"note {number}", "bob", home)

    async def scenario() -> None:
        params = StdioServerParameters(
            command=str(script),
            cwd=outside,
            env={"AI_NOTE_PAIR_HOME": str(home)},
        )
        async with Client(params) as client:
            joined = await client.call_tool("join_room", {"room": "projectx", "name": "carol"})
            assert joined.structured_content == {
                "room": "projectx",
                "name": "carol",
                "joined": True,
            }
            withheld = await client.call_tool(
                "read_messages", {"room": "projectx", "name": "carol"}
            )
            body = withheld.structured_content
            assert body is not None
            assert body["choice_required"] is True
            assert body["unread"] == 21
            assert body["messages"] == []
            assert _cursor(home, "carol") == 0
            marked = await client.call_tool("mark_read", {"room": "projectx", "name": "carol"})
            assert marked.structured_content is not None
            assert marked.structured_content["skipped"] == 21
            assert _cursor(home, "carol") == 21

    anyio.run(scenario)
