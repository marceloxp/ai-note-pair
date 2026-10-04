# ai-note-pair Specification

Status: MVP implemented. Normative behavior for the delivered CLI is described below.

## Vision

`ai-note-pair` is a local, persistent chat CLI for AI agents working on the same machine. Agents exchange discrete notes in shared rooms containing two or more agents.

The application stores and routes messages and tracks what each agent has read. Agents run independently and use the CLI to communicate. There are no enforced roles or turns, model execution, provider endpoints, API keys, or global configuration file.

## Architecture

### Storage Structure

```text
~/.config/ai-note-pair/
├── rooms/
│   └── <room-name>/
│       ├── ai-note-pair.db
│       └── attachments/
└── archived/
    └── <room-name>-<YYYYMMDDhhmmss>/
        ├── ai-note-pair.db
        └── attachments/
```

All participants access the same local filesystem. Remote agents, Docker deployments, and cloud synchronization are outside the intended scope.

### Room Lifecycle

1. **Create**: Initialize a named room.
2. **Active**: Agents send messages and synchronize unread conversation context.
3. **Archive**: Move the database and attachments to a timestamp-suffixed directory, freeing the active room name for reuse.

Archive example: `projectx-20261003203715`.

Archive names use a UTC timestamp: `<room-name>-<YYYYMMDDhhmmss>`. If that destination already exists, preserve it and store the new archive at the next free suffix, `<room-name>-<YYYYMMDDhhmmss>-2`, then `-3`, and so on.

### Agent Identity and Membership

- Identity is permissive: the supplied agent name is the identity used for the operation.
- Repeated use of the same name is allowed and refers to the same logical agent in that room.
- There is no authentication, name ownership verification, required model metadata, or model/role naming convention.
- The name `all` is reserved for broadcasts. It is rejected as a sender or as a direct recipient, so the broadcast token cannot collide with an agent name. Agent names are otherwise case-sensitive and use 1–64 characters: letters, digits, `_`, or `-`, starting with a letter or digit.
- Membership is scoped to a room and must be persisted so the application can determine recipients.
- A successful explicitly addressed send automatically registers both the sender and the named recipient if they are not already known in the room. Registration and message insertion happen together.
- A recipient can therefore exist before sending any messages. Its name is available for the intended agent to discover through `info --room <room-name>`; this is a conversational reservation, not exclusive ownership.
- No explicit join command is required for this flow.

### Message Routing

- With exactly two room participants, an omitted recipient is inferred as the participant other than the sender.
- With more than two participants, an explicit recipient is required.
- A message can target one named agent or **all** participants.
- CLI syntax: `--to <agent-name>` for direct messages and `--to all` for broadcasts. The stored broadcast marker is `all`.
- Explicitly naming an unknown direct recipient registers that participant. Recipient inference uses the resulting membership, including the sender: a new sender joining an existing two-agent room makes it a three-agent room and must specify a recipient.
- An omitted recipient is accepted only when that resulting membership is exactly two. With fewer than two participants, or with more than two, an explicit recipient is required.
- A broadcast registers the sender when needed and does not register an agent named `all`. It is rejected when no other participant is already known; the first exchange that introduces a second participant is a direct message.

### Room Information

```bash
ai-note-pair info --room projectx [--json]
```

Inspect room data without consuming messages or changing membership/read state. Include:

- Room name, creation time, and last modification time.
- Total message count and participant count.
- Each known participant's name and sent-message count, including participants with zero sent messages.

The sent-message count is based on the sender identity; each broadcast counts as one sent message. Counts can be derived from stored messages.

Example after Alice sends the first message to Bob:

```text
Room: projectx
Participants: 2
Messages: 1

Agent    Messages sent
alice    1
bob      0
```

Bob can inspect this information to discover the name already registered for him, then use `--name bob` to read or send. The example abbreviates the full output by omitting timestamps.

### Read as Synchronization

```bash
ai-note-pair read --room <room-name> --name <agent-name>
```

The application owns unread tracking; agents do not need to maintain timestamps or message cursors themselves.

1. Load the requesting agent's last read message ID for this room; the initial cursor is `0`.
2. Return every message with an ID greater than that cursor, in ascending ID order, through a fixed upper ID for this read.
3. Advance only that agent's cursor to the highest returned message ID and record `read_at`.
4. If there are no messages after the cursor, return an empty result without advancing it.

Example: if agent `alice` last read ID `4` and the room now contains messages through ID `9`, return IDs `5` through `9`, even if all those messages are addressed to other agents. Alice's cursor becomes `9`; the other agents' cursors are unchanged.

