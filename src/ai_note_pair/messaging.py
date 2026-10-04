"""Direct messages, broadcasts, and room inspection."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from ai_note_pair.attachments import copy_attachment, discard_attachments
from ai_note_pair.errors import AiNotePairError
from ai_note_pair.rooms import exclusive_room, room_directory
from ai_note_pair.storage import (
    DATABASE_FILENAME,
    SCHEMA_VERSION,
    archived_dir,
    connect,
    ensure_instance_id,
    schema_version,
    utc_now,
)

BROADCAST = "all"
_AGENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class AgentSummary:
    name: str
    messages_sent: int


@dataclass(frozen=True)
class RoomInfo:
    name: str
    path: str
    created_at: str
    updated_at: str
    message_count: int
    agents: tuple[AgentSummary, ...]

    @property
    def participant_count(self) -> int:
        return len(self.agents)


@dataclass(frozen=True)
class SentMessage:
    room: str
    id: int
    sender: str
    recipient: str
    timestamp: str


@dataclass(frozen=True)
class ChatMessage:
    id: int
    timestamp: str
    sender: str
    recipient: str
    content: str
    attachments: tuple[str, ...]


@dataclass(frozen=True)
class UnreadBatch:
    room: str
    reader: str
    instance_id: str
    previous_cursor: int
    messages: tuple[ChatMessage, ...]
    delivered_id: int


@dataclass(frozen=True)
class SendResult:
    sent: SentMessage
    pending: UnreadBatch


def validate_agent_name(name: str, *, role: str) -> str:
    """Reject blank, unsafe, or reserved agent names."""
    if name == BROADCAST or not _AGENT_NAME.fullmatch(name):
        raise AiNotePairError(
            f"Invalid {role} {name!r}. Use 1-64 characters: letters, digits, '_' or '-', "
            "starting with a letter or digit. The name 'all' is reserved for broadcasts."
        )
    return name


def load_message_content(message: str | None, message_file: str | None) -> str:
    """Select exactly one inline, file, or stdin message source."""
    if (message is None) == (message_file is None):
        raise AiNotePairError("Provide exactly one of --message or --message-file.")
    if message is not None:
        return message
    assert message_file is not None
    if message_file == "-":
        return sys.stdin.read()
    path = Path(message_file)
    if not path.is_file():
        raise AiNotePairError(f"Message file {message_file!r} does not exist.")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise AiNotePairError(f"Message file {message_file!r} is not valid UTF-8.") from exc
    except OSError as exc:
        raise AiNotePairError(
            f"Could not read message file {message_file!r}: {exc.strerror or exc}"
        ) from exc


def publish_message(
    room: str,
    sender: str,
    content: str,
    recipient: str | None = None,
    home: Path | None = None,
    attachment_sources: list[Path] | None = None,
) -> SendResult:
    """Copy attachments, then store the message on the same room instance."""
    validate_agent_name(sender, role="sender")
    if recipient is not None and recipient != BROADCAST:
        validate_agent_name(recipient, role="recipient")
    room_path = room_directory(room, home)
    database = _require_database(room, home)
    copied: list[str] = []
    with exclusive_room(room_path):
        database = _require_database(room, home)
        try:
            for source in attachment_sources or []:
                copied.append(copy_attachment(room_path, source))
            return send_message(
                room,
                sender,
                content,
                recipient,
                home,
                attachment_paths=copied,
                database=database,
            )
        except Exception:
            discard_attachments(room_path, copied)
            raise


def send_message(
    room: str,
    sender: str,
    content: str,
    recipient: str | None = None,
    home: Path | None = None,
    attachment_paths: list[str] | None = None,
    database: Path | None = None,
) -> SendResult:
    """Store one message and snapshot the sender's still-unread context."""
    validate_agent_name(sender, role="sender")
    if recipient is not None and recipient != BROADCAST:
        validate_agent_name(recipient, role="recipient")

    if database is None:
        database = _require_database(room, home)
    connection = _open_room(database)
    try:
        try:
            connection.execute("BEGIN IMMEDIATE")
            resolved = _resolve_recipient(connection, sender, recipient)
            _ensure_agent(connection, sender)
            if resolved != BROADCAST:
                _ensure_agent(connection, resolved)
            timestamp = utc_now()
            stored_attachments = "[]" if not attachment_paths else _encode_paths(attachment_paths)
            cursor = connection.execute(
                """
                INSERT INTO messages (
                    timestamp, sender_name, recipient_name, content, attachment_paths
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (timestamp, sender, resolved, content, stored_attachments),
            )
            message_id = int(cursor.lastrowid)
            connection.execute(
                "UPDATE metadata SET value = ? WHERE key = 'updated_at'",
                (timestamp,),
            )
            instance_id = _metadata(connection, "instance_id")
            previous = int(
                connection.execute(
                    "SELECT last_read_message_id FROM agents WHERE name = ?",
                    (sender,),
                ).fetchone()[0]
            )
            pending_rows = _select_messages(connection, previous, message_id, inclusive=False)
            connection.commit()
        except AiNotePairError:
            _rollback(connection)
            raise
        except sqlite3.Error as exc:
            _rollback(connection)
            raise _database_failure(exc) from exc
    finally:
        connection.close()

    sent = SentMessage(
        room=room,
        id=message_id,
        sender=sender,
        recipient=resolved,
        timestamp=timestamp,
    )
    return SendResult(
        sent=sent,
        pending=UnreadBatch(
            room=room,
            reader=sender,
            instance_id=instance_id,
            previous_cursor=previous,
            messages=tuple(_chat_message(row) for row in pending_rows),
            delivered_id=message_id,
        ),
    )


def collect_unread(room: str, reader: str, home: Path | None = None) -> UnreadBatch:
    """Return messages after this agent's cursor without advancing it."""
    validate_agent_name(reader, role="reader")
    database = _require_database(room, home)
    connection = _open_room(database)
    try:
        try:
            connection.execute("BEGIN IMMEDIATE")
            instance_id = _metadata(connection, "instance_id")
            row = connection.execute(
                "SELECT last_read_message_id FROM agents WHERE name = ?",
                (reader,),
            ).fetchone()
            if row is None:
                raise AiNotePairError(
                    f"Agent {reader!r} is not a participant in room {room!r}. "
                    "Send a message that includes this name before reading."
                )
            previous = int(row["last_read_message_id"])
            upper = int(
                connection.execute("SELECT COALESCE(MAX(id), 0) FROM messages").fetchone()[0]
            )
            rows = _select_messages(connection, previous, upper, inclusive=True)
            connection.rollback()
        except AiNotePairError:
            _rollback(connection)
            raise
        except sqlite3.Error as exc:
            _rollback(connection)
            raise _database_failure(exc) from exc
    finally:
        connection.close()
    messages = tuple(_chat_message(row) for row in rows)
    return UnreadBatch(
        room=room,
        reader=reader,
        instance_id=instance_id,
        previous_cursor=previous,
        messages=messages,
        delivered_id=messages[-1].id if messages else previous,
    )


def acknowledge_read(batch: UnreadBatch, home: Path | None = None) -> None:
    """Advance the reader cursor to this delivery's ID, never backward."""
    if batch.delivered_id <= batch.previous_cursor:
        return
    delivered_id = batch.delivered_id
    database = _database_for_instance(batch.room, batch.instance_id, home)
    connection = _open_room(database)
    try:
        try:
            connection.execute("BEGIN IMMEDIATE")
            if _metadata(connection, "instance_id") != batch.instance_id:
                raise AiNotePairError(
                    f"Room {batch.room!r} changed before the read could be recorded. "
                    "The replacement room was left unchanged."
                )
            connection.execute(
                """
                UPDATE agents
                SET last_read_message_id = ?, read_at = ?
                WHERE name = ? AND last_read_message_id < ?
                """,
                (delivered_id, utc_now(), batch.reader, delivered_id),
            )
            connection.commit()
        except AiNotePairError:
            _rollback(connection)
            raise
        except sqlite3.Error as exc:
            _rollback(connection)
            raise _database_failure(exc) from exc
    finally:
        connection.close()


def room_info(room: str, home: Path | None = None) -> RoomInfo:
    """Read room metadata and sent-message counts without changing room state."""
    database = _require_database(room, home)
    connection = _open_room(database)
    try:
        try:
            created_at = _metadata(connection, "created_at")
            updated_at = _metadata(connection, "updated_at")
            message_count = int(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
            rows = connection.execute(
                """
                SELECT agents.name AS name, COUNT(messages.id) AS messages_sent
                FROM agents
                LEFT JOIN messages ON messages.sender_name = agents.name
                GROUP BY agents.name
                ORDER BY agents.name
                """
            ).fetchall()
        except sqlite3.Error as exc:
            raise _database_failure(exc) from exc
    finally:
        connection.close()
    agents = tuple(
        AgentSummary(name=row["name"], messages_sent=int(row["messages_sent"])) for row in rows
    )
    return RoomInfo(
        name=room,
        path=str(database.parent.resolve()),
        created_at=created_at,
        updated_at=updated_at,
        message_count=message_count,
        agents=agents,
    )


def _resolve_recipient(connection: sqlite3.Connection, sender: str, recipient: str | None) -> str:
    if recipient == BROADCAST:
        others = _participant_names(connection) - {sender}
        if not others:
            raise AiNotePairError(
                "A broadcast needs another known participant. "
                "Send a direct message before broadcasting."
            )
        return BROADCAST
    if recipient is not None:
        return recipient

    participants = _participant_names(connection) | {sender}
    others = participants - {sender}
    if len(participants) == 2 and len(others) == 1:
        return next(iter(others))
    if len(participants) < 2:
        raise AiNotePairError(
            "A recipient is required when the room does not already contain the other participant. "
            "Pass --to <agent-name>."
        )
    raise AiNotePairError(
        "A recipient is required when the room has more than two participants. "
        "Pass --to <agent-name> or --to all."
    )


def _participant_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM agents").fetchall()
    return {str(row["name"]) for row in rows}


def _ensure_agent(connection: sqlite3.Connection, name: str) -> None:
    connection.execute(
        """
        INSERT INTO agents (name, last_read_message_id, read_at)
        VALUES (?, 0, NULL)
        ON CONFLICT(name) DO NOTHING
        """,
        (name,),
    )


def _require_database(room: str, home: Path | None) -> Path:
    directory = room_directory(room, home)
    database = directory / DATABASE_FILENAME
    if database.is_symlink() or not _database_is_inside(directory, database):
        if database.is_symlink():
            raise AiNotePairError(f"Room {room!r} points outside the storage directory.")
        raise AiNotePairError(f"Room {room!r} does not exist.")
    return database


def _database_for_instance(room: str, instance_id: str, home: Path | None) -> Path:
    """Find the database captured by a read, including after that room was archived."""
    directory = room_directory(room, home)
    active = directory / DATABASE_FILENAME
    if _database_is_inside(directory, active) and _peek_instance_id(active) == instance_id:
        return active
    root = archived_dir(home)
    if root.is_dir() and not root.is_symlink():
        for entry in root.iterdir():
            if entry.is_symlink() or not entry.is_dir():
                continue
            database = entry / DATABASE_FILENAME
            if _database_is_inside(entry, database) and _peek_instance_id(database) == instance_id:
                return database
    raise AiNotePairError(
        f"Room {room!r} changed before the read could be recorded. "
        "The replacement room was left unchanged."
    )


def _database_is_inside(directory: Path, database: Path) -> bool:
    if database.is_symlink() or not database.is_file():
        return False
    return database.resolve().parent == directory.resolve()


def _peek_instance_id(database: Path) -> str | None:
    connection: sqlite3.Connection | None = None
    try:
        connection = connect(database)
        row = connection.execute("SELECT value FROM metadata WHERE key = 'instance_id'").fetchone()
    except sqlite3.Error:
        return None
    finally:
        if connection is not None:
            connection.close()
    if row is None:
        return None
    return str(row[0])


def _database_failure(exc: sqlite3.Error) -> AiNotePairError:
    text = str(exc).lower()
    if "locked" in text or "busy" in text:
        return AiNotePairError(
            "The room database is busy. Wait for the other command to finish and retry."
        )
    return AiNotePairError(f"Could not access the room database: {exc}")


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.rollback()
    except sqlite3.Error:
        pass


def _open_room(database: Path) -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    try:
        connection = connect(database)
        version = schema_version(connection)
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise AiNotePairError(f"Could not open room database '{database}': {exc}") from exc
    if version != SCHEMA_VERSION:
        connection.close()
        raise AiNotePairError(
            f"Unsupported room schema version {version}. "
            f"This build supports version {SCHEMA_VERSION}."
        )
    try:
        ensure_instance_id(connection)
    except sqlite3.Error as exc:
        connection.close()
        text = str(exc).lower()
        if "locked" in text or "busy" in text:
            raise _database_failure(exc) from exc
        raise AiNotePairError(f"Could not open room database '{database}': {exc}") from exc
    return connection


def _metadata(connection: sqlite3.Connection, key: str) -> str:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        raise AiNotePairError(f"Room metadata is missing {key!r}.")
    return str(row["value"])


def _select_messages(
    connection: sqlite3.Connection,
    after_id: int,
    end_id: int,
    *,
    inclusive: bool,
) -> list[sqlite3.Row]:
    comparison = "id <= ?" if inclusive else "id < ?"
    return connection.execute(
        f"""
        SELECT id, timestamp, sender_name, recipient_name, content, attachment_paths
        FROM messages
        WHERE id > ? AND {comparison}
        ORDER BY id
        """,
        (after_id, end_id),
    ).fetchall()


def _encode_paths(paths: list[str]) -> str:
    return json.dumps(paths, ensure_ascii=False)


def _chat_message(row: sqlite3.Row) -> ChatMessage:
    raw_paths = row["attachment_paths"]
    try:
        parsed = json.loads(raw_paths)
    except json.JSONDecodeError as exc:
        raise AiNotePairError(f"Message {row['id']} has unreadable attachment metadata.") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise AiNotePairError(f"Message {row['id']} has unreadable attachment metadata.")
    return ChatMessage(
        id=int(row["id"]),
        timestamp=str(row["timestamp"]),
        sender=str(row["sender_name"]),
        recipient=str(row["recipient_name"]),
        content=str(row["content"]),
        attachments=tuple(parsed),
    )
