# ai-note-pair MCP Implementation Plan

Implementer: Grok 4.7.

## Objective

Add a local MCP interface to the existing app so AI clients discover available operations, parameters, and behavior directly from tool definitions. Preserve the working CLI and reuse its Python application logic, SQLite storage, and attachments.

This plan covers the next implementation phase; the initial CLI plan is complete. Follow the current SPEC for existing behavior, including synchronization during `send`.

## Agreed Scope

- Use the official Python MCP SDK and the `stdio` transport. Each client launches its own server process; all processes share the same storage root.
- Call the app's Python logic directly rather than invoking shell commands or duplicating domain behavior.
- Keep `room` and agent `name` explicit in tool arguments. Preserve permissive identity, implicit registration, recipient inference, and broadcasts.
- Use optimistic MCP acknowledgment: advance the cursor when writing a serialized successful response starts. Failures or cancellation before that start leave it unchanged; interruptions afterward do not undo it. No separate acknowledgment tool is required, and receipt by the model itself is not guaranteed. Disconnections at the delivery boundary can have an uncertain outcome.
- The user tells an agent when messages are available; the agent then calls `read_messages`. Do not implement polling, automatic agent activation, or background notifications.
- Preserve local-only operation. HTTP, remote hosting, authentication, resources, prompts, and model execution are outside this phase.

## Repository and Working Rules

All application code, package/dependency changes, tests, and tooling stay inside `./src`. Root documents can describe installation and usage; runtime operation must not depend on them. Use isolated temporary storage in tests.

Use idiomatic Python, appropriate typing, and the existing project conventions. Work in the milestones below, adding meaningful tests with each capability. Choose a modest design rather than introducing a new framework around the app. Preserve existing fixes for room identity, archive coordination, legacy databases, attachment failures, and path containment.

Do not commit unless the user asks. Report each milestone's behavior, validation, and material limitations. Check off only completed milestones.

## Tool Surface

The following is the intended public surface. Choose consistent response schemas, preserving existing message fields and semantics.

| Tool | Arguments | Result and behavior |
| --- | --- | --- |
| `create_room` | `name` | Creates an empty room and returns its identity/path information. |
| `list_rooms` | `archived=false` | Returns active or archived room names. |
| `room_info` | `room` | Returns path, dates, participants, total messages, and messages sent per agent; does not consume conversation. |
| `send_message` | `room`, `name`, `message`, optional `to`, optional `attachments` | Sends text with local attachment paths, returns confirmation and pending shared conversation, and acknowledges the sender through the sent ID when response writing starts. |
| `read_messages` | `room`, `name` | Returns all conversation after that agent's cursor and acknowledges the delivered range. |
| `archive_room` | `room` | Archives the room and returns the resulting archive information. |

Text is a string, including multiline content; there is no shell quoting, stdin, or message-file option in MCP calls. Attachments remain local files with room-relative references in results. Room information supplies the directory needed to resolve those references.

Tool descriptions and argument schemas must be sufficient for an agent to discover its registered name, address messages, and understand consumption of shared history without consulting a long manual. Describe the side effects of `read_messages` and `send_message` explicitly. Structured results should also be accessible to clients that consume text tool results.

## Milestones

### 1. MCP Server Foundation and Discovery

Add the SDK dependency and an installed server entry point, establish the stdio lifecycle, and expose `create_room`, `list_rooms`, and `room_info` using the existing logic.

Acceptance criteria:

- A client can start the installed server from outside the repository, initialize it, discover tools, and call these room operations.
- Descriptions and schemas are concise, typed, and self-contained; results have consistent structured contracts.
- Storage defaults and `AI_NOTE_PAIR_HOME` work as they do in the CLI. Two independent server processes can inspect the same rooms.
- Stdout contains MCP messages only; diagnostics use stderr. Domain failures return useful MCP tool errors without terminating the server.
- Protocol-level tests cover initialization, discovery, room operations, and a failed call followed by a successful call.

### 2. Shared Messaging and Response Delivery

Expose `send_message` and `read_messages`, preserving pending-context delivery and independent read cursors. Integrate cursor acknowledgment with the actual response-delivery boundary supported by the chosen SDK/transport.

Acceptance criteria:

- A two-agent exchange supports recipient inference; a four-agent room requires explicit recipients. Broadcasts retain existing rules.
- Reads include messages addressed to other agents. Sends return pending history without echoing the new message, then acknowledge through its ID for the sender only.
- Handler returns and queued results do not acknowledge conversation. The boundary is the start of writing a serialized successful response; interrupted writes or flushes remain acknowledged.
- Tool failures, serialization failures, and cancellation before response writing starts do not consume pending conversation. A committed send may survive a failed response; document the retry implications.
- Each result captures a bounded range. Later arrivals remain pending, cursor updates never move backward, and no room lock is held while waiting for output.
- Tests cover two/four agents, multiline Unicode text, broadcasts, independent cursors, successful delivery, serialization failure, write/flush interruptions after acknowledgment, cancellation before and after writing starts, and later arrivals. Include transport-level validation rather than testing handler returns alone.

### 3. Attachments, Archiving, and Cross-Interface Consistency

Complete `archive_room` and attachment support. Verify that MCP processes and CLI invocations can operate on the same storage without divergence.

Acceptance criteria:

- Attachments are copied and returned with usable references; collisions, invalid paths, and permission failures retain clear errors and cleanup behavior.
- Archiving preserves conversation, attachments, and cursors, supports active-name reuse, and does not split an in-flight send from its files.
- Pending acknowledgments stay bound to the original room instance after archiving/recreation. Existing schema-1 rooms continue to work.
- CLI and MCP observe the same participants, messages, and read state. Reading or sending through one interface updates the state seen through the other.
- Tests cover representative concurrent operations across clients/interfaces, archive overlap, name reuse, and storage failures. Preserve existing regression coverage rather than duplicating every unit test through MCP.

### 4. Installation, Agent Guidance, and Final Validation

Finish packaging and documentation and validate the server as a client would use it.

Acceptance criteria:

- Provide one short client configuration example specifying executable, arguments, and optional storage environment. Clarify that each client's settings format may differ; do not modify the user's clients automatically.
- Update SPEC and README to include the MCP interface and delivery policy. Keep USAGE a concise practical guide; avoid historical context and extensive per-agent instructions.
- Tool descriptions carry operational guidance: discover your registered name, read when the user requests it, and consume pending context returned by sends.
- A real SDK client launches the installed server and completes creation, name discovery, two-agent exchange, growth to four agents, independent synchronization, attachment sharing, and archiving/name reuse.
- Full tests, Ruff, and formatting checks pass. Report exact validation commands and remaining limitations.
- Confirm the installed server has no dependency on root-level application files and all required development/runtime supporting files remain under `./src`.

## Completion Checklist

- [x] 1. MCP server foundation and discovery
- [x] 2. Shared messaging and response delivery
- [x] 3. Attachments, archiving, and cross-interface consistency
- [x] 4. Installation, agent guidance, and final validation

## Primary References

- Python SDK: https://github.com/modelcontextprotocol/python-sdk
- Tools and structured results: https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- Stdio transport: https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
