# ai-note-pair — Agent Usage

Use MCP when the server is available. After `uv tool install --editable ./src`, run `ai-note-pair-mcp --help` and set the client `command` to the `Executable:` path it prints. A configuration example is in [README.md](README.md), and each client's settings format may differ. Tools: `create_room`, `list_rooms`, `room_info`, `check_messages`, `send_message`, `read_messages`, `archive_room`. Discover your registered name with `room_info`. `check_messages` reports the unread count and does not move the cursor. Call `read_messages` when the user says messages are available; do not poll. Consume the `pending` messages from `send_message`. Pass the text as `message` (no stdin or message-file). Attachment arguments are local paths; results look like `attachments/notes.txt`, resolved against `path` from `room_info`.

The CLI below is the same storage when MCP is not available. Run `ai-note-pair` from any directory. Every participant uses `~/.config/ai-note-pair/`, or the directory in `AI_NOTE_PAIR_HOME`.

## Commands

| Command | Usage | Result |
| --- | --- | --- |
| Create | `create-room --name ROOM` | Creates an empty room; an existing name is rejected. |
| Inspect | `info --room ROOM [--json]` | Shows the room path, dates, participants, and message counts without consuming messages. |
| Check | `check --room ROOM --name AGENT [--json]` | Counts messages after that agent's cursor without marking them read. |
| Send | `send --room ROOM --name AGENT [--to RECIPIENT] --message TEXT [--json]` | Confirms the send and returns pending conversation for the sender. |
| Read | `read --room ROOM --name AGENT [--json]` | Returns new conversation messages and marks them read for that agent. |
| Archive | `archive --room ROOM` | Archives the room with a UTC timestamp suffix, preserving its data and freeing the name. |
| List | `list-rooms [--archived]` | Lists sorted room names, one per line; an empty list produces no output. |

Prefix each command with `ai-note-pair`. Use `ai-note-pair COMMAND --help` for options. `--json` is supported by `info`, `check`, `send`, and `read`.

MCP marks the captured conversation read when writing a successful response starts, even if output is later interrupted. Errors or cancellation before writing starts leave it pending. CLI commands acknowledge after successful output.

## Names and Recipients

- Names are case-sensitive: 1–64 ASCII letters, digits, `_`, or `-`, starting with a letter or digit. The agent name `all` is reserved.
- A successful direct send registers both sender and recipient. Reusing a name reuses that agent's identity in the room.
- Inspect `info` to discover the name registered for you. You must be registered before using `read`.
- With exactly two participants, omit `--to` to address the other agent.
- With more than two participants, specify `--to NAME` or `--to all`. A new sender counts toward that total.
- A first message must establish another participant through a direct recipient. Broadcasts require another known participant.

## Start a Conversation

```bash
ai-note-pair create-room --name projectx
ai-note-pair send --room projectx --name alice --to bob --message 'Please review the parser.'

# Bob discovers his name, reads, and replies.
ai-note-pair info --room projectx --json
ai-note-pair read --room projectx --name bob --json
ai-note-pair send --room projectx --name bob --message 'I will review it now.' --json

# Add two more agents; subsequent sends require an explicit recipient.
ai-note-pair send --room projectx --name carol --to dave --message 'I will check the tests.'
ai-note-pair send --room projectx --name alice --to all --message 'Please report your results.'
```

## Message Input and Attachments

Choose exactly one input: `--message TEXT`, `--message-file PATH` (UTF-8), or `--message-file -` (stdin).

For multiline messages, use a quoted heredoc directly:

```bash
ai-note-pair send --room projectx --name alice --to bob --message-file - --json <<'EOF_MESSAGE'
Please review:
1. Blank-line handling.
2. Invalid-record errors.

$HOME, `commands`, and $(commands) are literal text here.
EOF_MESSAGE
```

For existing files and attachments:

```bash
ai-note-pair send --room projectx --name alice --to bob --message-file notes.txt
ai-note-pair send --room projectx --name alice --to bob --message-file - < notes.txt
ai-note-pair send --room projectx --name alice --to bob \
  --message 'See the attached results.' --attachment results.json --attachment notes.txt
```

Attachments are copied into the room. Filename collisions receive numeric suffixes. Resolve returned paths such as `attachments/notes.txt` against the room's `path` from `info`.

## Reading and Sending

All agents read the shared conversation, including messages addressed to other participants. Each agent has its own cursor.

- `check` returns how many messages are after your cursor. It does not mark them read, so a later `read` still returns them.
- `read` returns messages after your cursor in ascending ID order. Your first read includes the entire history.
- `send` returns confirmation plus any conversation you have not yet read, excluding the new message itself. After successful output, your cursor includes both the returned context and your new message.
- Example: you read through `4`, messages `5` and `6` arrive, and you send `7`. The send response delivers `5` and `6` and marks you current through `7`.
- Messages arriving after the response's captured range remain pending. Other agents' cursors are unchanged.
- Failed output does not advance your cursor. A successful read is not replayable; concurrent reads under one name may overlap.

## JSON Responses

`info` returns `room`, `path`, `created_at`, `updated_at`, `participants`, `messages` (total count), and `agents` (objects with `name` and `messages_sent`). Registered recipients with no sends have count `0`.

`send` returns confirmation fields and a `pending` array:

```json
{
  "room": "projectx",
  "id": 7,
  "sender": "alice",
  "recipient": "bob",
  "timestamp": "2026-10-04T02:00:00Z",
  "pending": []
}
```

`read` returns `room`, `reader`, and a `messages` array. Both `messages` and `pending` contain objects of this shape:

```json
{
  "id": 6,
  "timestamp": "2026-10-04T01:59:00Z",
  "sender": "carol",
  "recipient": "dave",
  "content": "Tests passed.",
  "attachments": ["attachments/results.json"]
}
```

No pending content produces an empty array. Without `--json`, output is readable text; an empty `read` prints `No unread messages.`

`check` returns `room`, `reader`, and `unread`. Without `--json`, it prints `N unread messages.` (`1 unread message.` when the count is one).

## Errors and Archiving

Success exits with `0`. Application errors exit with `1`; invalid CLI syntax exits with `2`. Results go to stdout and errors to stderr. `--json` does not change error formatting. If the database is busy, wait and retry. An error after output can occur after a send was committed; check before retrying to avoid duplicate messages.

```bash
ai-note-pair archive --room projectx
ai-note-pair list-rooms --archived
ai-note-pair create-room --name projectx
```

Archived names look like `projectx-20261004020000`, with `-2`, `-3`, etc. on collisions. A reused active name starts a new, empty conversation. Message commands operate on active rooms.
