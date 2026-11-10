"""Handover notes survive `/clear` and compaction through SessionStart.

The agent writes one note per working directory with `lore handover write`.
SessionStart reads the hook payload's ``source``: after ``clear`` or
``compact`` it injects a pointer to the note file, on ``startup`` or ``resume``
it offers the note in one line. A clear archives the note, so the next clear
does not replay a stale handover. The pointer then names the archived file.
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


def test_write_accepts_a_note_of_50_000_characters(env: Path) -> None:
    note = "x" * 50_000
    handover.write(env, note)
    assert handover.read(env) == note


def test_write_rejects_an_empty_note(env: Path) -> None:
    with pytest.raises(ValueError):
        handover.write(env, "  \n")


# --- SessionStart ------------------------------------------------------------


def _pointed_file(out: str) -> Path:
    line = next(ln for ln in out.splitlines() if "Read the whole file" in ln)
    return Path(line.split("`")[1])


def test_clear_points_to_the_archived_note_and_injects_no_line_of_it(env: Path) -> None:
    handover.write(env, NOTE)
    out = _session_start(env, "clear")
    assert "## Handover (written " in out
    assert "Read the whole file before any other action." in out
    assert not any(ln and ln in out for ln in NOTE.splitlines())
    assert _pointed_file(out).read_text(encoding="utf-8") == NOTE
    assert handover.read(env) is None
    assert "handover" not in _session_start(env, "clear").lower()


def test_compact_points_to_the_note_and_keeps_it(env: Path) -> None:
    handover.write(env, NOTE)
    out = _session_start(env, "compact")
    assert "Read the whole file before any other action." in out
    assert "Ship the handover hook." not in out
    assert _pointed_file(out) == handover.handover_path(env)
    assert handover.read(env) == NOTE


def test_startup_offers_the_note_in_one_line(env: Path) -> None:
    handover.write(env, NOTE)
    out = _session_start(env, "startup")
    assert "Ship the handover hook." not in out
    lines = [ln for ln in out.splitlines() if "lore handover show" in ln]
    assert len(lines) == 1
    assert handover.read(env) == NOTE


def test_pointer_follows_the_status_line_in_a_text_over_the_hook_limit(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lore_cli import hooks

    monkeypatch.setattr(hooks, "_session_start", lambda cwd: "lore: active\n" + "x" * 12_000)
    handover.write(env, NOTE)
    out = _session_start(env, "clear")
    assert out.splitlines()[0] == "lore: active"
    assert out.index("Read the whole file before any other action.") < 2_000


def test_probe_never_archives(env: Path) -> None:
    handover.write(env, NOTE)
    _session_start(env, "clear", probe=True)
    assert handover.read(env) == NOTE


def test_no_note_no_mention(env: Path) -> None:
    for source in ("startup", "clear", "compact"):
        assert "handover" not in _session_start(env, source).lower()


def _banner(cwd: Path, source: str) -> str:
    out = _hook("session-start", "--cwd", str(cwd), "--probe", source=source)
    return json.loads(out)["systemMessage"]


def test_banner_shows_the_note_was_found_and_its_goal(env: Path) -> None:
    handover.write(env, NOTE)
    assert "handover pointed to" in _banner(env, "clear")
    assert "handover pointed to" in _banner(env, "compact")
    assert "Ship the handover hook." in _banner(env, "clear")
    assert "handover available" in _banner(env, "startup")


def test_banner_says_when_no_note_was_found_after_clear(env: Path) -> None:
    assert "no handover note" in _banner(env, "clear")
    assert "handover" not in _banner(env, "startup")


def test_gist_falls_back_to_the_first_prose_line_and_clips() -> None:
    assert handover._gist("# Title\nplain line\n") == "plain line"
    assert len(handover._gist("## Goal\n" + "x" * 200)) == 80


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


def test_cli_show_without_note_fails(env: Path) -> None:
    result = CliRunner().invoke(handover_app, ["show", "--cwd", str(env)])
    assert result.exit_code != 0


def test_cli_show_prints_the_newest_archived_note_after_clear(env: Path) -> None:
    handover.write(env, "old note")
    old = handover.archive(env)
    old.rename(old.with_name(old.name.replace(".2", ".1", 1)))  # an earlier stamp
    handover.write(env, NOTE)
    archived = handover.archive(env)
    shown = CliRunner().invoke(handover_app, ["show", "--cwd", str(env)])
    assert shown.exit_code == 0
    assert NOTE in shown.output
    assert "old note" not in shown.output
    assert str(archived) in shown.output


def test_cli_show_prefers_the_live_note(env: Path) -> None:
    handover.write(env, "old note")
    handover.archive(env)
    handover.write(env, NOTE)
    shown = CliRunner().invoke(handover_app, ["show", "--cwd", str(env)])
    assert shown.output == NOTE
