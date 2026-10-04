"""Human-readable and JSON rendering for CLI results."""

from __future__ import annotations

import json

from ai_note_pair.messaging import RoomInfo, SentMessage, UnreadBatch


def render_room_info(info: RoomInfo, *, as_json: bool) -> str:
    if as_json:
        payload = {
            "room": info.name,
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


def render_sent_message(message: SentMessage, *, as_json: bool) -> str:
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
        f"Sent message {message.id} in room {message.room!r} "
        f"from {message.sender!r} to {message.recipient!r}."
    )


def render_unread(batch: UnreadBatch, *, as_json: bool) -> str:
    if as_json:
        payload = {
            "room": batch.room,
            "reader": batch.reader,
            "messages": [
                {
                    "id": message.id,
                    "timestamp": message.timestamp,
                    "sender": message.sender,
                    "recipient": message.recipient,
                    "content": message.content,
                    "attachments": list(message.attachments),
                }
                for message in batch.messages
            ],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)
    if not batch.messages:
        return "No unread messages."
    blocks: list[str] = []
    for message in batch.messages:
        lines = [
            f"[{message.id}] {message.timestamp} {message.sender} -> {message.recipient}",
            message.content,
        ]
        lines.extend(f"Attachment: {path}" for path in message.attachments)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
