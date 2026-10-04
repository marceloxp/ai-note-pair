# ai-note-pair

Local persistent chat for two or more AI agents on the same machine. Agents run independently and use the `ai-note-pair` command to leave notes in a shared room. The app stores messages, attachments, and a separate read cursor for each agent. It does not call models, assign roles, or authenticate names.

## Install

Application code lives in `src`. From that directory:

```bash
cd src
uv sync
uv run ai-note-pair --help
```

Rooms are stored in `~/.config/ai-note-pair/`. Set `AI_NOTE_PAIR_HOME` to use a different directory. Active rooms are `rooms/<room-name>/`. Archiving moves a room to `archived/<room-name>-<YYYYMMDDhhmmss>/`, using UTC. A second archive in the same second gets a `-2` suffix.

## Create a room and discover names

```bash
uv run ai-note-pair create-room --name projectx
uv run ai-note-pair send --room projectx --name alice --to bob --message "hello bob"
uv run ai-note-pair info --room projectx
```

`info` does not consume messages. Bob can read that output, see that `alice` already registered `bob`, and then pass `--name bob`.

## Send

Provide exactly one message source:

```bash
uv run ai-note-pair send --room projectx --name bob --message "hello alice"
uv run ai-note-pair send --room projectx --name bob --message-file notes.txt --to alice
uv run ai-note-pair send --room projectx --name bob --message-file - --to alice < notes.txt
```

Repeat `--attachment <path>` to copy files into the room. Stored references look like `attachments/notes.txt`. If that filename already exists, the new copy is `attachments/notes-2.txt`.

## Recipients

- With exactly two participants, `--to` may be omitted and the message goes to the other agent.
- With any other participant count, pass `--to <agent-name>`.
- `--to all` broadcasts one message. The name `all` cannot be an agent. A broadcast is rejected until some other participant is already known.
- A new sender counts toward the participant total before routing is decided. Joining a two-agent room requires an explicit recipient.

## Read

`read` returns the shared history after that agent's cursor, including messages addressed to someone else, then advances only that cursor.

```bash
uv run ai-note-pair read --room projectx --name bob
uv run ai-note-pair read --room projectx --name alice --json
```

The first read returns every message already in the room. A later read returns only newer ids. Sending does not advance the sender's cursor. An empty read leaves the cursor unchanged. The cursor moves only after the command has written its output; if that output fails, the next read returns the same messages. After a successful read, this version has no replay command.

Add `--json` to `info`, `send`, and `read` for machine-readable output. Errors go to stderr with a non-zero exit code and leave stdout empty.

## Archive

```bash
uv run ai-note-pair archive --room projectx
uv run ai-note-pair list-rooms
uv run ai-note-pair list-rooms --archived
uv run ai-note-pair create-room --name projectx
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

- Agents must share this machine's filesystem. There is no remote transport, authentication, or model integration.
- Room and agent names are 1–64 characters: letters, digits, `_`, or `-`, starting with a letter or digit.
- Only schema version 1 is supported. A corrupt database or a different schema version is rejected instead of being rewritten.
- A successful `read` is not replayable. Concurrent reads by the same name may deliver an overlapping range; the stored cursor never moves backward.
