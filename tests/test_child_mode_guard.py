"""Hooks stay silent inside a Lore-spawned child process (LORE_CHILD_MODE=1).

Every detached child that ``lore_cli.spawn`` starts carries the variable,
and each hook entry point returns at once when it is set (issue #23), so
a child never re-enters the hooks of the session that spawned it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class _FakePopen:
    envs: list[dict[str, str]] = []

    def __init__(self, cmd, *, env=None, **kw):
        _FakePopen.envs.append(dict(env or {}))


def test_spawned_children_set_child_mode(tmp_path: Path) -> None:
    """Both spawn paths pass LORE_CHILD_MODE=1 to the child env."""
    _FakePopen.envs = []
    (tmp_path / ".lore" / "run").mkdir(parents=True)

    with patch("subprocess.Popen", _FakePopen):
        from lore_cli.spawn import _spawn_detached, _spawn_in_repo

        _spawn_detached(tmp_path, "test-role", ["echo", "hello"], cooldown_s=0)
        _spawn_in_repo(tmp_path, "log", "flush")

    assert [e.get("LORE_CHILD_MODE") for e in _FakePopen.envs] == ["1", "1"]
    assert _FakePopen.envs[0].get("LORE_ROOT") == str(tmp_path)


def test_in_child_mode_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LORE_CHILD_MODE", "1")
    from lore_cli.hooks import _in_child_mode
    assert _in_child_mode() is True


def test_in_child_mode_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LORE_CHILD_MODE", raising=False)
    from lore_cli.hooks import _in_child_mode
    assert _in_child_mode() is False


def test_session_start_noop_in_child_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LORE_CHILD_MODE", "1")
    mock_session = MagicMock()
    with patch("lore_cli.hooks._session_start", mock_session):
        from lore_cli.hooks import cmd_session_start
        cmd_session_start(cwd="/tmp", plain=True, probe=False)
    mock_session.assert_not_called()


def test_capture_noop_in_child_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LORE_CHILD_MODE", "1")
    mock_resolve = MagicMock()
    with patch("lore_cli.hooks._resolve_cwd_capture", mock_resolve):
        from lore_cli.hooks import capture
        capture(event="session-end")
    mock_resolve.assert_not_called()
