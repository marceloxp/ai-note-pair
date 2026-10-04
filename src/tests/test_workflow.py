"""End-to-end CLI workflow for the MVP."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ai_note_pair.cli import app


def _invoke(
    runner: CliRunner, args: list[str], cli_env: dict[str, str], input_text: str | None = None
):
    return runner.invoke(app, args, env=cli_env, input=input_text)


def test_full_cli_workflow(
    runner: CliRunner, tmp_path: Path, storage_home: Path, cli_env: dict[str, str]
) -> None:
    created = _invoke(runner, ["create-room", "--name", "projectx"], cli_env)
    assert created.exit_code == 0

    missing_reader = _invoke(runner, ["info", "--room", "projectx", "--json"], cli_env)
    assert missing_reader.exit_code == 0
    assert json.loads(missing_reader.stdout)["participants"] == 0

    first = _invoke(
        runner,
        [
            "send",
            "--room",
            "projectx",
            "--name",
            "alice",
            "--to",
            "bob",
            "--message",
            "hello bob",
        ],
        cli_env,
    )
    assert first.exit_code == 0

    discovered = json.loads(
        _invoke(runner, ["info", "--room", "projectx", "--json"], cli_env).stdout
    )
    assert [agent["name"] for agent in discovered["agents"]] == ["alice", "bob"]
    assert discovered["agents"][1]["messages_sent"] == 0

    reply = _invoke(
        runner,
        ["send", "--room", "projectx", "--name", "bob", "--message", "hello alice"],
        cli_env,
    )
    assert reply.exit_code == 0
    assert "to 'alice'" in reply.stdout

    note = tmp_path / "note.txt"
    note.write_text("shared notes", encoding="utf-8")
    attached = _invoke(
        runner,
        [
            "send",
            "--room",
            "projectx",
            "--name",
            "alice",
            "--to",
            "all",
            "--message-file",
            "-",
            "--attachment",
            str(note),
        ],
        cli_env,
        input_text="broadcast with file",
    )
    assert attached.exit_code == 0, attached.stderr

    carol = _invoke(
        runner,
        ["send", "--room", "projectx", "--name", "carol", "--to", "dave", "--message", "join"],
        cli_env,
    )
    assert carol.exit_code == 0
    needs_recipient = _invoke(
        runner,
        ["send", "--room", "projectx", "--name", "dave", "--message", "which one?"],
        cli_env,
    )
    assert needs_recipient.exit_code == 1
    assert needs_recipient.stdout == ""

    alice_read = _invoke(
        runner, ["read", "--room", "projectx", "--name", "alice", "--json"], cli_env
    )
    dave_read = _invoke(runner, ["read", "--room", "projectx", "--name", "dave", "--json"], cli_env)
    assert alice_read.exit_code == 0 and dave_read.exit_code == 0
    alice_messages = json.loads(alice_read.stdout)["messages"]
    dave_messages = json.loads(dave_read.stdout)["messages"]
    assert [item["id"] for item in alice_messages] == [4]
    assert [item["id"] for item in dave_messages] == [1, 2, 3, 4]
    assert dave_messages[2]["attachments"] == ["attachments/note.txt"]
    assert (storage_home / "rooms" / "projectx" / "attachments" / "note.txt").read_text(
        encoding="utf-8"
    ) == "shared notes"

    later = _invoke(
        runner,
        ["send", "--room", "projectx", "--name", "bob", "--to", "carol", "--message", "later"],
        cli_env,
    )
    assert later.exit_code == 0
    alice_delta = json.loads(
        _invoke(runner, ["read", "--room", "projectx", "--name", "alice", "--json"], cli_env).stdout
    )
    dave_again = json.loads(
        _invoke(runner, ["read", "--room", "projectx", "--name", "dave", "--json"], cli_env).stdout
    )
    assert [item["id"] for item in alice_delta["messages"]] == [5]
    assert [item["id"] for item in dave_again["messages"]] == [5]

    bad_json = _invoke(runner, ["read", "--room", "missing", "--name", "alice", "--json"], cli_env)
    assert bad_json.exit_code == 1
    assert bad_json.stdout == ""
    assert bad_json.stderr != ""

    archived = _invoke(runner, ["archive", "--room", "projectx"], cli_env)
    assert archived.exit_code == 0
    assert _invoke(runner, ["list-rooms"], cli_env).stdout == ""
    archived_names = _invoke(runner, ["list-rooms", "--archived"], cli_env).stdout.splitlines()
    assert len(archived_names) == 1

    assert _invoke(runner, ["create-room", "--name", "projectx"], cli_env).exit_code == 0
    reused = json.loads(_invoke(runner, ["info", "--room", "projectx", "--json"], cli_env).stdout)
    assert reused["messages"] == 0
    assert reused["participants"] == 0