Read state is per agent. Reading a broadcast as one agent must not mark it read for other agents. Message IDs determine synchronization order; timestamps record when events happened.

All messages after the cursor are included, regardless of sender or recipient, including the reader's own messages and broadcasts. Sending a message does not advance the sender's read cursor, since that could skip intervening conversation.

Recipients indicate whom a message addresses; they neither restrict visibility nor determine which messages trigger a read. The same synchronization behavior applies to two-agent and larger rooms.

An agent's first read returns the full room history from cursor `0`, including messages sent before that agent was registered. A broadcast follows the same read rules as any other message.

Messages arriving beyond the fixed upper ID remain eligible for a later read.

An unregistered name cannot read. `read` does not create a participant; the name must already have been registered by a send. A missing reader leaves membership and cursors unchanged.

The command selects messages and remembers that read's upper ID before writing output. It advances the cursor only after the output has been produced, and only on the same room instance that was read. Archiving that room and creating a new one with the same name does not let the older read mark the new room. If output fails, the cursor stays where it was and a later read returns the same messages. Once the cursor has advanced, the MVP has no replay command: a caller that loses the output cannot ask the room for that range again. The application does not claim that an external agent consumed a successful read.

Simultaneous reads for the same agent are allowed. Cursor updates never move backward: acknowledgment sets the cursor to this read's upper ID only when that ID is greater than the stored cursor. Overlapping reads may deliver the same range; the stored cursor ends at the highest acknowledged ID.

### Database Schema (SQLite)

The following representation uses one read cursor per agent in each room.

- **agents**:
  - `name`: Room participant identity, unique within the room.
  - `last_read_message_id`: Last message ID returned by a read, initially `0`.
  - `read_at`: Time the cursor last advanced, initially NULL.
- **messages**:
  - `id`: INTEGER PRIMARY KEY, ordered within the room.
  - `timestamp`: Message creation time.
  - `sender_name`: Supplied sender identity.
  - `recipient_name`: Direct recipient or an unambiguous broadcast marker.
  - `content`: TEXT, supporting large messages.
  - `attachment_paths`: JSON array of room-relative attachment references.
- **metadata**: Room metadata stored as key/value pairs, including creation and modification times.

Per-agent cursors track independent consumption of the shared history without needing per-message read receipts.

Use `PRAGMA user_version` for schema versioning, starting at version `1`.

## Message Content and CLI Output

Send interface:

```bash
ai-note-pair send --room <room-name> --name <agent-name> [--to <recipient>] --message <text>
ai-note-pair send --room <room-name> --name <agent-name> [--to <recipient>] --message-file <path>
ai-note-pair send --room <room-name> --name <agent-name> [--to <recipient>] --message-file -
```

- Accept inline text, a UTF-8 file, or stdin (`--message-file -`).
- Select exactly one message input source.
- Allow repeatable `--attachment <path>` options; copy attachments into the room and store relative references. If that filename already exists in the room, store the new copy under a numeric suffix (`notes.txt`, then `notes-2.txt`) and leave the earlier file unchanged.
- Include attachment metadata in read results.
- Support human-readable output and machine-readable JSON (`--json`).
- Preserve message IDs and sender/recipient information in read results.

## Stack

- **Language**: Python.
- **Package manager**: `uv`.
- **CLI framework**: Typer.
- **Database**: SQLite via the Python standard library.
- **JSON**: Python standard library for attachment references and structured output.
- **Distribution**: Installable Python package exposing the global `ai-note-pair` command through a console entry point.

## Implementation Constraints

- All code, commands, and user-facing CLI text are in English.
- Rooms and their read state are isolated.
- Attachments remain local to their rooms and move with archived rooms.
- Support MB-scale message content.
- Concurrent local CLI invocations must preserve consistent routing and read state.
- Provide clear errors for missing rooms, invalid recipient selection, attachment failures, and corrupted databases.

## Commands (MVP)

- `create-room --name <room-name>`: Initialize an empty room.
- `info --room <room-name> [--json]`: Show room metadata, participants, and sent-message counts without changing read state.
- `send --room <room-name> --name <agent-name> [--to <recipient>] ...`: Send a direct or broadcast message.
- `read --room <room-name> --name <agent-name>`: Return unread conversation context and update that agent's read state.
- `archive --room <room-name>`: Archive a room with a timestamp suffix.
- `list-rooms [--archived]`: List active or archived rooms.

Message commands support the content and output options above. Participants are registered implicitly through message sending.

## Future Extensions

- Message reactions or annotations.
- Room templates or initialization scripts.
- Conversation export to Markdown or JSON.
- Web UI for monitoring.
