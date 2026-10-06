"""Per-agent conversation synchronization."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from typer.testing import CliRunner

from ai_note_pair.cli import app
from ai_note_pair.messaging import acknowledge_read, collect_unread
from ai_note_pair.storage import DATABASE_FILENAME


def _invoke(runner: CliRunner, args: list[str], cli_env: dict[str, str]):
    return runner.invoke(app, args, env=cli_env)


def _create(runner: CliRunner, cli_env: dict[str, str], room: str = "projectx") -> None:
    result = _invoke(runner, ["create-room", "--name", room], cli_env)
    assert result.exit_code == 0, result.stderr


def _send(
    runner: CliRunner,
    cli_env: dict[str, str],
    *,
    name: str,
    message: str,
    to: str | None = None,
    room: str = "projectx",
) -> None:
    args = ["send", "--room", room, "--name", name, "--message", message]
    if to is not None:
        args.extend(["--to", to])
    result = _invoke(runner, args, cli_env)
    assert result.exit_code == 0, result.stderr


def _read(
    runner: CliRunner,
    cli_env: dict[str, str],
    name: str,
    *,
    room: str = "projectx",
    as_json: bool = False,
):
    args = ["read", "--room", room, "--name", name]
    if as_json:
        args.append("--json")
    return _invoke(runner, args, cli_env)


def _cursors(storage_home: Path, room: str = "projectx") -> dict[str, tuple[int, str | None]]:
    database = storage_home / "rooms" / room / DATABASE_FILENAME
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "SELECT name, last_read_message_id, read_at FROM agents"
        ).fetchall()
    finally:
        connection.close()
    return {name: (cursor, read_at) for name, cursor, read_at in rows}


def test_reads_are_independent_and_include_every_message(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    _send(runner, cli_env, name="alice", message="hello", to="bob")
    _send(runner, cli_env, name="bob", message="hi")
    _send(runner, cli_env, name="alice", message="all hands", to="all")

    assert _cursors(storage_home)["alice"][0] == 3
    assert _cursors(storage_home)["bob"][0] == 2

    bob = _read(runner, cli_env, "bob", as_json=True)
    assert bob.exit_code == 0, bob.stderr
    assert bob.stderr == ""
    payload = json.loads(bob.stdout)
    assert [item["id"] for item in payload["messages"]] == [3]
    assert payload["messages"][0]["sender"] == "alice"
    assert payload["messages"][0]["recipient"] == "all"
    assert payload["messages"][0]["content"] == "all hands"
    assert _cursors(storage_home)["bob"][0] == 3
    assert _cursors(storage_home)["bob"][1] is not None
    assert _cursors(storage_home)["alice"][0] == 3

    empty = _read(runner, cli_env, "bob")
    assert empty.exit_code == 0
    assert empty.stdout.strip() == "No unread messages."
    assert _cursors(storage_home)["bob"][0] == 3

    _send(runner, cli_env, name="bob", message="only for carol", to="carol")
    alice_again = _read(runner, cli_env, "alice", as_json=True)
    again = json.loads(alice_again.stdout)["messages"]
    assert [item["id"] for item in again] == [4]
    assert again[0]["recipient"] == "carol"
    assert _cursors(storage_home)["bob"][0] == 4
    assert _cursors(storage_home)["carol"][0] == 0

    carol = _read(runner, cli_env, "carol", as_json=True)
    carol_messages = json.loads(carol.stdout)["messages"]
    assert [item["content"] for item in carol_messages] == [
        "hello",
        "hi",
        "all hands",
        "only for carol",
    ]
    assert _cursors(storage_home)["bob"][0] == 4


def test_first_read_includes_history_before_registration(
    runner: CliRunner, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    _send(runner, cli_env, name="alice", message="before", to="bob")
    _send(runner, cli_env, name="bob", message="still before", to="alice")
    _send(runner, cli_env, name="alice", message="welcome", to="carol")

    unknown = _read(runner, cli_env, "dave")
    assert unknown.exit_code == 1
    assert "not a participant" in unknown.stderr
    assert unknown.stdout == ""

    carol = _read(runner, cli_env, "carol", as_json=True)
    assert carol.exit_code == 0, carol.stderr
    messages = json.loads(carol.stdout)["messages"]
    assert [item["content"] for item in messages] == ["before", "still before", "welcome"]
    assert [item["id"] for item in messages] == [1, 2, 3]


def test_four_agent_reads_stay_independent(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    _send(runner, cli_env, name="alice", message="a", to="bob")
    _send(runner, cli_env, name="carol", message="c", to="dave")
    alice = _read(runner, cli_env, "alice", as_json=True)
    dave = _read(runner, cli_env, "dave", as_json=True)
    assert [item["id"] for item in json.loads(alice.stdout)["messages"]] == [2]
    assert [item["id"] for item in json.loads(dave.stdout)["messages"]] == [1, 2]
    assert _cursors(storage_home)["bob"][0] == 0
    assert _cursors(storage_home)["carol"][0] == 2

    _send(runner, cli_env, name="bob", message="later", to="dave")
    carol = _read(runner, cli_env, "carol", as_json=True)
    assert [item["id"] for item in json.loads(carol.stdout)["messages"]] == [3]
    alice_delta = _read(runner, cli_env, "alice", as_json=True)
    assert [item["id"] for item in json.loads(alice_delta.stdout)["messages"]] == [3]
    assert _cursors(storage_home)["dave"][0] == 2
    assert _cursors(storage_home)["bob"][0] == 3


def test_cursor_advances_only_after_messages_are_collected(
    storage_home: Path, cli_env: dict[str, str], runner: CliRunner
) -> None:
    _create(runner, cli_env)
    _send(runner, cli_env, name="alice", message="one", to="bob")
    _send(runner, cli_env, name="bob", message="two")
    home = storage_home
    batch = collect_unread("projectx", "alice", home)
    assert [message.id for message in batch.messages] == [2]
    assert _cursors(storage_home)["alice"][0] == 1

    _send(runner, cli_env, name="bob", message="three")
    acknowledge_read(batch, home)
    assert _cursors(storage_home)["alice"][0] == 2

    remaining = collect_unread("projectx", "alice", home)
    assert [message.id for message in remaining.messages] == [3]
    acknowledge_read(remaining, home)
    assert _cursors(storage_home)["alice"][0] == 3

    acknowledge_read(batch, home)
    assert _cursors(storage_home)["alice"][0] == 3


def test_concurrent_same_identity_reads_never_move_backward(
    storage_home: Path, cli_env: dict[str, str], runner: CliRunner
) -> None:
    _create(runner, cli_env)
    for index in range(8):
        _send(runner, cli_env, name="bob", message=f"m{index}", to="alice")

    home = storage_home
    delivered: list[list[int]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def read_once() -> None:
        try:
            batch = collect_unread("projectx", "alice", home)
            acknowledge_read(batch, home)
            with lock:
                delivered.append([message.id for message in batch.messages])
        except BaseException as exc:  # pragma: no cover - reported by assertion
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=read_once) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    cursor = _cursors(storage_home)["alice"][0]
    assert cursor == 8
    assert _cursors(storage_home)["bob"][0] == 8
    seen = {message_id for batch in delivered for message_id in batch}
    final = collect_unread("projectx", "alice", home)
    seen.update(message.id for message in final.messages)
    acknowledge_read(final, home)
    assert seen == set(range(1, 9))
    assert _cursors(storage_home)["alice"][0] == 8


def _check(
    runner: CliRunner,
    cli_env: dict[str, str],
    name: str,
    *,
    room: str = "projectx",
    as_json: bool = False,
):
    args = ["check", "--room", room, "--name", name]
    if as_json:
        args.append("--json")
    return _invoke(runner, args, cli_env)


def test_check_counts_unread_without_moving_the_cursor(
    runner: CliRunner, storage_home: Path, cli_env: dict[str, str]
) -> None:
    _create(runner, cli_env)
    _send(runner, cli_env, name="alice", message="one", to="bob")
    _send(runner, cli_env, name="alice", message="two", to="bob")

    before = _cursors(storage_home)
    checked = _check(runner, cli_env, "bob")
    assert checked.exit_code == 0, checked.stderr
    assert checked.stdout.strip() == "2 unread messages."
    assert _cursors(storage_home) == before

    again = _check(runner, cli_env, "bob", as_json=True)
    payload = json.loads(again.stdout)
    assert payload == {"room": "projectx", "reader": "bob", "unread": 2}
    assert _cursors(storage_home)["bob"][0] == 0

    read = _read(runner, cli_env, "bob", as_json=True)
    assert [item["content"] for item in json.loads(read.stdout)["messages"]] == ["one", "two"]
    assert _cursors(storage_home)["bob"][0] == 2

    empty = _check(runner, cli_env, "bob")
    assert empty.exit_code == 0, empty.stderr
    assert empty.stdout.strip() == "0 unread messages."
    assert _cursors(storage_home)["bob"][0] == 2

    _send(runner, cli_env, name="bob", message="reply")
    alice = _check(runner, cli_env, "alice")
    assert alice.stdout.strip() == "1 unread message."
    assert _cursors(storage_home)["alice"][0] == 2


def test_check_rejects_an_unregistered_agent(runner: CliRunner, cli_env: dict[str, str]) -> None:
    _create(runner, cli_env)
    _send(runner, cli_env, name="alice", message="hello", to="bob")
    missing = _check(runner, cli_env, "dave")
    assert missing.exit_code == 1
    assert "not a participant" in missing.stderr
    assert missing.stdout == ""
