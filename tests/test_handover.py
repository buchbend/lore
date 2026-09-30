"""Handover notes survive `/clear` and compaction through SessionStart.

The agent writes one note per working directory with `lore handover write`.
SessionStart reads the hook payload's ``source``: after ``clear`` or
``compact`` it injects the full note, on ``startup`` or ``resume`` it offers
the note in one line. A clear archives the note after injecting it, so the
next clear does not replay a stale handover.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lore_cli.handover_cmd import app as handover_app
from lore_cli.hooks import hook_app
from lore_core import handover
from typer.testing import CliRunner

NOTE = "## Goal\nShip the handover hook.\n\n## Next\nWire PreCompact.\n"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    (tmp_path / "vault" / "wiki").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "vault"))
    cwd = tmp_path / "proj"
    cwd.mkdir()
    return cwd


def _hook(*args: str, source: str | None = None) -> str:
    payload = json.dumps({"source": source}) if source else ""
    result = CliRunner().invoke(hook_app, list(args), input=payload)
    assert result.exit_code == 0, result.output
    return result.output


def _session_start(cwd: Path, source: str, *, probe: bool = False) -> str:
    args = ["session-start", "--cwd", str(cwd), "--plain"]
    if probe:
        args.append("--probe")
    return _hook(*args, source=source)


# --- store -------------------------------------------------------------------


def test_write_stores_one_note_per_cwd(env: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    handover.write(env, NOTE)
    assert handover.read(env) == NOTE
    assert handover.read(other) is None


def test_write_replaces_the_previous_note(env: Path) -> None:
    handover.write(env, "old")
    handover.write(env, NOTE)
    assert handover.read(env) == NOTE


def test_write_rejects_a_note_over_the_budget(env: Path) -> None:
    with pytest.raises(handover.HandoverTooLong):
        handover.write(env, "x" * (handover.BUDGET_CHARS + 1))
    assert handover.read(env) is None


def test_write_rejects_an_empty_note(env: Path) -> None:
    with pytest.raises(ValueError):
        handover.write(env, "  \n")


# --- SessionStart ------------------------------------------------------------


def test_clear_injects_the_full_note_and_archives_it(env: Path) -> None:
    handover.write(env, NOTE)
    out = _session_start(env, "clear")
    assert "Ship the handover hook." in out
    assert "Wire PreCompact." in out
    assert handover.read(env) is None
    assert "Ship the handover hook." not in _session_start(env, "clear")


def test_compact_injects_the_full_note_and_keeps_it(env: Path) -> None:
    handover.write(env, NOTE)
    out = _session_start(env, "compact")
    assert "Ship the handover hook." in out
    assert handover.read(env) == NOTE


def test_startup_offers_the_note_in_one_line(env: Path) -> None:
    handover.write(env, NOTE)
    out = _session_start(env, "startup")
    assert "Ship the handover hook." not in out
    lines = [ln for ln in out.splitlines() if "lore handover show" in ln]
    assert len(lines) == 1
    assert handover.read(env) == NOTE


def test_probe_never_archives(env: Path) -> None:
    handover.write(env, NOTE)
    _session_start(env, "clear", probe=True)
    assert handover.read(env) == NOTE


def test_no_note_no_mention(env: Path) -> None:
    for source in ("startup", "clear", "compact"):
        assert "handover" not in _session_start(env, source).lower()


# --- PreCompact --------------------------------------------------------------


def test_pre_compact_reminds_to_update_an_existing_note(env: Path) -> None:
    assert "handover" not in _hook("pre-compact", "--cwd", str(env), "--plain").lower()
    handover.write(env, NOTE)
    out = _hook("pre-compact", "--cwd", str(env), "--plain")
    assert "lore handover write" in out


# --- CLI ---------------------------------------------------------------------


def test_cli_write_reads_stdin_and_show_prints_it(env: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(handover_app, ["write", "--cwd", str(env)], input=NOTE)
    assert result.exit_code == 0, result.output
    shown = runner.invoke(handover_app, ["show", "--cwd", str(env)])
    assert shown.exit_code == 0
    assert "Ship the handover hook." in shown.output


def test_cli_write_over_budget_fails_and_names_the_limit(env: Path) -> None:
    result = CliRunner().invoke(
        handover_app,
        ["write", "--cwd", str(env)],
        input="x" * (handover.BUDGET_CHARS + 1),
    )
    assert result.exit_code != 0
    assert str(handover.BUDGET_CHARS) in result.output


def test_cli_show_without_note_fails(env: Path) -> None:
    result = CliRunner().invoke(handover_app, ["show", "--cwd", str(env)])
    assert result.exit_code != 0
