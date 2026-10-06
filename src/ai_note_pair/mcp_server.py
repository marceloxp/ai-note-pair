"""Local stdio MCP server for shared agent rooms."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, TypeVar

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from ai_note_pair import __version__
from ai_note_pair.archive import archive_room as move_room
from ai_note_pair.errors import AiNotePairError
from ai_note_pair.mcp_delivery import delivering_stdio, schedule_delivery, wait_for_test_hold
from ai_note_pair.messaging import ChatMessage, collect_unread, count_unread, publish_message
from ai_note_pair.messaging import room_info as inspect_room
from ai_note_pair.rooms import create_room as create_empty_room
from ai_note_pair.rooms import list_active_rooms, list_archived_rooms
from ai_note_pair.storage import archived_dir

server = MCPServer(
    "ai-note-pair",
    version=__version__,
    instructions=(
        "Local rooms shared by AI agents on this machine. Agent names are not authenticated. "
        "Use room_info to discover the name already registered for you. "
        "read_messages and send_message return conversation that this agent has not consumed. "
        "That agent's cursor advances when writing a successful tool response starts. "
        "Other agents' cursors stay unchanged. Do not poll; read when the user says messages "
        "are available. check_messages counts unread messages and does not move the cursor."
    ),
)

_NAME = Annotated[
    str,
    Field(
        description=(
            "Name of 1-64 characters: letters, digits, '_' or '-', "
            "starting with a letter or digit. 'all' is reserved."
        )
    ),
]


class CreatedRoom(BaseModel):
    name: str = Field(description="Name of the room that was created.")
    path: str = Field(description="Absolute directory containing the room database.")


class RoomList(BaseModel):
    archived: bool = Field(description="True when the names are archived rooms.")
    rooms: list[str] = Field(description="Sorted room names. Empty when none match.")


class AgentCount(BaseModel):
    name: str = Field(description="Registered participant name.")
    messages_sent: int = Field(description="Messages this participant sent. Broadcasts count once.")


class ArchivedRoom(BaseModel):
    room: str = Field(description="Active name that was archived.")
    name: str = Field(description="Archive directory name, including the timestamp suffix.")
    path: str = Field(
        description="Absolute directory of the archived room, database, and attachments."
    )


class RoomDetails(BaseModel):
    room: str
    path: str = Field(description="Absolute directory of the room database and attachments.")
    created_at: str
    updated_at: str
    participants: int
    messages: int
    agents: list[AgentCount]


T = TypeVar("T")


def _call(func: Callable[..., T], *args: object) -> T:
    try:
        return func(*args)
    except AiNotePairError as exc:
        raise ToolError(str(exc)) from exc


@server.tool()
def create_room(name: _NAME) -> CreatedRoom:
    """Create an empty local room.

    Fails when the name is invalid or the room already exists. Does not register
    agents or write messages.
    """
    room = _call(create_empty_room, name)
    return CreatedRoom(name=name, path=str(room.resolve()))


@server.tool()
def list_rooms(
    archived: Annotated[
        bool,
        Field(description="When true, list archived room names instead of active rooms."),
    ] = False,
) -> RoomList:
    """List active or archived room names.

    Does not create rooms, change membership, or consume messages. An empty
    storage location returns no names.
    """
    names = _call(list_archived_rooms if archived else list_active_rooms)
    return RoomList(archived=archived, rooms=names)


@server.tool()
def archive_room(room: _NAME) -> ArchivedRoom:
    """Move an active room into timestamped archive storage.

    Messages, attachments, and read cursors move with the room. The active name
    becomes available again. A missing room or a storage failure leaves the
    active room in place and returns an error.
    """
    name = _call(move_room, room)
    destination = archived_dir() / name
    return ArchivedRoom(room=room, name=name, path=str(destination.resolve()))


@server.tool()
def room_info(room: _NAME) -> RoomDetails:
    """Show one active room's path, dates, participants, and sent-message counts.

    Call this to discover the name already registered for you. It does not
    consume messages or change read cursors. A participant can appear here
    before it has sent anything.
    """
    info = _call(inspect_room, room)
    return RoomDetails(
        room=info.name,
        path=info.path,
        created_at=info.created_at,
        updated_at=info.updated_at,
        participants=info.participant_count,
        messages=info.message_count,
        agents=[
            AgentCount(name=agent.name, messages_sent=agent.messages_sent) for agent in info.agents
        ],
    )


class ChatLine(BaseModel):
    id: int
    timestamp: str
    sender: str
    recipient: str = Field(description="Direct recipient, or 'all' for a broadcast.")
    content: str
    attachments: list[str] = Field(description="Room-relative attachment paths.")


class SendReceipt(BaseModel):
    room: str
    id: int = Field(description="ID of the message that was stored.")
    sender: str
    recipient: str
    timestamp: str
    pending: list[ChatLine] = Field(
        description="Messages this sender had not read, excluding the message just stored."
    )


class ReadBatch(BaseModel):
    room: str
    reader: str
    messages: list[ChatLine] = Field(
        description="Conversation after this agent's cursor, in ID order."
    )


def _lines(messages: tuple[ChatMessage, ...]) -> list[ChatLine]:
    return [
        ChatLine(
            id=message.id,
            timestamp=message.timestamp,
            sender=message.sender,
            recipient=message.recipient,
            content=message.content,
            attachments=list(message.attachments),
        )
        for message in messages
    ]


@server.tool()
def send_message(
    room: _NAME,
    name: _NAME,
    message: Annotated[str, Field(description="Message text. Line breaks are preserved.")],
    ctx: Context,
    to: Annotated[
        str | None,
        Field(
            description=(
                "Direct recipient, or 'all' to broadcast. Omit only when the room "
                "has exactly two participants; the other one is inferred."
            )
        ),
    ] = None,
    attachments: Annotated[
        list[str] | None,
        Field(description="Absolute or relative local file paths to copy into the room."),
    ] = None,
) -> SendReceipt:
    """Store a message and return this sender's pending conversation.

    Consume the pending messages: they are unread context for this sender,
    whoever they address, and the new message is not repeated there. When writing
    this successful response starts, only this sender's cursor advances through
    the stored ID. Errors or cancellation before writing starts leave it unchanged.
    Interrupted output after writing starts remains acknowledged and may not be
    recoverable by reading again. Retrying a send can create a duplicate.
    """
    paths = [Path(item) for item in attachments] if attachments else None
    result = _call(publish_message, room, name, message, to, None, paths)
    schedule_delivery(ctx.request_id, result.pending)
    wait_for_test_hold()
    sent = result.sent
    return SendReceipt(
        room=sent.room,
        id=sent.id,
        sender=sent.sender,
        recipient=sent.recipient,
        timestamp=sent.timestamp,
        pending=_lines(result.pending.messages),
    )


class UnreadStatus(BaseModel):
    room: str
    reader: str
    unread: int = Field(description="Messages after this agent's cursor.")


@server.tool()
def check_messages(room: _NAME, name: _NAME) -> UnreadStatus:
    """Count messages after this agent's cursor without acknowledging them.

    Does not change read cursors. Use this to learn whether unread conversation
    exists. Call read_messages only when the user says messages are available.
    """
    status = _call(count_unread, room, name)
    return UnreadStatus(room=status.room, reader=status.reader, unread=status.unread)


@server.tool()
def read_messages(room: _NAME, name: _NAME, ctx: Context) -> ReadBatch:
    """Return unread conversation and acknowledge when response writing starts.

    Call this when the user says messages are available. Do not poll. Every
    message after the cursor is included, whoever it addresses. An empty result
    does not move the cursor. When writing a successful response starts, only
    this agent's cursor advances to the last returned ID. Later arrivals stay
    unread. Errors or cancellation before writing starts leave messages pending.
    Interrupted output after writing starts remains acknowledged.
    """
    batch = _call(collect_unread, room, name)
    schedule_delivery(ctx.request_id, batch)
    wait_for_test_hold()
    return ReadBatch(room=batch.room, reader=batch.reader, messages=_lines(batch.messages))


def main() -> None:
    """Serve tools over stdio. Protocol messages use stdout."""
    parser = argparse.ArgumentParser(
        prog="ai-note-pair-mcp",
        description="Local MCP server for shared agent chat rooms over stdio.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            f"Executable: {Path(sys.argv[0]).absolute()}\n\n"
            "Without arguments, starts the server on stdin/stdout. Configure your "
            "MCP client to launch this command. Storage defaults to "
            "~/.config/ai-note-pair/; set AI_NOTE_PAIR_HOME to override it."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args()
    anyio.run(_serve)


async def _serve() -> None:
    async with delivering_stdio() as (read_stream, write_stream):
        await server._lowlevel_server.run(
            read_stream,
            write_stream,
            server._lowlevel_server.create_initialization_options(),
        )


if __name__ == "__main__":
    main()
