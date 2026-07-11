"""`lore runs` reads curator runs reconstructed from the event spine (#189).

There are no per-run files any more; a run is a group of ``source="curator"``
spine envelopes sharing a ``run_id``. Seeding writes envelopes onto the spine.
"""

import json
import re
from pathlib import Path

from lore_core.spine import SpineWriter
from typer.testing import CliRunner

_RUN_ID = "2026-04-20T14-32-05-a1b2c3"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(s: str) -> str:
    """Strip ANSI so number/path highlighting doesn't split substrings."""
    return _ANSI.sub("", s)


def _seed_run(tmp_path: Path, run_id: str = _RUN_ID) -> str:
    w = SpineWriter(tmp_path)
    w.emit(source="curator", event="run-start", run_id=run_id, data={"trigger": "hook"})
    w.emit(
        source="curator",
        event="transcript-start",
        run_id=run_id,
        data={"transcript_id": "t1", "new_turns": 10, "hash_before": "abc"},
    )
    w.emit(
        source="curator",
        event="noteworthy",
        run_id=run_id,
        data={
            "transcript_id": "t1",
            "verdict": True,
            "reason": "worthy",
            "tier": "middle",
            "latency_ms": 500,
        },
    )
    w.emit(
        source="curator",
        event="session-note",
        run_id=run_id,
        data={
            "transcript_id": "t1",
            "action": "filed",
            "path": "p.md",
            "wikilink": "[[2026-04-20-test]]",
        },
    )
    w.emit(
        source="curator",
        event="run-end",
        run_id=run_id,
        data={"duration_ms": 4000, "notes_new": 1, "notes_merged": 0, "skipped": 0, "errors": 0},
    )
    return run_id


def _seed_hook_events(tmp_path: Path, rows: list[dict]) -> None:
    w = SpineWriter(tmp_path)
    for row in rows:
        data = {k: v for k, v in row.items() if k not in ("ts", "event", "schema_version")}
        w.emit(source="hook", event=row.get("event"), data=data)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_runs_show_latest(tmp_path, monkeypatch):
    from lore_cli import runs_cmd

    _seed_run(tmp_path)
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["show", "latest"])
    assert result.exit_code == 0, result.output
    out = _plain(result.stdout)
    assert "2026-04-20-test" in out
    assert "1 new" in out
    assert "worthy" in out


def test_runs_show_json_mode(tmp_path, monkeypatch):
    from lore_cli import runs_cmd

    _seed_run(tmp_path)
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["show", "a1b2c3", "--json"])
    assert result.exit_code == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    for ln in lines:
        json.loads(ln)  # must be valid JSON


def test_runs_show_verbose_notes_trace_not_persisted(tmp_path, monkeypatch):
    from lore_cli import runs_cmd

    _seed_run(tmp_path)
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["show", "latest", "--verbose"])
    assert result.exit_code == 0
    assert "LORE_TRACE_LLM" in result.stdout


def test_runs_show_not_found(tmp_path, monkeypatch):
    from lore_cli import runs_cmd

    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["show", "zzzzzz"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower() or "no runs" in result.output.lower()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_runs_list_empty(tmp_path, monkeypatch):
    from lore_cli import runs_cmd

    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["list"])
    assert result.exit_code == 0
    assert "no capture activity" in result.stdout.lower()


def test_runs_list_shows_seeded_run(tmp_path, monkeypatch):
    from lore_cli import runs_cmd

    _seed_run(tmp_path)
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["list"])
    assert result.exit_code == 0
    assert "a1b2c3" in result.stdout  # short suffix
    assert "1 new" in result.stdout  # notes cell


def test_runs_list_json(tmp_path, monkeypatch):
    from lore_cli import runs_cmd

    _seed_run(tmp_path)
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["list", "--json"])
    assert result.exit_code == 0
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            json.loads(line)


# ---------------------------------------------------------------------------
# list --hooks
# ---------------------------------------------------------------------------


def test_runs_list_hooks_interleaved_run_and_hook(tmp_path, monkeypatch):
    _seed_run(tmp_path)
    _seed_hook_events(
        tmp_path,
        [
            {
                "event": "session-end",
                "outcome": "spawned-curator",
                "pid": 12345,
                "cwd": "/home/user/myproject",
            },
        ],
    )
    from lore_cli import runs_cmd

    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    runner = CliRunner()
    result = runner.invoke(runs_cmd.app, ["list", "--hooks"])
    assert result.exit_code == 0, result.output
    assert "a1b2c3" in result.stdout
    assert "─" in result.stdout  # ─ hook marker
    assert "session-end" in result.stdout
    assert "myproject" in result.stdout
    assert "12345" in result.stdout


