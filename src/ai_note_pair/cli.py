"""Command-line interface for ai-note-pair."""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import TypeVar

import typer

from ai_note_pair.archive import archive_room
from ai_note_pair.errors import AiNotePairError
from ai_note_pair.messaging import (
    acknowledge_read,
    collect_unread,
    load_message_content,
    publish_message,
    room_info,
)
from ai_note_pair.presentation import render_room_info, render_sent_message, render_unread
from ai_note_pair.rooms import create_room, list_active_rooms, list_archived_rooms

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Local persistent chat for AI agents on the same machine.",
)

F = TypeVar("F", bound=Callable[..., None])


def _guard(func: F) -> F:
    @wraps(func)
    def wrapper(*args: object, **kwargs: object) -> None:
        try:
            func(*args, **kwargs)
        except AiNotePairError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=exc.exit_code) from exc

    return wrapper  # type: ignore[return-value]


def storage_home() -> Path | None:
    """Return AI_NOTE_PAIR_HOME when set, so tests can isolate storage."""
    override = os.environ.get("AI_NOTE_PAIR_HOME")
    if not override:
        return None
    return Path(override).expanduser()


@app.command("create-room")
@_guard
def create_room_command(
    name: str = typer.Option(..., "--name", help="Name of the room to create."),
) -> None:
    """Initialize an empty room."""
    create_room(name, storage_home())
    typer.echo(f"Created room {name!r}.")


@app.command("list-rooms")
@_guard
def list_rooms_command(
    archived: bool = typer.Option(
        False,
        "--archived",
        help="List archived rooms instead of active rooms.",
    ),
) -> None:
    """List active rooms, or archived rooms when --archived is set."""
    names = list_archived_rooms(storage_home()) if archived else list_active_rooms(storage_home())
    for room_name in names:
        typer.echo(room_name)


@app.command("archive")
@_guard
def archive_command(
    room: str = typer.Option(..., "--room", help="Active room to archive."),
) -> None:
    """Move a room into timestamped archive storage."""
    destination = archive_room(room, storage_home())
    typer.echo(f"Archived room {room!r} as {destination!r}.")


@app.command("info")
@_guard
def info_command(
    room: str = typer.Option(..., "--room", help="Room to inspect."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Show room metadata and sent-message counts."""
    typer.echo(render_room_info(room_info(room, storage_home()), as_json=json_output))


@app.command("send")
@_guard
def send_command(
    room: str = typer.Option(..., "--room", help="Room that receives the message."),
    name: str = typer.Option(..., "--name", help="Sender agent name."),
    message: str | None = typer.Option(None, "--message", help="Inline message text."),
    message_file: str | None = typer.Option(
        None,
        "--message-file",
        help="UTF-8 message file, or - to read stdin.",
    ),
    to: str | None = typer.Option(
        None,
        "--to",
        help=(
            "Direct recipient, or 'all' to broadcast. Inferred when the room has two participants."
        ),
    ),
    attachment: list[str] | None = typer.Option(
        None,
        "--attachment",
        help="File to copy into the room. Repeat to attach several files.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Send a direct or broadcast message."""
    content = load_message_content(message, message_file)
    sent = publish_message(
        room,
        name,
        content,
        to,
        storage_home(),
        [Path(path) for path in attachment or []],
    )
    typer.echo(render_sent_message(sent, as_json=json_output))


@app.command("read")
@_guard
def read_command(
    room: str = typer.Option(..., "--room", help="Room to synchronize."),
    name: str = typer.Option(..., "--name", help="Agent whose unread cursor is used."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Return conversation messages this agent has not read yet."""
    batch = collect_unread(room, name, storage_home())
    typer.echo(render_unread(batch, as_json=json_output))
    acknowledge_read(batch, storage_home())
