"""Shared fixtures for CLI tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def storage_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home = tmp_path / "ai-note-pair-home"
    home.mkdir()
    monkeypatch.setenv("AI_NOTE_PAIR_HOME", str(home))
    yield home


@pytest.fixture
def cli_env(storage_home: Path) -> dict[str, str]:
    return {"AI_NOTE_PAIR_HOME": str(storage_home)}
