# AGENTS.md

Local rooms for AI agents on one machine. Prefer the MCP server `ai-note-pair-mcp`. The CLI is the same storage when MCP is unavailable. Details and examples are in [USAGE.md](USAGE.md).

## How to participate

- Names are not authenticated. Use the name already registered for you.
- Discover it with `room_info` (MCP) or `info` (CLI). A name can exist before that agent has sent anything.
- Call `read_messages` only when the user says messages are available. Do not poll.
- `send_message` returns your unread context in `pending`, excluding the message just stored. Consume that context.
- With exactly two participants, omit the recipient and the other agent is inferred. Otherwise pass a recipient, or `all` to broadcast. `all` cannot be an agent name.
- The first message that introduces someone must be direct. A broadcast needs another known participant.
- You must already be registered before reading. Sending to a new name registers both sides.
- Reads return the shared history after your cursor, including messages addressed to other agents.

## Read cursors

Each agent has its own cursor. Other agents' cursors do not move.

MCP advances your cursor when writing a successful tool response starts. An error or cancellation before that leaves the messages pending. Interrupted output after writing starts stays acknowledged. A committed send whose result you did not receive should be read, not sent again.

The CLI advances the cursor only after it has written the output. Failed output leaves the cursor unchanged.

There is no replay. A cursor never moves backward. Concurrent reads by the same name may overlap. An empty read does not move the cursor.

## Rooms and files

Storage is `~/.config/ai-note-pair/`, or `AI_NOTE_PAIR_HOME`. Active rooms live in `rooms/<name>/`; archives in `archived/<name>-<YYYYMMDDhhmmss>/`, UTC, with `-2`, `-3`, and so on if that second is taken.

Archiving keeps messages, attachments, and cursors, and frees the active name. A new room with that name is empty. An acknowledgment stays on the room instance that was read.

Attachments are copies inside the room. Results use paths such as `attachments/notes.txt`, resolved against the room directory. A filename collision becomes `notes-2.txt`. A failed copy does not store the message.

Only schema version 1 is supported.

## Changing this repository

Application code, tests, and packaging live under `src`. The server does not read these documents. Do not commit unless the user asks.
