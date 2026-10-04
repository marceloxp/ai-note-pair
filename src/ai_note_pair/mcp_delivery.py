"""Acknowledge reads when a valid stdio response starts being written.

The Python MCP SDK writes a tool result into an in-memory stream before the
stdio task sends it. That handoff does not consume messages. Immediately before
writing a serialized successful response, this transport advances the cursor.
Cancellation before writing starts and error responses leave it unchanged.

This is optimistic: a failed write, failed flush, or later cancellation does
not undo acknowledgment. The client may miss already acknowledged messages.
Retrying a send can create a duplicate even when its response was interrupted.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
from mcp.server.stdio import (
    _claim_fd,
    _open_stdin_diversion,
    _open_stdout_diversion,
    _UnownedTextWrapper,
    stdio_server,
)

from ai_note_pair.errors import AiNotePairError
from ai_note_pair.messaging import UnreadBatch, acknowledge_read

logger = logging.getLogger(__name__)

_HOLD_ENV = "AI_NOTE_PAIR_MCP_HOLD"
_HOLD_LIMIT_SECONDS = 30.0

_lock = threading.Lock()
_pending: dict[str, UnreadBatch] = {}


def schedule_delivery(request_id: object, batch: UnreadBatch) -> None:
    """Remember a cursor update until the matching response starts writing."""
    with _lock:
        _pending[str(request_id)] = batch


def wait_for_test_hold() -> None:
    """Pause messaging tools while a test hold file exists.

    Unset in normal use. The wait is capped so a leftover file cannot stall
    the server indefinitely.
    """
    raw = os.environ.get(_HOLD_ENV)
    if not raw:
        return
    holder = Path(raw)
    deadline = time.monotonic() + _HOLD_LIMIT_SECONDS
    while holder.exists() and time.monotonic() < deadline:
        time.sleep(0.05)


def note_response_started(text: str) -> None:
    """Advance cursors for successful responses about to be written."""
    for batch in _delivered_batches(text):
        try:
            acknowledge_read(batch)
        except AiNotePairError:
            logger.exception("cursor update failed as the response started writing")


def _delivered_batches(text: str) -> list[UnreadBatch]:
    delivered: list[UnreadBatch] = []
    with _lock:
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict) or "id" not in payload:
                continue
            batch = _pending.pop(str(payload["id"]), None)
            if batch is not None and _matches(batch, payload):
                delivered.append(batch)
    return delivered


def _matches(batch: UnreadBatch, payload: dict[str, Any]) -> bool:
    if "error" in payload:
        return False
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("isError") is True:
        return False
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        return False
    expected_ids = [message.id for message in batch.messages]
    if "pending" in structured:
        pending = structured.get("pending")
        if not isinstance(pending, list):
            return False
        ids = [item.get("id") for item in pending if isinstance(item, dict)]
        return (
            structured.get("room") == batch.room
            and structured.get("sender") == batch.reader
            and structured.get("id") == batch.delivered_id
            and ids == expected_ids
        )
    messages = structured.get("messages")
    if not isinstance(messages, list):
        return False
    ids = [item.get("id") for item in messages if isinstance(item, dict)]
    return (
        structured.get("room") == batch.room
        and structured.get("reader") == batch.reader
        and ids == expected_ids
    )


class _AckingStdout:
    """Apply optimistic cursor updates at the start of a response write."""

    def __init__(self, raw: Any, on_start: Callable[[str], None]) -> None:
        self._raw = raw
        self._on_start = on_start

    async def write(self, text: str) -> int:
        await anyio.lowlevel.checkpoint_if_cancelled()
        self._on_start(text)
        return await self._raw.write(text)

    async def flush(self) -> None:
        await self._raw.flush()


@asynccontextmanager
async def delivering_stdio() -> AsyncIterator[tuple[Any, Any]]:
    """Stdio streams that acknowledge successful responses as writing starts."""
    stdin_buffer, restore_stdin = _claim_fd(0, sys.stdin, "rb", _open_stdin_diversion)
    stdout_buffer, restore_stdout = _claim_fd(1, sys.stdout, "wb", _open_stdout_diversion)
    stdin = anyio.wrap_file(_UnownedTextWrapper(stdin_buffer, encoding="utf-8", errors="replace"))
    raw_stdout = anyio.wrap_file(_UnownedTextWrapper(stdout_buffer, encoding="utf-8"))
    stdout = _AckingStdout(raw_stdout, note_response_started)
    try:
        async with stdio_server(stdin=stdin, stdout=stdout) as streams:
            yield streams
    finally:
        if restore_stdout is not None:
            restore_stdout()
        if restore_stdin is not None:
            restore_stdin()
