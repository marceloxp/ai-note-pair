"""Human-readable and JSON rendering for CLI results."""

from __future__ import annotations

import json

from ai_note_pair.messaging import ChatMessage, RoomInfo, SentMessage, UnreadBatch


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


def render_unread(batch: UnreadBatch, *, as_json: bool) -> str:
    if as_json:
        payload = {
            "room": batch.room,
            "reader": batch.reader,
            "messages": [_message_payload(message) for message in batch.messages],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)
    if not batch.messages:
        return "No unread messages."
    return _render_message_blocks(batch.messages)


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
