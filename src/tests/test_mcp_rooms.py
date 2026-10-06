"""Protocol tests for the stdio MCP room tools."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import anyio
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import TextContent

from ai_note_pair.archive import archive_room


def _script() -> Path:
    script = Path(sys.executable).with_name("ai-note-pair-mcp")
    assert script.is_file(), script
    return script


def _server(home: Path, cwd: Path, *, use_home_env: bool = True) -> StdioServerParameters:
    env = {"AI_NOTE_PAIR_HOME": str(home)} if use_home_env else None
    return StdioServerParameters(command=str(_script()), cwd=cwd, env=env)


def _text(result: object) -> str:
    content = getattr(result, "content")
    return "\n".join(block.text for block in content if isinstance(block, TextContent))


def test_installed_server_discovers_and_operates_rooms_outside_the_repository(
    tmp_path: Path,
) -> None:
    home = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()

    async def scenario() -> None:
        params = _server(home, outside)
        async with Client(params) as client:
            assert client.server_info is not None
            assert client.server_info.name == "ai-note-pair"
            assert client.instructions
            listed = await client.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            assert set(tools) == {
                "archive_room",
                "check_messages",
                "create_room",
                "list_rooms",
                "read_messages",
                "room_info",
                "send_message",
            }
            assert "ctx" not in tools["send_message"].input_schema["properties"]
            assert "ctx" not in tools["read_messages"].input_schema["properties"]
            for tool in tools.values():
                assert tool.description
                assert tool.input_schema["type"] == "object"
                assert tool.output_schema is not None
            assert tools["list_rooms"].input_schema["properties"]["archived"]["default"] is False

            invalid = await client.call_tool("create_room", {"name": "bad name"})
            assert invalid.is_error is True
            assert invalid.structured_content is None
            assert "Invalid room" in _text(invalid)

            created = await client.call_tool("create_room", {"name": "projectx"})
            assert created.is_error is False
            assert created.structured_content is not None
            assert created.structured_content["name"] == "projectx"
            room_path = Path(created.structured_content["path"])
            assert room_path == (home / "rooms" / "projectx").resolve()
            assert "projectx" in _text(created)

            duplicate = await client.call_tool("create_room", {"name": "projectx"})
            assert duplicate.is_error is True
            assert "already exists" in _text(duplicate)

            active = await client.call_tool("list_rooms", {})
            assert active.structured_content == {"archived": False, "rooms": ["projectx"]}

            missing = await client.call_tool("room_info", {"room": "missing"})
            assert missing.is_error is True
            assert "does not exist" in _text(missing)

            info = await client.call_tool("room_info", {"room": "projectx"})
            assert info.is_error is False
            details = info.structured_content
            assert details is not None
            assert details["participants"] == 0
            assert details["messages"] == 0
            assert details["agents"] == []
            assert details["path"] == created.structured_content["path"]
            assert details["created_at"].endswith("Z")

    anyio.run(scenario)


def test_two_server_processes_share_storage_and_archived_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("AI_NOTE_PAIR_HOME", str(home))

    async def scenario() -> None:
        params = _server(home, outside)
        async with Client(params) as left, Client(params) as right:
            created = await left.call_tool("create_room", {"name": "shared"})
            assert created.is_error is False
            seen = await right.call_tool("list_rooms", {})
            assert seen.structured_content == {"archived": False, "rooms": ["shared"]}
            info = await right.call_tool("room_info", {"room": "shared"})
            assert info.is_error is False
            assert info.structured_content is not None
            assert info.structured_content["room"] == "shared"

    anyio.run(scenario)
    destination = archive_room("shared", home)
    archived = anyio.run(_archived_names, home, outside)
    assert archived == {"archived": True, "rooms": [destination]}
    active = anyio.run(_active_names, home, outside)
    assert active == {"archived": False, "rooms": []}


async def _archived_names(home: Path, outside: Path) -> dict[str, object]:
    async with Client(_server(home, outside)) as client:
        result = await client.call_tool("list_rooms", {"archived": True})
        assert result.is_error is False
        assert result.structured_content is not None
        return result.structured_content


async def _active_names(home: Path, outside: Path) -> dict[str, object]:
    async with Client(_server(home, outside)) as client:
        result = await client.call_tool("list_rooms", {})
        assert result.is_error is False
        assert result.structured_content is not None
        return result.structured_content


def test_default_storage_uses_the_home_config_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("AI_NOTE_PAIR_HOME", raising=False)

    async def scenario() -> None:
        async with Client(_server(home, outside, use_home_env=False)) as client:
            created = await client.call_tool("create_room", {"name": "projectx"})
            assert created.is_error is False

    anyio.run(scenario)
    room = home / ".config" / "ai-note-pair" / "rooms" / "projectx"
    assert (room / "ai-note-pair.db").is_file()


def test_server_stdout_carries_protocol_messages_only(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    completed = subprocess.run(
        [_script()],
        cwd=outside,
        input="",
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        env={"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin"},
    )
    assert completed.returncode == 0
    assert completed.stdout == ""
    assert "Traceback" not in completed.stderr
    for line in completed.stderr.splitlines():
        if line.startswith("{"):
            json.loads(line)
