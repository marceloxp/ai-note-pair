# ai-note-pair Specification

## Vision

`ai-note-pair` is a framework enabling two AI agents to work collaboratively as a distributed pair programming team. One AI acts as the **Main/Engineer** (architectural decisions, specifications), while the other acts as the **Dev/Hands** (implementation, code execution). Communication happens through discrete "notes" (messages) stored in a persistent, room-based system.

The metaphor: Two colleagues working in the same office but at distant desks, shouting questions and answers to each other.

## Architecture

### Global Structure

```
~/.config/ai-note-pair/
├── config.json           # Global configuration (endpoints, API keys, etc.)
├── rooms/                # Active conversation rooms
│   └── <room-name>/
│       ├── ai-note-pair.db    # SQLite database (schema versioned via PRAGMA)
│       └── attachments/       # Shared files between agents
└── archived/             # Archived rooms (name reuse possible)
    └── <room-name>/
        ├── ai-note-pair.db
        └── attachments/
```

### Room Lifecycle

1. **Create**: Any AI can initialize a room with `ai-note-pair create-room --name <room-name>`
2. **Active**: Agents exchange messages via `ai-note-pair send`
3. **Read**: Agents retrieve conversation history via `ai-note-pair read`
4. **Archive**: Room moved to `archived/` via `ai-note-pair archive --room <room-name>`

### Database Schema (SQLite)

Each room has a single `ai-note-pair.db` with:

- **messages table**: Stores all exchanges between agents
  - `id` (INTEGER PRIMARY KEY)
  - `timestamp` (DATETIME)
  - `sender_name` (TEXT, agent nickname in slug format)
  - `sender_model` (TEXT, e.g., "gpt-4", "claude-3", "llama-2")
  - `content` (TEXT, supports large texts)
  - `attachment_paths` (JSON array or TEXT list of file references)

- **metadata table**: Room information
  - `key` (TEXT PRIMARY KEY, e.g., "created_at", "last_modified")
  - `value` (TEXT)

**Schema Versioning**: Use `PRAGMA user_version` to track schema evolution. On first run, set version to 1.

### Agent Identification

Each agent self-identifies with:
- **name** (slug format, includes model reference): e.g., `gpt4-main`, `claude3-hands`, `llama2-coder`
- **model** (metadata): e.g., `gpt-4`, `claude-3-opus`, `llama-2-70b`

Nickname format: `{model}-{role}` (lowercase, hyphens only)

First agent to `send` in a room auto-registers. Subsequent agents with the same name are rejected (name collision prevention).

### Message Exchange

**Send**:
```bash
ai-note-pair send --room <room-name> --name <agent-nickname> --message <text> [--attachment <file-path>]
```

- Inserts row into messages table
- Optionally stores attachments in `attachments/` folder
- Stores relative file paths in `attachment_paths`

**Read**:
```bash
ai-note-pair read --room <room-name> [--since <timestamp>] [--from <agent-name>]
```

- Retrieves messages (with optional filtering)
- Returns in chronological order
- Includes attachment metadata

### Global Configuration

`~/.config/ai-note-pair/config.json`:

```json
{
  "agents": {
    "gpt4-main": {
      "model": "gpt-4",
      "endpoint": "https://api.openai.com/v1",
      "api_key": "sk-..."
    },
    "claude3-hands": {
      "model": "claude-3-opus",
      "endpoint": "https://api.anthropic.com",
      "api_key": "..."
    }
  }
}
```

## Stack

- **Language**: Python
- **Package Manager**: `uv` (fast, dependency-locked)
- **CLI Framework**: Click or Typer (modern, intuitive)
- **Database**: SQLite3 (standard library)
- **Config**: JSON (stdlib json module)
- **Distribution**: Installable via `pip` (after building wheel), symlinked/aliased as global `ai-note-pair` command

## Implementation Notes

- All code, commands, and user-facing text in English
- Final deliverable: CLI executable accessible globally as `ai-note-pair`
- Rooms are isolated and versioned; schema changes tracked via `PRAGMA user_version`
- Attachments stay local to rooms; no cloud sync (keep it simple)
- Large text support: SQLite TEXT type handles MB-scale messages
- Error handling: Clear messages for missing rooms, name collisions, corrupted DBs

## Commands (MVP)

- `ai-note-pair create-room --name <room-name>` — Initialize a new room
- `ai-note-pair send --room <room-name> --name <agent-nickname> --message <text> [--attachment <path>]` — Send a message
- `ai-note-pair read --room <room-name> [--since <timestamp>] [--from <agent-name>]` — Retrieve messages
- `ai-note-pair archive --room <room-name>` — Archive a room (frees the name)
- `ai-note-pair list-rooms [--archived]` — List active or archived rooms

## Future Extensions

- Multi-agent rooms (>2 agents)
- Message reactions/annotations
- Room templates or initialization scripts
- Export to markdown or JSON
- Web UI for monitoring
