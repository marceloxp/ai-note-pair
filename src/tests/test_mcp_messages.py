"""Transport-level send and read delivery for the MCP server."""

from __future__ import annotations

import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path

import anyio
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.shared.exceptions import MCPError
from mcp.types import TextContent

from ai_note_pair.mcp_delivery import _HOLD_ENV
from ai_note_pair.storage import DATABASE_FILENAME


def _script() -> Path:
    script = Path(sys.executable).with_name("ai-note-pair-mcp")
    assert script.is_file(), script
    return script


def _server(home: Path, cwd: Path, *, hold: Path | None = None) -> StdioServerParameters:
    env = {"AI_NOTE_PAIR_HOME": str(home)}
    if hold is not None:
        env[_HOLD_ENV] = str(hold)
    return StdioServerParameters(command=str(_script()), cwd=cwd, env=env)


def _text(result: object) -> str:
    content = getattr(result, "content")
    return "\n".join(block.text for block in content if isinstance(block, TextContent))


def _wait_cursor(home: Path, name: str, expected: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if _cursor(home, name) == expected:
            return
        time.sleep(0.02)
    assert _cursor(home, name) == expected


def _cursor(home: Path, name: str) -> int | None:
    database = home / "rooms" / "projectx" / DATABASE_FILENAME
    if not database.is_file():
        return None
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


def _message_count(home: Path) -> int:
    database = home / "rooms" / "projectx" / DATABASE_FILENAME
    connection = sqlite3.connect(database)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
    finally:
        connection.close()


def test_send_and_read_deliver_shared_history_over_stdio(tmp_path: Path) -> None:
    home = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()

    async def scenario() -> None:
        params = _server(home, outside)
        async with Client(params) as alice, Client(params) as bob:
            created = await alice.call_tool("create_room", {"name": "projectx"})
            assert created.is_error is False
            first = await alice.call_tool(
                "send_message",
                {"room": "projectx", "name": "alice", "to": "bob", "message": "linha\nação 🤖"},
            )
            assert first.is_error is False
            assert first.structured_content is not None
            assert first.structured_content["id"] == 1
            assert first.structured_content["pending"] == []
            assert "linha" not in _text(first)
            _wait_cursor(home, "alice", 1)
            assert _cursor(home, "bob") == 0

            inferred = await bob.call_tool(
                "send_message",
                {"room": "projectx", "name": "bob", "message": "reply"},
            )
            assert inferred.is_error is False
            receipt = inferred.structured_content
            assert receipt is not None
            assert receipt["recipient"] == "alice"
            assert [item["id"] for item in receipt["pending"]] == [1]
            assert receipt["pending"][0]["content"] == "linha\nação 🤖"
            assert "reply" not in _text(inferred)
            _wait_cursor(home, "bob", 2)
            assert _cursor(home, "alice") == 1

            broadcast = await alice.call_tool(
                "send_message",
                {"room": "projectx", "name": "alice", "to": "all", "message": "everyone"},
            )
            assert broadcast.is_error is False
            assert broadcast.structured_content is not None
            assert [item["id"] for item in broadcast.structured_content["pending"]] == [2]
            _wait_cursor(home, "alice", 3)
            assert _cursor(home, "bob") == 2

            joined = await alice.call_tool(
                "send_message",
                {"room": "projectx", "name": "carol", "to": "dave", "message": "for dave"},
            )
            assert joined.is_error is False
            omitted = await bob.call_tool(
                "send_message",
                {"room": "projectx", "name": "bob", "message": "which?"},
            )
            assert omitted.is_error is True
            assert "more than two participants" in _text(omitted)
            assert _cursor(home, "bob") == 2
            still_pending = await bob.call_tool(
                "read_messages",
                {"room": "projectx", "name": "bob"},
            )
            assert still_pending.structured_content is not None
            assert [item["id"] for item in still_pending.structured_content["messages"]] == [3, 4]
            _wait_cursor(home, "bob", 4)
            assert _cursor(home, "alice") == 3

            dave = await alice.call_tool(
                "read_messages",
                {"room": "projectx", "name": "dave"},
            )
            assert dave.is_error is False
            dave_body = dave.structured_content
            assert dave_body is not None
            assert [item["recipient"] for item in dave_body["messages"]] == [
                "bob",
                "alice",
                "all",
                "dave",
            ]
            _wait_cursor(home, "dave", 4)
            assert _cursor(home, "carol") == 4
            assert _cursor(home, "alice") == 3

            empty = await alice.call_tool(
                "read_messages",
                {"room": "projectx", "name": "dave"},
            )
            assert empty.structured_content is not None
            assert empty.structured_content["messages"] == []
            assert empty.structured_content["reader"] == "dave"
            assert _cursor(home, "dave") == 4

    anyio.run(scenario)


def test_later_arrival_stays_unread_when_delivery_is_held(tmp_path: Path) -> None:
    home = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()
    hold = tmp_path / "hold"
    hold.write_text("wait", encoding="utf-8")

    async def scenario() -> None:
        open_params = _server(home, outside)
        held = _server(home, outside, hold=hold)
        async with Client(open_params) as alice, Client(held) as bob:
            assert (await alice.call_tool("create_room", {"name": "projectx"})).is_error is False
            first = await alice.call_tool(
                "send_message",
                {"room": "projectx", "name": "alice", "to": "bob", "message": "first"},
            )
            assert first.is_error is False

            receipt: dict[str, object] = {}

            async def reply() -> None:
                receipt.update(await _reply(bob))

            async with anyio.create_task_group() as tasks:
                tasks.start_soon(reply)
                await _wait_until(lambda: _message_count(home) == 2)
                assert _cursor(home, "bob") == 0
                late = await alice.call_tool(
                    "send_message",
                    {"room": "projectx", "name": "alice", "to": "bob", "message": "late"},
                )
                assert late.is_error is False
                hold.unlink()
            assert receipt["id"] == 2
            assert [item["content"] for item in receipt["pending"]] == ["first"]
            _wait_cursor(home, "bob", 2)
            follow_up = await bob.call_tool(
                "read_messages",
                {"room": "projectx", "name": "bob"},
            )
            assert follow_up.structured_content is not None
            follow_messages = follow_up.structured_content["messages"]
            assert [item["content"] for item in follow_messages] == ["late"]

    anyio.run(scenario)


async def _reply(bob: Client) -> dict[str, object]:
    result = await bob.call_tool(
        "send_message",
        {"room": "projectx", "name": "bob", "message": "reply"},
    )
    assert result.is_error is False
    assert result.structured_content is not None
    return result.structured_content


async def _wait_until(ready: Callable[[], bool]) -> None:
    deadline = anyio.current_time() + 5
    while anyio.current_time() < deadline:
        if ready():
            return
        await anyio.sleep(0.05)
    raise AssertionError("timed out waiting for the held send to commit")


def test_cancellation_before_delivery_leaves_pending_messages(tmp_path: Path) -> None:
    home = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()
    hold = tmp_path / "hold"
    hold.write_text("wait", encoding="utf-8")

    async def scenario() -> None:
        async with Client(_server(home, outside)) as setup:
            assert (await setup.call_tool("create_room", {"name": "projectx"})).is_error is False
            sent = await setup.call_tool(
                "send_message",
                {"room": "projectx", "name": "alice", "to": "bob", "message": "pending"},
            )
            assert sent.is_error is False

        async with Client(_server(home, outside, hold=hold)) as bob:
            with pytest.raises(MCPError):
                await bob.call_tool(
                    "read_messages",
                    {"room": "projectx", "name": "bob"},
                    read_timeout_seconds=0.4,
                )
            assert _cursor(home, "bob") == 0
            # Round-trip after the cancellation notification so the server has
            # processed it before the held handler is allowed to return.
            assert (await bob.call_tool("room_info", {"room": "projectx"})).is_error is False
            hold.unlink()
            await anyio.sleep(0.3)
            assert _cursor(home, "bob") == 0
            recovered = await bob.call_tool(
                "read_messages",
                {"room": "projectx", "name": "bob"},
            )
            assert recovered.is_error is False
            assert recovered.structured_content is not None
            assert [item["content"] for item in recovered.structured_content["messages"]] == [
                "pending"
            ]

    anyio.run(scenario)


def test_cancelled_blocked_response_is_already_acknowledged(tmp_path: Path) -> None:
    """A real pipe blocks a large response before flush; its cursor is already current."""
    import json
    import os
    import selectors
    import subprocess

    from ai_note_pair.messaging import send_message
    from ai_note_pair.rooms import create_room

    home = tmp_path / "storage"
    create_room("projectx", home)
    send_message("projectx", "alice", "x" * 1_000_000, "bob", home)
    process = subprocess.Popen(
        [str(_script())],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "AI_NOTE_PAIR_HOME": str(home)},
        bufsize=0,
    )
    assert process.stdin is not None and process.stdout is not None

    def send(payload: dict[str, object]) -> None:
        assert process.stdin is not None
        process.stdin.write((json.dumps(payload) + "\n").encode())
        process.stdin.flush()

    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "delivery-test", "version": "1"},
                    },
                }
            )
            response = b""
            deadline = time.monotonic() + 10
            while not response.endswith(b"\n"):
                assert time.monotonic() < deadline, "Initialization timed out"
                if selector.select(0.2):
                    chunk = os.read(process.stdout.fileno(), 65536)
                    assert chunk, "Server closed during initialization"
                    response += chunk
            assert "result" in json.loads(response)
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "read_messages",
                        "arguments": {"room": "projectx", "name": "bob"},
                    },
                }
            )
            # Do not drain the large response: the pipe cannot hold it, so flush
            # cannot complete. Read state must already have advanced.
            assert selector.select(10), "Response did not start"
            assert _cursor(home, "bob") == 1
            assert _cursor(home, "alice") == 0
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"requestId": 2, "reason": "cancel blocked output"},
                }
            )
            process.stdout.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
    assert _cursor(home, "bob") == 1
    assert _cursor(home, "alice") == 0
