"""Attachments, archiving, and CLI/MCP consistency over stdio."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import TextContent
from typer.testing import CliRunner

from ai_note_pair.cli import app
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


def _cli(home: Path, args: list[str]):
    return CliRunner().invoke(app, args, env={"AI_NOTE_PAIR_HOME": str(home)})


def _cursor(database: Path, name: str) -> int | None:
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


def _message_count(database: Path) -> int:
    connection = sqlite3.connect(database)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
    finally:
        connection.close()


def test_attachments_round_trip_and_failed_copies_leave_no_message(tmp_path: Path) -> None:
    home = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()
    first = tmp_path / "a" / "notes.txt"
    second = tmp_path / "b" / "notes.txt"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    missing = tmp_path / "missing.txt"

    async def scenario() -> None:
        async with Client(_server(home, outside)) as client:
            created = await client.call_tool("create_room", {"name": "projectx"})
            assert created.is_error is False
            sent = await client.call_tool(
                "send_message",
                {
                    "room": "projectx",
                    "name": "alice",
                    "to": "bob",
                    "message": "see files",
                    "attachments": [str(first), str(second)],
                },
            )
            assert sent.is_error is False
            info = await client.call_tool("room_info", {"room": "projectx"})
            assert info.structured_content is not None
            room = Path(info.structured_content["path"])
            assert (room / "attachments" / "notes.txt").read_text(encoding="utf-8") == "first"
            assert (room / "attachments" / "notes-2.txt").read_text(encoding="utf-8") == "second"

            read = await client.call_tool("read_messages", {"room": "projectx", "name": "bob"})
            assert read.structured_content is not None
            attachments = read.structured_content["messages"][0]["attachments"]
            assert attachments == ["attachments/notes.txt", "attachments/notes-2.txt"]
            assert "notes.txt" in _text(read)

            failed = await client.call_tool(
                "send_message",
                {
                    "room": "projectx",
                    "name": "alice",
                    "to": "bob",
                    "message": "nope",
                    "attachments": [str(first), str(missing)],
                },
            )
            assert failed.is_error is True
            assert "does not exist" in _text(failed)
            assert _message_count(room / DATABASE_FILENAME) == 1
            assert sorted(path.name for path in (room / "attachments").iterdir()) == [
                "notes-2.txt",
                "notes.txt",
            ]

            blocked = room / "attachments"
            blocked.chmod(0o555)
            try:
                denied = await client.call_tool(
                    "send_message",
                    {
                        "room": "projectx",
                        "name": "alice",
                        "to": "bob",
                        "message": "blocked",
                        "attachments": [str(first)],
                    },
                )
            finally:
                blocked.chmod(0o755)
            assert denied.is_error is True
            assert "Could not copy attachment" in _text(denied)
            assert _message_count(room / DATABASE_FILENAME) == 1

    anyio.run(scenario)


def test_cli_and_mcp_share_messages_and_read_state(tmp_path: Path) -> None:
    home = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()

    async def scenario() -> None:
        async with Client(_server(home, outside)) as client:
            assert (await client.call_tool("create_room", {"name": "projectx"})).is_error is False
            sent = await client.call_tool(
                "send_message",
                {"room": "projectx", "name": "alice", "to": "bob", "message": "from mcp"},
            )
            assert sent.is_error is False

            info = _cli(home, ["info", "--room", "projectx", "--json"])
            assert info.exit_code == 0, info.stderr
            assert '"messages": 1' in info.stdout
            assert '"name": "alice"' in info.stdout
            assert '"name": "bob"' in info.stdout

            read = _cli(home, ["read", "--room", "projectx", "--name", "bob", "--json"])
            assert read.exit_code == 0, read.stderr
            assert "from mcp" in read.stdout

            again = await client.call_tool("read_messages", {"room": "projectx", "name": "bob"})
            assert again.structured_content is not None
            assert again.structured_content["messages"] == []

            reply = _cli(
                home,
                [
                    "send",
                    "--room",
                    "projectx",
                    "--name",
                    "bob",
                    "--message",
                    "from cli",
                ],
            )
            assert reply.exit_code == 0, reply.stderr
            pending = await client.call_tool(
                "read_messages",
                {"room": "projectx", "name": "alice"},
            )
            assert pending.structured_content is not None
            assert [item["content"] for item in pending.structured_content["messages"]] == [
                "from cli"
            ]
            empty = _cli(home, ["read", "--room", "projectx", "--name", "alice", "--json"])
            assert empty.exit_code == 0, empty.stderr
            assert '"messages": []' in empty.stdout

    anyio.run(scenario)


def test_archive_preserves_files_and_frees_the_name(tmp_path: Path) -> None:
    home = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()
    source = tmp_path / "note.txt"
    source.write_text("payload", encoding="utf-8")

    async def scenario() -> None:
        async with Client(_server(home, outside)) as client:
            assert (await client.call_tool("create_room", {"name": "projectx"})).is_error is False
            sent = await client.call_tool(
                "send_message",
                {
                    "room": "projectx",
                    "name": "alice",
                    "to": "bob",
                    "message": "keep",
                    "attachments": [str(source)],
                },
            )
            assert sent.is_error is False
            read = await client.call_tool("read_messages", {"room": "projectx", "name": "bob"})
            assert read.is_error is False

            archived = await client.call_tool("archive_room", {"room": "projectx"})
            assert archived.is_error is False
            body = archived.structured_content
            assert body is not None
            assert body["room"] == "projectx"
            assert body["name"].startswith("projectx-")
            archive = Path(body["path"])
            assert archive == (home / "archived" / body["name"]).resolve()
            assert (archive / "attachments" / "note.txt").read_text(encoding="utf-8") == "payload"
            database = archive / DATABASE_FILENAME
            assert _message_count(database) == 1
            assert _cursor(database, "bob") == 1
            assert _cursor(database, "alice") == 1
            assert not (home / "rooms" / "projectx").exists()

            listed = await client.call_tool("list_rooms", {"archived": True})
            assert listed.structured_content == {"archived": True, "rooms": [body["name"]]}
            active = await client.call_tool("list_rooms", {})
            assert active.structured_content == {"archived": False, "rooms": []}

            created = await client.call_tool("create_room", {"name": "projectx"})
            assert created.is_error is False
            info = await client.call_tool("room_info", {"room": "projectx"})
            assert info.structured_content is not None
            assert info.structured_content["messages"] == 0
            assert info.structured_content["participants"] == 0
            assert (archive / DATABASE_FILENAME).is_file()

            missing = await client.call_tool("archive_room", {"room": "absent"})
            assert missing.is_error is True
            assert "does not exist" in _text(missing)

    anyio.run(scenario)


def test_concurrent_archives_keep_one_copy(tmp_path: Path) -> None:
    home = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()

    async def scenario() -> None:
        params = _server(home, outside)
        async with Client(params) as left, Client(params) as right:
            assert (await left.call_tool("create_room", {"name": "projectx"})).is_error is False
            marker = home / "rooms" / "projectx" / "attachments" / "marker.txt"
            marker.write_text("only-copy", encoding="utf-8")
            first, second = await anyio.gather(
                left.call_tool("archive_room", {"room": "projectx"}),
                right.call_tool("archive_room", {"room": "projectx"}),
            )
        outcomes = [first, second]
        successes = [item for item in outcomes if item.is_error is False]
        failures = [item for item in outcomes if item.is_error is True]
        assert len(successes) == 1
        assert len(failures) == 1
        assert "does not exist" in _text(failures[0])
        assert not (home / "rooms" / "projectx").exists()
        copies = list((home / "archived").rglob("marker.txt"))
        assert len(copies) == 1
        assert copies[0].read_text(encoding="utf-8") == "only-copy"

    anyio.run(scenario)


def test_archive_storage_failure_leaves_the_active_room(tmp_path: Path) -> None:
    home = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()

    async def scenario() -> None:
        async with Client(_server(home, outside)) as client:
            assert (await client.call_tool("create_room", {"name": "projectx"})).is_error is False
            marker = home / "rooms" / "projectx" / "attachments" / "marker.txt"
            marker.write_text("still here", encoding="utf-8")
            (home / "archived").write_text("not a directory", encoding="utf-8")
            failed = await client.call_tool("archive_room", {"room": "projectx"})
            assert failed.is_error is True
            assert "Could not prepare archive storage" in _text(failed)
            assert marker.read_text(encoding="utf-8") == "still here"
            assert (home / "rooms" / "projectx" / DATABASE_FILENAME).is_file()

    anyio.run(scenario)


def test_pending_ack_stays_on_the_archived_instance(tmp_path: Path) -> None:
    home = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()
    hold = tmp_path / "hold"
    hold.write_text("wait", encoding="utf-8")

    async def scenario() -> None:
        async with (
            Client(_server(home, outside)) as alice,
            Client(_server(home, outside, hold=hold)) as bob,
        ):
            assert (await alice.call_tool("create_room", {"name": "projectx"})).is_error is False
            sent = await alice.call_tool(
                "send_message",
                {"room": "projectx", "name": "alice", "to": "bob", "message": "old"},
            )
            assert sent.is_error is False
            captured: dict[str, object] = {}

            async def read_old() -> None:
                result = await bob.call_tool("read_messages", {"room": "projectx", "name": "bob"})
                captured["result"] = result

            async with anyio.create_task_group() as tasks:
                tasks.start_soon(read_old)
                await anyio.sleep(0.4)
                archived = await alice.call_tool("archive_room", {"room": "projectx"})
                assert archived.is_error is False
                assert archived.structured_content is not None
                created = await alice.call_tool("create_room", {"name": "projectx"})
                assert created.is_error is False
                fresh = await alice.call_tool(
                    "send_message",
                    {"room": "projectx", "name": "alice", "to": "bob", "message": "fresh"},
                )
                assert fresh.is_error is False
                hold.unlink()

            result = captured["result"]
            assert result.is_error is False
            assert result.structured_content is not None
            assert [item["content"] for item in result.structured_content["messages"]] == ["old"]
            archive = Path(archived.structured_content["path"])
            active = home / "rooms" / "projectx" / DATABASE_FILENAME
            assert _cursor(archive / DATABASE_FILENAME, "bob") == 1
            assert _cursor(active, "bob") == 0
            follow = await bob.call_tool("read_messages", {"room": "projectx", "name": "bob"})
            assert follow.structured_content is not None
            assert [item["content"] for item in follow.structured_content["messages"]] == ["fresh"]

    anyio.run(scenario)


def test_schema_one_room_is_readable_through_mcp(tmp_path: Path) -> None:
    home = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()
    room = home / "rooms" / "projectx"
    (room / "attachments").mkdir(parents=True)
    database = room / DATABASE_FILENAME
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE agents (
            name TEXT PRIMARY KEY,
            last_read_message_id INTEGER NOT NULL DEFAULT 0,
            read_at TEXT
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            timestamp TEXT NOT NULL,
            sender_name TEXT NOT NULL,
            recipient_name TEXT NOT NULL,
            content TEXT NOT NULL,
            attachment_paths TEXT NOT NULL DEFAULT '[]'
        );
        INSERT INTO metadata (key, value) VALUES ('created_at', '2026-01-01T00:00:00Z');
        INSERT INTO metadata (key, value) VALUES ('updated_at', '2026-01-01T00:00:00Z');
        INSERT INTO agents (name, last_read_message_id, read_at)
        VALUES ('alice', 0, NULL);
        INSERT INTO agents (name, last_read_message_id, read_at)
        VALUES ('bob', 1, '2026-01-02T00:00:00Z');
        INSERT INTO messages (
            timestamp, sender_name, recipient_name, content, attachment_paths
        ) VALUES ('2026-01-01T00:00:01Z', 'alice', 'bob', 'legacy note', '[]');
        """
    )
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()

    async def scenario() -> None:
        async with Client(_server(home, outside)) as client:
            read = await client.call_tool("read_messages", {"room": "projectx", "name": "alice"})
            assert read.is_error is False
            assert read.structured_content is not None
            assert read.structured_content["messages"][0]["content"] == "legacy note"

    anyio.run(scenario)
    connection = sqlite3.connect(database)
    try:
        instance_id = connection.execute(
            "SELECT value FROM metadata WHERE key = 'instance_id'"
        ).fetchone()[0]
        bob = connection.execute(
            "SELECT last_read_message_id, read_at FROM agents WHERE name = 'bob'"
        ).fetchone()
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()
    assert instance_id
    assert bob == (1, "2026-01-02T00:00:00Z")
    assert version == 1
    assert _cursor(database, "alice") == 1
