# ai-note-pair Implementation Plan

Implementer: Grok 4.7.

## Goal and Scope

Build the local Python CLI described in [SPEC.md](SPEC.md): persistent room-based chat for two or more independently running AI agents on the same machine. Deliver an installable `ai-note-pair` command with tests.

Treat the SPEC as the product reference. This plan defines implementation milestones and acceptance criteria, not a prescribed internal architecture. Choose appropriate modules, abstractions, and libraries within the agreed stack.

Do not add model execution, provider configuration, authentication, enforced agent roles, remote communication, Docker support, or future extensions to the MVP.

## Repository Boundary

All application code and supporting development files belong inside `./src`. This includes Python package metadata (`pyproject.toml`), the dependency lockfile, tests, tool configuration, and any build or execution scripts. Keep virtual environments, generated build files, and test artifacts inside `./src` or outside the repository as appropriate.

The repository root holds project documentation and repository housekeeping only. The installed app must not depend on root-level files. Choose the Python package layout inside `./src`; the directory is the application project root, not necessarily the package itself.

Runtime room data belongs in the local storage location described by the SPEC, outside the repository. Tests must use isolated temporary storage rather than the user's real rooms.

## Working Approach

- Complete one milestone at a time, including its relevant tests, before moving on.
- Keep changes focused on a usable capability; avoid both a single monolithic implementation and separate tasks for every function.
- Use idiomatic Python, clear names, appropriate type annotations, and a modest separation between CLI handling and persistence/domain behavior.
- Prefer standard-library capabilities and a small dependency set. Use `uv` and choose Click or Typer.
- Handle database connections, transactions, and filesystem resources deliberately. Surface actionable CLI errors and meaningful exit codes.
- Test observable behavior and failure cases, rather than private implementation details. Add tests alongside features instead of postponing them to the final milestone.
- At each milestone, report the resulting capability, validation performed, and any remaining limitation. Update the checklist only when its acceptance criteria are met.

## Decisions to Resolve During Implementation

The SPEC still has a few open policy choices. Before implementing the affected behavior, choose and document a simple, consistent policy in the SPEC, with tests. Do not silently change established requirements. If a choice materially changes the intended product behavior, leave it explicit for discussion rather than assuming approval.

- Broadcast syntax and whether its token is reserved as an agent name.
- Sending without a recipient when fewer than two participants are known, and broadcasting without another known participant.
- Whether an unknown reader joins implicitly or must first be registered through a send.
- Simultaneous reads under the same identity, and when a cursor advances relative to output delivery. Document realistic failure/retry behavior without claiming guaranteed consumption by an external agent.
- Archive timestamp timezone and collisions within the same second.

These are small policy decisions, not invitations to expand scope with new subsystems.

## Milestones

### 1. Package Foundation and Room Storage

Establish the Python project under `./src`, the installable CLI entry point, and isolated SQLite-backed rooms. Implement `create-room` and active `list-rooms`, with schema versioning and room metadata.

Acceptance criteria:

- The CLI runs through the project environment and offers command help.
- Rooms persist across separate invocations and are isolated from one another.
- A new room starts empty with schema version `1`.
- Duplicate creation and invalid room paths produce clear errors without overwriting data or escaping the storage directory.
- Tests cover creation, listing, isolation, and representative failures using temporary storage.

### 2. Message Sending, Participants, and Room Information

Implement direct and broadcast sending, permissive identity, implicit participant registration, recipient inference, and `info`. Start with inline message input and establish human-readable and JSON output conventions.

Acceptance criteria:

- The first direct send registers sender and recipient together with the message; repeated names reuse the same participant.
- With two participants, an omitted recipient resolves to the other agent. With more than two, an explicit recipient is required.
- A new sender cannot bypass recipient requirements by relying on the membership count before its own registration.
- Broadcasts create a single message and no participant representing the broadcast token.
- `info` shows room metadata, total messages, participant count, and messages sent per agent, including zero-message recipients.
- Inspection does not change membership or read state. Failed sends do not leave partial registration or messages.
- Tests cover the two-agent flow, growth to four participants, routing errors, broadcasts, and information counts.

### 3. Per-Agent Conversation Synchronization

Implement `read` using independent per-agent cursors. This is shared conversation history, not a recipient-filtered inbox.

Acceptance criteria:

- A first read returns the full history; later reads return only IDs greater than that agent's last read ID, in ascending order.
- Every new message is included, regardless of sender or recipient, including the reader's own messages and broadcasts.
- Only the requesting agent's cursor advances, to the last returned ID; an empty read does not advance it.
- Sending never advances the sender's cursor.
- Each read has a fixed upper ID; messages arriving afterward remain available for the next read.
- Tests demonstrate independent synchronization in rooms with two and four agents, including new messages addressed exclusively to other agents and history preceding participant registration.
- Concurrent operations preserve message and cursor consistency; test representative concurrent sends/reads, including reads under the same identity, against the documented policy.

### 4. Large Message Input and Attachments

Extend sending with UTF-8 file input, stdin, and repeatable attachments. Include attachment metadata in read output.

Acceptance criteria:

- Exactly one message input source is accepted: inline text, file, or stdin.
- Unicode and MB-scale content survive storage and retrieval without truncation.
- Attachments are copied into their room and referenced by relative paths. Identical source filenames do not overwrite previous attachments.
- Missing/unreadable inputs and copy failures produce clear errors without leaving a partially committed message.
- Tests cover input selection, stdin/file round trips, multiple attachments, filename collisions, and relevant failure cleanup.

### 5. Archiving and Storage Failure Handling

Implement `archive` and archived `list-rooms`. Complete handling of database and filesystem failures, including operations overlapping with archiving.

Acceptance criteria:

- Archiving preserves the database, participant cursors, and attachments together in the timestamp-suffixed directory.
- The active name can be reused for a new independent room.
- Repeated archives preserve prior archives, including timestamp collisions.
- Concurrent or failed archive operations do not silently lose committed data or report success after a partial move.
- Missing rooms, corrupted databases, unsupported schema versions, and relevant filesystem failures have actionable errors.
- Tests cover archive preservation, name reuse, archived listing, and representative failure/concurrency cases.

### 6. End-to-End Validation and Delivery

Verify the complete workflow and package installation. Update the root README with setup and usage instructions and reconcile the SPEC with implemented policy choices.

Acceptance criteria:

- An installed `ai-note-pair` command works from outside the repository without depending on root-level files.
- A full CLI workflow covers room creation, initial name discovery through `info`, two-agent conversation, expansion to four agents, independent reads, attachments, archiving, and name reuse.
- Machine-readable output is valid JSON; errors use nonzero exit codes and do not contaminate successful JSON output.
- The test suite and selected formatting/lint checks pass. Document exact commands, run from `./src`, and any material limitations.
- README explains installation, content input, recipient rules, and per-agent synchronization with practical examples.
- No required application code, dependencies, configuration, or test tooling has been placed at the repository root.

## Completion Checklist

- [x] 1. Package foundation and room storage
- [x] 2. Message sending, participants, and room information
- [x] 3. Per-agent conversation synchronization
- [x] 4. Large message input and attachments
- [x] 5. Archiving and storage failure handling
- [x] 6. End-to-end validation and delivery
