# ai-note-pair

![ai-note-pair — local persistent chat for AI agents](banner-v1.jpg)

See [USAGE.md](USAGE.md) for command contracts, JSON responses, and copyable examples, including multiline messages via heredoc.

Local persistent chat for two or more AI agents on the same machine. Agents run independently and use the `ai-note-pair` command to leave notes in a shared room. The app stores messages, attachments, and a separate read cursor for each agent. It does not call models, assign roles, or authenticate names.

## Install

Application code lives in `src`. From the repository root, install the command onto your PATH:

```bash
uv tool install --editable ./src
ai-note-pair --help
```

After that, `ai-note-pair` and `ai-note-pair-mcp` run from any directory. The editable install follows this checkout, so later source changes are picked up without reinstalling. The server does not read `AGENTS.md`, `README.md`, or `USAGE.md`. See [AGENTS.md](AGENTS.md) for the operating rules.

## MCP

`ai-note-pair-mcp` is a local stdio server for the same rooms. Each client starts its own process.

Run `ai-note-pair-mcp --help` and use the printed `Executable:` path as the client `command`. `--help` and `--version` exit without starting the server. Unknown arguments return an error. The server starts when that executable is launched with no arguments.

This repository does not edit client settings. Formats differ; one common shape is:

```json
{
  "mcpServers": {
    "ai-note-pair": {
      "command": "/absolute/path/printed/as/Executable",
      "args": [],
      "env": {
        "AI_NOTE_PAIR_HOME": "/optional/storage/root"
      }
    }
  }
}
```

Omit `env` to use `~/.config/ai-note-pair/`. `args` may be empty. Tools are `create_room`, `list_rooms`, `room_info`, `send_message`, `read_messages`, and `archive_room`.

Call `room_info` to discover your registered name and `read_messages` when the user says messages are available; do not poll. Consume `pending` from `send_message`. MCP advances only that agent's cursor when writing a successful response starts. Interrupted output remains marked read. Errors or cancellation before writing starts leave messages pending. Retrying a committed send can create a duplicate.

Rooms are stored in `~/.config/ai-note-pair/`. Set `AI_NOTE_PAIR_HOME` to use a different directory. Active rooms are `rooms/<room-name>/`. Archiving moves a room to `archived/<room-name>-<YYYYMMDDhhmmss>/`, using UTC. A second archive in the same second gets a `-2` suffix.

## Create a room and discover names

```bash
ai-note-pair create-room --name projectx
ai-note-pair send --room projectx --name alice --to bob --message "hello bob"
ai-note-pair info --room projectx
```

`info` does not consume messages. Bob can read that output, see that `alice` already registered `bob`, and then pass `--name bob`.

## Send

Provide exactly one message source:

```bash
ai-note-pair send --room projectx --name bob --message "hello alice"
ai-note-pair send --room projectx --name bob --message-file notes.txt --to alice
ai-note-pair send --room projectx --name bob --message-file - --to alice < notes.txt
```

Repeat `--attachment <path>` to copy files into the room. Stored references look like `attachments/notes.txt`. If that filename already exists, the new copy is `attachments/notes-2.txt`.

A successful send marks the new message read by its author. If that author still had unread messages, they are printed with the confirmation, including notes addressed to someone else and broadcasts. JSON lists those messages in `pending` and does not repeat the message just sent. The cursor moves only after that output is written, and only through the new message id, so anything that arrives later stays unread. The other agents' cursors do not move. A failed send leaves unread messages in place. If the confirmation cannot be written, the cursor stays where it was.

## Recipients

- With exactly two participants, `--to` may be omitted and the message goes to the other agent.
- With any other participant count, pass `--to <agent-name>`.
- `--to all` broadcasts one message. The name `all` cannot be an agent. A broadcast is rejected until some other participant is already known.
- A new sender counts toward the participant total before routing is decided. Joining a two-agent room requires an explicit recipient.

## Read

`read` returns the shared history after that agent's cursor, including messages addressed to someone else, then advances only that cursor.

```bash
ai-note-pair read --room projectx --name bob
ai-note-pair read --room projectx --name alice --json
```

The first read returns every message already in the room. A later read returns only newer ids. An empty read leaves the cursor unchanged. The cursor moves only after the command has written its output; if that output fails, the next read returns the same messages. After a successful read or send, this version has no replay command.

Add `--json` to `info`, `send`, and `read` for machine-readable output. Errors go to stderr with a non-zero exit code and leave stdout empty.

## Archive

```bash
ai-note-pair archive --room projectx
ai-note-pair list-rooms
ai-note-pair list-rooms --archived
ai-note-pair create-room --name projectx
```

The active name can be reused. The new room does not contain the archived messages.

## Checks

From `src`:

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## Limitations

- Agents must share this machine's filesystem. There is no HTTP server, authentication, or model integration. The MCP interface is local stdio only: no resources, prompts, polling, or background notification.
- MCP acknowledgment is optimistic: interrupted responses can contain already acknowledged messages that the client did not receive.
- Room and agent names are 1–64 characters: letters, digits, `_`, or `-`, starting with a letter or digit.
- Only schema version 1 is supported. A corrupt database or a different schema version is rejected instead of being rewritten.
- A successful `read` is not replayable. Concurrent reads by the same name may deliver an overlapping range; the stored cursor never moves backward.
