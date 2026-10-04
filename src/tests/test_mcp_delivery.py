"""Cursor updates follow the start of a valid response write, not a queued result."""

from __future__ import annotations

import anyio
import pytest

from ai_note_pair.mcp_delivery import _AckingStdout, note_response_started, schedule_delivery
from ai_note_pair.messaging import ChatMessage, UnreadBatch


@pytest.fixture(autouse=True)
def _clear_pending() -> None:
    from ai_note_pair import mcp_delivery

    mcp_delivery._pending.clear()


def _batch() -> UnreadBatch:
    return UnreadBatch(
        room="projectx",
        reader="bob",
        instance_id="instance",
        previous_cursor=0,
        messages=(
            ChatMessage(
                id=1,
                timestamp="2026-01-01T00:00:00Z",
                sender="alice",
                recipient="bob",
                content="hello",
                attachments=(),
            ),
        ),
        delivered_id=1,
    )


def _payload(body: str) -> str:
    return body if body.endswith("\n") else body + "\n"


def test_cursor_updates_when_writing_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[UnreadBatch] = []
    monkeypatch.setattr(
        "ai_note_pair.mcp_delivery.acknowledge_read",
        lambda batch: applied.append(batch),
    )
    schedule_delivery(4, _batch())
    line = _payload(
        '{"jsonrpc":"2.0","id":4,"result":{"structuredContent":'
        '{"room":"projectx","reader":"bob","messages":[{"id":1}]}}}'
    )

    async def scenario() -> None:
        class Raw:
            async def write(self, text: str) -> int:
                assert applied == [_batch()]
                return len(text)

            async def flush(self) -> None:
                return None

        stdout = _AckingStdout(Raw(), note_response_started)
        assert applied == []
        await stdout.write(line)
        assert applied == [_batch()]
        await stdout.flush()

    anyio.run(scenario)
    assert [batch.reader for batch in applied] == ["bob"]


@pytest.mark.parametrize("stage", ["write", "flush"])
def test_output_failure_after_writing_starts_keeps_acknowledgment(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    applied: list[UnreadBatch] = []
    monkeypatch.setattr(
        "ai_note_pair.mcp_delivery.acknowledge_read", lambda batch: applied.append(batch)
    )
    schedule_delivery(4, _batch())
    line = _payload(
        '{"jsonrpc":"2.0","id":4,"result":{"structuredContent":'
        '{"room":"projectx","reader":"bob","messages":[{"id":1}]}}}'
    )

    async def scenario() -> None:
        class Raw:
            async def write(self, text: str) -> int:
                if stage == "write":
                    raise OSError("broken pipe")
                return len(text)

            async def flush(self) -> None:
                raise OSError("broken pipe")

        stdout = _AckingStdout(Raw(), note_response_started)
        with pytest.raises(OSError, match="broken pipe"):
            await stdout.write(line)
            await stdout.flush()

    anyio.run(scenario)
    assert applied == [_batch()]


@pytest.mark.parametrize(
    "body",
    [
        '{"jsonrpc":"2.0","id":4,"error":{"code":-1,"message":"no"}}',
        '{"jsonrpc":"2.0","id":4,"result":{"isError":true,"structuredContent":'
        '{"room":"projectx","reader":"bob","messages":[{"id":1}]}}}',
        '{"jsonrpc":"2.0","id":4,"result":{"structuredContent":'
        '{"room":"projectx","reader":"bob","messages":[{"id":9}]}}}',
        "invalid json",
    ],
)
def test_error_or_invalid_response_does_not_acknowledge(
    monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    applied: list[UnreadBatch] = []
    monkeypatch.setattr(
        "ai_note_pair.mcp_delivery.acknowledge_read", lambda batch: applied.append(batch)
    )
    schedule_delivery(4, _batch())
    note_response_started(_payload(body))
    assert applied == []


def test_cancellation_before_writing_starts_does_not_acknowledge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied: list[UnreadBatch] = []
    monkeypatch.setattr(
        "ai_note_pair.mcp_delivery.acknowledge_read", lambda batch: applied.append(batch)
    )
    schedule_delivery(4, _batch())
    line = _payload(
        '{"jsonrpc":"2.0","id":4,"result":{"structuredContent":'
        '{"room":"projectx","reader":"bob","messages":[{"id":1}]}}}'
    )

    async def scenario() -> None:
        class Raw:
            async def write(self, text: str) -> int:
                raise AssertionError("Cancelled output must not start writing")

        stdout = _AckingStdout(Raw(), note_response_started)
        with anyio.CancelScope() as scope:
            scope.cancel()
            await stdout.write(line)
        assert scope.cancelled_caught

    anyio.run(scenario)
    assert applied == []
