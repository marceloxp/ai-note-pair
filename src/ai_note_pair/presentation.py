"""Human-readable and JSON rendering for CLI results."""

from __future__ import annotations

import json

from ai_note_pair.messaging import (
    ChatMessage,
    DeletedMessage,
    JoinedAgent,
    MarkedRead,
    RecentView,
    RoomInfo,
    SentMessage,
    StoredMessage,
    UnreadBatch,
    UnreadCount,
    choice_notice,
)


def render_room_info(info: RoomInfo, *, as_json: bool) -> str:
    if as_json:
        payload = {
            "room": info.name,
            "path": info.path,
            "created_at": info.created_at,
            "updated_at": info.updated_at,
            "participants": info.participant_count,
            "messages": info.message_count,
            "agents": [
                {"name": agent.name, "messages_sent": agent.messages_sent} for agent in info.agents
            ],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)
    label_width = max([len("Agent"), *(len(agent.name) for agent in info.agents)])
    lines = [
        f"Room: {info.name}",
        f"Path: {info.path}",
        f"Created: {info.created_at}",
        f"Updated: {info.updated_at}",
        f"Participants: {info.participant_count}",
        f"Messages: {info.message_count}",
        "",
        f"{'Agent':<{label_width}}  Messages sent",
    ]
    for agent in info.agents:
        lines.append(f"{agent.name:<{label_width}}  {agent.messages_sent}")
    return "\n".join(lines)


def render_sent_message(
    message: SentMessage,
    *,
    as_json: bool,
    pending: tuple[ChatMessage, ...] = (),
) -> str:
    if as_json:
        payload = {
            "room": message.room,
            "id": message.id,
            "sender": message.sender,
            "recipient": message.recipient,
            "timestamp": message.timestamp,
            "pending": [_message_payload(item) for item in pending],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)
    confirmation = (
        f"Sent message {message.id} in room {message.room!r} "
        f"from {message.sender!r} to {message.recipient!r}."
    )
    if not pending:
        return confirmation
    return f"{confirmation}\n\n{_render_message_blocks(pending)}"


def render_deleted_message(message: DeletedMessage, *, as_json: bool) -> str:
    if as_json:
        payload = {
            "room": message.room,
            "id": message.id,
            "sender": message.sender,
            "recipient": message.recipient,
            "timestamp": message.timestamp,
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)
    return (
        f"Deleted message {message.id} in room {message.room!r} "
        f"from {message.sender!r} to {message.recipient!r}."
    )


def render_joined(joined: JoinedAgent, *, as_json: bool) -> str:
    if as_json:
        payload = {"room": joined.room, "name": joined.name, "joined": joined.joined}
        return json.dumps(payload, indent=2, ensure_ascii=False)
    if joined.joined:
        return f"Joined room {joined.room!r} as {joined.name!r}."
    return f"Agent {joined.name!r} is already in room {joined.room!r}."


def render_marked_read(marked: MarkedRead, *, as_json: bool) -> str:
    if as_json:
        payload = {
            "room": marked.room,
            "reader": marked.reader,
            "marked_through": marked.marked_through,
            "skipped": marked.skipped,
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)
    if marked.skipped == 0:
        return "No unread messages."
    if marked.skipped == 1:
        skipped = "1 message"
    else:
        skipped = f"{marked.skipped} messages"
    return (
        f"Marked {skipped} read for {marked.reader!r} in room {marked.room!r} "
        f"through message {marked.marked_through}."
    )


def render_unread(batch: UnreadBatch, *, as_json: bool) -> str:
    notice = choice_notice(batch.unread_count, batch.characters) if batch.choice_required else ""
    if as_json:
        payload = {
            "room": batch.room,
            "reader": batch.reader,
            "unread": batch.unread_count,
            "characters": batch.characters,
            "choice_required": batch.choice_required,
            "notice": notice,
            "messages": [_message_payload(message) for message in batch.messages],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)
    if batch.choice_required:
        return notice
    if not batch.messages:
        return "No unread messages."
    return _render_message_blocks(batch.messages)


def render_recent(batch: RecentView, *, as_json: bool) -> str:
    if as_json:
        payload = {
            "room": batch.room,
            "reader": batch.reader,
            "cursor": batch.cursor,
            "latest_id": batch.latest_id,
            "unread": batch.unread,
            "limit": batch.limit,
            "agents": [
                {
                    "name": agent.name,
                    "last_read_message_id": agent.last_read_message_id,
                    "read_at": agent.read_at,
                }
                for agent in batch.agents
            ],
            "messages": [_message_payload(message) for message in batch.messages],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)
    lines = [
        f"Room: {batch.room}",
        f"Reader: {batch.reader}",
        f"Cursor: {batch.cursor}",
        f"Latest: {batch.latest_id}",
        f"Unread: {batch.unread}",
        "",
    ]
    label_width = max([len("Agent"), *(len(agent.name) for agent in batch.agents)])
    lines.append(f"{'Agent':<{label_width}}  Cursor  Read at")
    for agent in batch.agents:
        read_at = agent.read_at if agent.read_at is not None else "-"
        lines.append(f"{agent.name:<{label_width}}  {agent.last_read_message_id:<6}  {read_at}")
    lines.append("")
    if batch.messages:
        lines.append(_render_message_blocks(batch.messages))
    else:
        lines.append("No read messages.")
    return "\n".join(lines)


def render_stored_message(stored: StoredMessage, *, as_json: bool) -> str:
    if as_json:
        payload = {
            "room": stored.room,
            "reader": stored.reader,
            **_message_payload(stored.message),
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)
    return _render_message_blocks((stored.message,))


def render_unread_count(count: UnreadCount, *, as_json: bool) -> str:
    if as_json:
        payload = {"room": count.room, "reader": count.reader, "unread": count.unread}
        return json.dumps(payload, indent=2, ensure_ascii=False)
    if count.unread == 1:
        return "1 unread message."
    return f"{count.unread} unread messages."


def _message_payload(message: ChatMessage) -> dict[str, object]:
    return {
        "id": message.id,
        "timestamp": message.timestamp,
        "sender": message.sender,
        "recipient": message.recipient,
        "content": message.content,
        "attachments": list(message.attachments),
    }


def _render_message_blocks(messages: tuple[ChatMessage, ...]) -> str:
    blocks: list[str] = []
    for message in messages:
        lines = [
            f"[{message.id}] {message.timestamp} {message.sender} -> {message.recipient}",
            message.content,
        ]
        lines.extend(f"Attachment: {path}" for path in message.attachments)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
