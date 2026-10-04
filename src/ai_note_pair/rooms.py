"""Active room creation and listing."""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ai_note_pair.errors import AiNotePairError
from ai_note_pair.storage import (
    ATTACHMENTS_DIRNAME,
    DATABASE_FILENAME,
    archived_dir,
    initialize_database,
    rooms_dir,
)

_ROOM_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def validate_room_name(name: str) -> str:
    """Reject names that are empty or could escape the rooms directory."""
    if not _ROOM_NAME.fullmatch(name):
        raise AiNotePairError(
            "Invalid room name "
            f"{name!r}. Use 1-64 characters: letters, digits, '_' or '-', "
            "starting with a letter or digit."
        )
    return name


def room_directory(name: str, home: Path | None = None) -> Path:
    """Return the active room directory after checking it stays inside rooms/."""
    validate_room_name(name)
    root = rooms_dir(home)
    candidate = root / name
    if candidate.is_symlink():
        raise AiNotePairError(
            f"Room {name!r} is a symbolic link. "
            "Room directories must stay inside the storage directory."
        )
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if resolved.parent != resolved_root or resolved.name != name:
        raise AiNotePairError(
            f"Invalid room name {name!r}. Room directories must stay inside the storage directory."
        )
    return candidate


@contextmanager
def exclusive_room(room: Path) -> Iterator[None]:
    """Lock one room directory so send and archive cannot split that instance."""
    if room.is_symlink() or not room.is_dir():
        raise AiNotePairError(f"Room {room.name!r} does not exist.")
    lock_path = room / ".room.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as exc:
        raise AiNotePairError(
            f"Could not lock room {room.name!r}: {_os_error_message(exc)}"
        ) from exc
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if room.is_symlink() or not room.is_dir():
            raise AiNotePairError(f"Room {room.name!r} does not exist.")
        try:
            current = lock_path.stat()
        except OSError as exc:
            raise AiNotePairError(f"Room {room.name!r} does not exist.") from exc
        held = os.fstat(descriptor)
        if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
            raise AiNotePairError(
                f"Room {room.name!r} changed while the command was waiting. Retry the command."
            )
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def create_room(name: str, home: Path | None = None) -> Path:
    """Create an empty room. Existing rooms are left unchanged."""
    room = room_directory(name, home)
    try:
        room.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AiNotePairError(
            f"Could not prepare storage for room {name!r}: {_os_error_message(exc)}"
        ) from exc

    try:
        room.mkdir()
    except FileExistsError as exc:
        raise AiNotePairError(f"Room {name!r} already exists.") from exc
    except OSError as exc:
        raise AiNotePairError(f"Could not create room {name!r}: {_os_error_message(exc)}") from exc

    try:
        (room / ATTACHMENTS_DIRNAME).mkdir()
        initialize_database(room / DATABASE_FILENAME)
    except (OSError, sqlite3.Error) as exc:
        shutil.rmtree(room, ignore_errors=True)
        raise AiNotePairError(
            f"Could not initialize room {name!r}: {_os_error_message(exc)}"
        ) from exc
    except Exception:
        shutil.rmtree(room, ignore_errors=True)
        raise
    return room


def list_active_rooms(home: Path | None = None) -> list[str]:
    """List active room names that contain a room database."""
    root = rooms_dir(home)
    if not root.is_dir():
        return []
    names: list[str] = []
    try:
        entries = list(root.iterdir())
    except OSError as exc:
        raise AiNotePairError(f"Could not list rooms: {_os_error_message(exc)}") from exc
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir():
            continue
        if (entry / DATABASE_FILENAME).is_file():
            names.append(entry.name)
    return sorted(names)


def list_archived_rooms(home: Path | None = None) -> list[str]:
    """List archived room directory names that still contain a database."""
    root = archived_dir(home)
    if not root.is_dir():
        return []
    names: list[str] = []
    try:
        entries = list(root.iterdir())
    except OSError as exc:
        raise AiNotePairError(f"Could not list archived rooms: {_os_error_message(exc)}") from exc
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir():
            continue
        if (entry / DATABASE_FILENAME).is_file():
            names.append(entry.name)
    return sorted(names)


def _os_error_message(exc: Exception) -> str:
    strerror = getattr(exc, "strerror", None)
    if isinstance(strerror, str) and strerror:
        return strerror
    return str(exc) or exc.__class__.__name__
