"""Move an active room into timestamped archive storage."""

from __future__ import annotations

import errno
import os
from datetime import datetime, timezone
from pathlib import Path

from ai_note_pair.errors import AiNotePairError
from ai_note_pair.rooms import exclusive_room, room_directory
from ai_note_pair.storage import DATABASE_FILENAME, archived_dir


def archive_room(
    name: str,
    home: Path | None = None,
    moment: datetime | None = None,
) -> str:
    """Archive one active room. Existing archives are left in place."""
    source = room_directory(name, home)
    database = source / DATABASE_FILENAME
    if database.is_symlink() or not database.is_file():
        raise AiNotePairError(f"Room {name!r} does not exist.")

    destination_root = archived_dir(home)
    try:
        destination_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AiNotePairError(
            f"Could not prepare archive storage for room {name!r}: {exc.strerror or exc}"
        ) from exc

    with exclusive_room(source):
        if source.is_symlink() or database.is_symlink() or not database.is_file():
            raise AiNotePairError(f"Room {name!r} does not exist.")
        stamp = (moment or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M%S")
        base = f"{name}-{stamp}"
        for candidate_name in _archive_names(base):
            destination = destination_root / candidate_name
            resolved_root = destination_root.resolve()
            resolved_destination = destination.resolve()
            if resolved_destination.parent != resolved_root:
                raise AiNotePairError(f"Invalid archive destination {candidate_name!r}.")
            try:
                os.rename(source, destination)
            except FileNotFoundError as exc:
                raise AiNotePairError(f"Room {name!r} does not exist.") from exc
            except OSError as exc:
                if exc.errno in {errno.EEXIST, errno.ENOTEMPTY} and source.exists():
                    continue
                raise AiNotePairError(
                    f"Could not archive room {name!r}: {exc.strerror or exc}"
                ) from exc
            return candidate_name

    raise AiNotePairError(
        f"Could not archive room {name!r}: every destination for timestamp {stamp} already exists."
    )


def _archive_names(base: str):
    yield base
    number = 2
    while number < 10_000:
        yield f"{base}-{number}"
        number += 1