def test_runs_list_hooks_only_hook_events_no_runs(tmp_path, monkeypatch):
    _seed_hook_events(
        tmp_path,
        [
            {"event": "session-start", "outcome": "below-threshold"},
        ],
    )
    from lore_cli import runs_cmd

    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    runner = CliRunner()
    result = runner.invoke(runs_cmd.app, ["list", "--hooks"])
    assert result.exit_code == 0, result.output
    assert "session-start" in result.stdout
    assert "─" in result.stdout


def test_runs_list_hooks_only_runs_no_hook_events(tmp_path, monkeypatch):
    """--hooks with runs but no hook events renders the runs AND a diagnostic
    banner (the exact pattern when Claude Code's capture hook never fires)."""
    _seed_run(tmp_path)
    from lore_cli import runs_cmd

    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    runner = CliRunner()
    result = runner.invoke(runs_cmd.app, ["list", "--hooks"])
    assert result.exit_code == 0, result.output
    assert "a1b2c3" in result.stdout
    assert "1 new" in result.stdout
    assert "spine" in result.output.lower()
    assert "may not be firing" in result.output.lower()


def test_runs_list_hooks_no_runs_no_events_no_warning(tmp_path, monkeypatch):
    from lore_cli import runs_cmd

    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    runner = CliRunner()
    result = runner.invoke(runs_cmd.app, ["list", "--hooks"])
    assert result.exit_code == 0, result.output
    assert "no capture activity" in result.output.lower()
    assert "may not be firing" not in result.output.lower()


# ---------------------------------------------------------------------------
# tail (follows the spine's curator events)
# ---------------------------------------------------------------------------


def test_runs_tail_once_reads_to_run_end(tmp_path, monkeypatch):
    from lore_cli import runs_cmd

    _seed_run(tmp_path)
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    monkeypatch.setattr(runs_cmd, "_POLL_INTERVAL_S", 0.01)
    result = runner.invoke(runs_cmd.app, ["tail", "--once"])
    assert result.exit_code == 0
    assert "start-run" in result.stdout or "trigger" in result.stdout
    assert "end" in result.stdout


def test_runs_tail_missing_spine(tmp_path, monkeypatch):
    from lore_cli import runs_cmd

    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["tail", "--once"])
    assert result.exit_code == 0
    assert "no active" in result.stdout.lower() or "no run" in result.stdout.lower()


def test_runs_tail_ignores_non_curator_events(tmp_path, monkeypatch):
    """A spine with only hook events yields no run output but still terminates."""
    from lore_cli import runs_cmd

    _seed_hook_events(tmp_path, [{"event": "session-start", "outcome": "ok"}])
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    monkeypatch.setattr(runs_cmd, "_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(runs_cmd, "_IDLE_TIMEOUT_S", 0)  # exit immediately when idle
    result = runner.invoke(runs_cmd.app, ["tail", "--once"])
    assert result.exit_code == 0
    assert "session-start" not in result.stdout  # hook events are not run records


# ---------------------------------------------------------------------------
# Shell completion helper
# ---------------------------------------------------------------------------


def test_complete_run_id_returns_candidates(tmp_path, monkeypatch):
    from lore_cli import runs_cmd
    from lore_core import config as cfg_mod

    _seed_run(tmp_path)
    monkeypatch.setattr(cfg_mod, "get_lore_root", lambda: tmp_path)

    results = runs_cmd._complete_run_id(None, None, "")
    assert "a1b2c3" in results
    assert "latest" in results
    assert "^1" in results
    assert "^5" in results
    assert "^6" not in results


def test_complete_run_id_filters_by_prefix(tmp_path, monkeypatch):
    from lore_cli import runs_cmd
    from lore_core import config as cfg_mod

    _seed_run(tmp_path)
    monkeypatch.setattr(cfg_mod, "get_lore_root", lambda: tmp_path)

    results = runs_cmd._complete_run_id(None, None, "^")
    assert all(r.startswith("^") for r in results)
    assert "latest" not in results
    assert "a1b2c3" not in results


def test_complete_run_id_graceful_on_missing_root(tmp_path, monkeypatch):
    from lore_cli import runs_cmd
    from lore_core import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "get_lore_root", lambda: tmp_path / "nonexistent")

    results = runs_cmd._complete_run_id(None, None, "")
    assert "latest" in results
    assert "^1" in results


# ---------------------------------------------------------------------------
# Deprecation — thin alias pointing at `lore trace` (#195)
# ---------------------------------------------------------------------------


def test_runs_deprecation_pointer_and_delegation(tmp_path, monkeypatch):
    """`lore runs` prints a pointer to `lore trace` on stderr, then still
    runs its own subcommand — the deprecation window keeps it functional."""
    from lore_cli import runs_cmd

    _seed_run(tmp_path)
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    runner = CliRunner()
    result = runner.invoke(runs_cmd.app, ["list"])
    assert "deprecated" in result.stderr
    assert "lore trace" in result.stderr
    assert "a1b2c3" in result.stdout  # delegation: old behavior intact
