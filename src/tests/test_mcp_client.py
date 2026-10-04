"""Installed stdio server used the way a client would."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import anyio
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

import ai_note_pair
from ai_note_pair.storage import DATABASE_FILENAME


def _script() -> Path:
    script = Path(sys.executable).with_name("ai-note-pair-mcp")
    assert script.is_file(), script
    return script


def _server(home: Path, cwd: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=str(_script()),
        cwd=cwd,
        env={"AI_NOTE_PAIR_HOME": str(home)},
    )


@pytest.mark.parametrize("option", ["--help", "-h", "--version", "--unknown"])
def test_server_command_options_exit_without_serving(tmp_path: Path, option: str) -> None:
    # Keep stdin open: a server accidentally started here would wait and time out.
    process = subprocess.Popen(
        [str(_script()), option],
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        process.wait(timeout=10)
        stdout, stderr = process.communicate(timeout=2)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    if option == "--unknown":
        assert process.returncode == 2
        assert stdout == ""
        assert "unrecognized arguments: --unknown" in stderr
    else:
        assert process.returncode == 0
        assert stderr == ""
        if option == "--version":
            assert stdout.strip() == f"ai-note-pair-mcp {ai_note_pair.__version__}"
        else:
            assert "usage: ai-note-pair-mcp" in stdout
            assert "AI_NOTE_PAIR_HOME" in stdout
            assert "--version" in stdout
            assert f"Executable: {_script().absolute()}" in stdout


def test_package_and_entry_point_stay_under_src() -> None:
    package = Path(ai_note_pair.__file__).resolve().parent
    src = Path(__file__).resolve().parents[1]
    repo = src.parent
    assert package.is_relative_to(src)
    assert not (package / "SPEC.md").exists()
    script = _script().read_text(encoding="utf-8")
    assert "from ai_note_pair.mcp_server import main" in script
    assert _script().resolve().is_relative_to(src)
    for name in ("README.md", "SPEC.md", "USAGE.md", "PLAN.md"):
        assert name not in script
        assert not (package / name).exists()
    assert (repo / "SPEC.md").is_file()


def test_installed_client_completes_the_shared_room_flow(tmp_path: Path) -> None:
    home = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()
    note = outside / "note.txt"
    note.write_text("payload", encoding="utf-8")

    async def scenario() -> None:
        params = _server(home, outside)
        async with Client(params) as alice, Client(params) as bob:
            listed = await alice.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            assert "discover the name" in tools["room_info"].description
            assert "when the user" in tools["read_messages"].description
            assert "Consume the pending" in tools["send_message"].description

            created = await alice.call_tool("create_room", {"name": "projectx"})
            assert created.is_error is False
            sent = await alice.call_tool(
                "send_message",
                {"room": "projectx", "name": "alice", "to": "bob", "message": "hello bob"},
            )
            assert sent.is_error is False
            assert sent.structured_content is not None
            assert sent.structured_content["pending"] == []

            info = await bob.call_tool("room_info", {"room": "projectx"})
            assert info.structured_content is not None
            agents = {
                agent["name"]: agent["messages_sent"] for agent in info.structured_content["agents"]
            }
            assert agents == {"alice": 1, "bob": 0}
            room = Path(info.structured_content["path"])

            reply = await bob.call_tool(
                "send_message",
                {"room": "projectx", "name": "bob", "message": "hello alice"},
            )
            assert reply.structured_content is not None
            assert reply.structured_content["recipient"] == "alice"
            assert [item["id"] for item in reply.structured_content["pending"]] == [1]

            caught_up = await alice.call_tool(
                "send_message",
                {"room": "projectx", "name": "alice", "message": "thanks"},
            )
            assert caught_up.structured_content is not None
            assert caught_up.structured_content["recipient"] == "bob"
            assert [item["content"] for item in caught_up.structured_content["pending"]] == [
                "hello alice"
            ]

            joined = await alice.call_tool(
                "send_message",
                {"room": "projectx", "name": "carol", "to": "dave", "message": "four now"},
            )
            assert joined.is_error is False
            needs_recipient = await bob.call_tool(
                "send_message",
                {"room": "projectx", "name": "bob", "message": "which?"},
            )
            assert needs_recipient.is_error is True

            dave = await bob.call_tool("read_messages", {"room": "projectx", "name": "dave"})
            assert dave.structured_content is not None
            assert [item["id"] for item in dave.structured_content["messages"]] == [1, 2, 3, 4]
            alice_before = await alice.call_tool(
                "read_messages",
                {"room": "projectx", "name": "alice"},
            )
            assert alice_before.structured_content is not None
            assert [item["id"] for item in alice_before.structured_content["messages"]] == [4]

            attached = await alice.call_tool(
                "send_message",
                {
                    "room": "projectx",
                    "name": "alice",
                    "to": "bob",
                    "message": "see file",
                    "attachments": [str(note)],
                },
            )
            assert attached.is_error is False
            shared = await bob.call_tool("read_messages", {"room": "projectx", "name": "bob"})
            assert shared.structured_content is not None
            messages = shared.structured_content["messages"]
            assert [item["id"] for item in messages] == [3, 4, 5]
            assert messages[-1]["attachments"] == ["attachments/note.txt"]
            assert (room / "attachments" / "note.txt").read_text(encoding="utf-8") == "payload"
            carol = await alice.call_tool("read_messages", {"room": "projectx", "name": "carol"})
            assert carol.structured_content is not None
            assert [item["id"] for item in carol.structured_content["messages"]] == [5]

            archived = await alice.call_tool("archive_room", {"room": "projectx"})
            assert archived.is_error is False
            assert archived.structured_content is not None
            archive = Path(archived.structured_content["path"])
            assert (archive / "attachments" / "note.txt").is_file()
            assert (archive / DATABASE_FILENAME).is_file()

            reused = await bob.call_tool("create_room", {"name": "projectx"})
            assert reused.is_error is False
            fresh = await bob.call_tool("room_info", {"room": "projectx"})
            assert fresh.structured_content is not None
            assert fresh.structured_content["messages"] == 0
            assert fresh.structured_content["participants"] == 0
            names = await alice.call_tool("list_rooms", {"archived": True})
            assert names.structured_content == {
                "archived": True,
                "rooms": [archived.structured_content["name"]],
            }

    anyio.run(scenario)
