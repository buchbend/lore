"""Curator subprocess hooks are silenced by LORE_CURATOR_MODE=1.

Verifies that _spawn_detached sets the env var and that each hook
entry point returns immediately when the var is set (issue #23).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_spawn_detached_sets_curator_mode(tmp_path: Path) -> None:
    """_spawn_detached passes LORE_CURATOR_MODE=1 to the subprocess env."""
    captured_env: dict[str, str] = {}

    class FakePopen:
        def __init__(self, cmd, *, env=None, **kw):
            captured_env.update(env or {})

    lock_dir = tmp_path / ".lore" / "run"
    lock_dir.mkdir(parents=True)

    with patch("subprocess.Popen", FakePopen):
        from lore_cli.hooks import _spawn_detached

        _spawn_detached(
            tmp_path,
            "test-role",
            ["echo", "hello"],
            cooldown_s=0,
        )

    assert captured_env.get("LORE_CURATOR_MODE") == "1"
    assert captured_env.get("LORE_ROOT") == str(tmp_path)


def test_in_curator_mode_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LORE_CURATOR_MODE", "1")
    from lore_cli.hooks import _in_curator_mode
    assert _in_curator_mode() is True


def test_in_curator_mode_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LORE_CURATOR_MODE", raising=False)
    from lore_cli.hooks import _in_curator_mode
    assert _in_curator_mode() is False


def test_session_start_noop_in_curator_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LORE_CURATOR_MODE", "1")
    mock_session = MagicMock()
    with patch("lore_cli.hooks._session_start", mock_session):
        from lore_cli.hooks import cmd_session_start
        cmd_session_start(cwd="/tmp", plain=True, probe=False)
    mock_session.assert_not_called()


def test_capture_noop_in_curator_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LORE_CURATOR_MODE", "1")
    mock_resolve = MagicMock()
    with patch("lore_cli.hooks._resolve_cwd_capture", mock_resolve):
        from lore_cli.hooks import capture
        capture(event="session-end")
    mock_resolve.assert_not_called()
