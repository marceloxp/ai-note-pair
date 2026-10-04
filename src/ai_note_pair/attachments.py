"""Copy message attachments into a room without overwriting existing files."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ai_note_pair.errors import AiNotePairError
from ai_note_pair.storage import ATTACHMENTS_DIRNAME


def copy_attachment(room: Path, source: Path) -> str:
    """Copy one file into the room and return its room-relative path."""
    if not source.is_file():
        raise AiNotePairError(f"Attachment {os.fspath(source)!r} does not exist or is not a file.")
    filename = source.name
    if filename in {"", ".", ".."}:
        raise AiNotePairError(f"Attachment {os.fspath(source)!r} has an invalid file name.")

    destination_dir = room / ATTACHMENTS_DIRNAME
    if (
        destination_dir.is_symlink()
        or not destination_dir.is_dir()
        or destination_dir.resolve().parent != room.resolve()
    ):
        raise AiNotePairError(
            f"Room attachment directory {os.fspath(destination_dir)!r} is not available."
        )

    for candidate in _candidate_names(filename):
        destination = destination_dir / candidate
        try:
            descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            continue
        try:
            with os.fdopen(descriptor, "wb") as outgoing, source.open("rb") as incoming:
                shutil.copyfileobj(incoming, outgoing)
        except OSError as exc:
            destination.unlink(missing_ok=True)
            raise AiNotePairError(
                f"Could not copy attachment {os.fspath(source)!r}: {exc.strerror or exc}"
            ) from exc
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return f"{ATTACHMENTS_DIRNAME}/{candidate}"

    raise AiNotePairError(f"Could not allocate a unique name for attachment {filename!r}.")


def discard_attachments(room: Path, relative_paths: list[str]) -> None:
    """Remove attachment files copied for a send that did not commit."""
    root = (room / ATTACHMENTS_DIRNAME).resolve()
    for relative in relative_paths:
        candidate = (room / relative).resolve()
        if candidate.parent != root or not candidate.is_file():
            continue
        candidate.unlink(missing_ok=True)


def _candidate_names(filename: str):
    yield filename
    path = Path(filename)
    number = 2
    while number < 10_000:
        yield f"{path.stem}-{number}{path.suffix}"
        number += 1
