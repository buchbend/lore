"""`lore on` / `lore off` CLI verbs — write/clear the toggle sentinel.

The skill bodies (`/lore:off`, `/lore:on`) shell out to these verbs
via Bash. Default scope is `all`; `citations` is the narrower scope.
A missing `CLAUDE_SESSION_ID` is a hard error — there's no point
muting nothing.

Both verbs are one typer app apiece over a shared toggle helper, so
these drive them through the root dispatcher — the surface users type.
"""

from __future__ import annotations

import pytest
from lore_cli.__main__ import app
from lore_core import toggles
from typer.testing import CliRunner


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    yield


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# `lore off` ---------------------------------------------------------------


def test_off_default_scope_writes_all_sentinel(runner, monkeypatch):
    sid = "cli-sid-1"
    monkeypatch.setenv("CLAUDE_SESSION_ID", sid)

    result = runner.invoke(app, ["off"])

    assert result.exit_code == 0, result.output
    assert toggles.is_off("all", sid) is True
    assert toggles.is_off("citations", sid) is False


def test_off_citations_scope(runner, monkeypatch):
    sid = "cli-sid-2"
    monkeypatch.setenv("CLAUDE_SESSION_ID", sid)

    result = runner.invoke(app, ["off", "citations"])

    assert result.exit_code == 0
    assert toggles.is_off("citations", sid) is True
    assert toggles.is_off("all", sid) is False


def test_off_all_scope_explicit(runner, monkeypatch):
    sid = "cli-sid-3"
    monkeypatch.setenv("CLAUDE_SESSION_ID", sid)

    result = runner.invoke(app, ["off", "all"])

    assert result.exit_code == 0
    assert toggles.is_off("all", sid) is True


def test_off_invalid_scope_exits_nonzero(runner, monkeypatch):
    monkeypatch.setenv("CLAUDE_SESSION_ID", "cli-sid-x")

    result = runner.invoke(app, ["off", "banana"])

    assert result.exit_code != 0


def test_off_no_session_id_exits_nonzero(runner):
    """No CLAUDE_SESSION_ID → can't scope the sentinel; refuse with a clear error."""
    result = runner.invoke(app, ["off"])

    assert result.exit_code != 0
    assert "session" in result.output.lower() or "session" in (result.stderr or "").lower()


def test_off_idempotent(runner, monkeypatch):
    sid = "cli-sid-4"
    monkeypatch.setenv("CLAUDE_SESSION_ID", sid)

    runner.invoke(app, ["off"])
    result = runner.invoke(app, ["off"])

    assert result.exit_code == 0
    assert toggles.is_off("all", sid) is True


# `lore on` ----------------------------------------------------------------


def test_on_default_scope_clears_all_sentinel(runner, monkeypatch):
    sid = "cli-sid-5"
    monkeypatch.setenv("CLAUDE_SESSION_ID", sid)
    toggles.set_off("all", sid)

    result = runner.invoke(app, ["on"])

    assert result.exit_code == 0, result.output
    assert toggles.is_off("all", sid) is False


def test_on_citations_scope(runner, monkeypatch):
    sid = "cli-sid-6"
    monkeypatch.setenv("CLAUDE_SESSION_ID", sid)
    toggles.set_off("citations", sid)
    toggles.set_off("all", sid)

    result = runner.invoke(app, ["on", "citations"])

    assert result.exit_code == 0
    assert toggles.is_off("citations", sid) is False
    # `all` sentinel is unrelated; on citations doesn't touch it.
    assert toggles.is_off("all", sid) is True


def test_on_when_already_on_is_noop(runner, monkeypatch):
    sid = "cli-sid-7"
    monkeypatch.setenv("CLAUDE_SESSION_ID", sid)

    result = runner.invoke(app, ["on"])

    assert result.exit_code == 0
    assert toggles.is_off("all", sid) is False


def test_on_invalid_scope_exits_nonzero(runner, monkeypatch):
    monkeypatch.setenv("CLAUDE_SESSION_ID", "cli-sid-y")

    result = runner.invoke(app, ["on", "banana"])

    assert result.exit_code != 0


def test_on_no_session_id_exits_nonzero(runner):
    result = runner.invoke(app, ["on"])

    assert result.exit_code != 0


def test_on_and_off_share_one_module(runner):
    """One toggle, one file, two verbs — not two near-identical modules."""
    from lore_cli import toggle_cmd

    assert toggle_cmd.on_app is not toggle_cmd.off_app
