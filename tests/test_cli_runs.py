import json
from pathlib import Path

from typer.testing import CliRunner


def _seed_run(tmp_path: Path):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    path = runs / "2026-04-20T14-32-05-a1b2c3.jsonl"
    path.write_text(
        json.dumps({"type": "run-start", "schema_version": 1, "ts": "2026-04-20T14:32:05Z",
                    "run_id": "2026-04-20T14-32-05-a1b2c3", "trigger": "hook"}) + "\n" +
        json.dumps({"type": "transcript-start", "schema_version": 1, "ts": "2026-04-20T14:32:06Z",
                    "transcript_id": "t1", "new_turns": 10, "hash_before": "abc"}) + "\n" +
        json.dumps({"type": "noteworthy", "schema_version": 1, "ts": "2026-04-20T14:32:07Z",
                    "transcript_id": "t1", "verdict": True, "reason": "worthy",
                    "tier": "middle", "latency_ms": 500}) + "\n" +
        json.dumps({"type": "session-note", "schema_version": 1, "ts": "2026-04-20T14:32:08Z",
                    "transcript_id": "t1", "action": "filed",
                    "path": "p.md", "wikilink": "[[2026-04-20-test]]"}) + "\n" +
        json.dumps({"type": "run-end", "schema_version": 1, "ts": "2026-04-20T14:32:09Z",
                    "duration_ms": 4000, "notes_new": 1, "notes_merged": 0,
                    "skipped": 0, "errors": 0}) + "\n"
    )
    return path


def test_runs_show_latest(tmp_path, monkeypatch):
    from lore_cli import runs_cmd
    _seed_run(tmp_path)
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["show", "latest"])
    assert result.exit_code == 0, result.output
    assert "2026-04-20-test" in result.stdout
    assert "1 new" in result.stdout
    assert "worthy" in result.stdout


def test_runs_show_json_mode(tmp_path, monkeypatch):
    from lore_cli import runs_cmd
    _seed_run(tmp_path)
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["show", "a1b2c3", "--json"])
    assert result.exit_code == 0
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    for l in lines:
        json.loads(l)  # must be valid JSON


def test_runs_show_verbose_without_companion_prints_message(tmp_path, monkeypatch):
    from lore_cli import runs_cmd
    _seed_run(tmp_path)
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["show", "latest", "--verbose"])
    assert result.exit_code == 0
    assert "LORE_TRACE_LLM" in result.stdout


def test_runs_show_not_found(tmp_path, monkeypatch):
    from lore_cli import runs_cmd
    (tmp_path / ".lore" / "runs").mkdir(parents=True)
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["show", "zzzzzz"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower() or "no runs" in result.output.lower()


def test_runs_show_schema_too_new_refuses(tmp_path, monkeypatch):
    from lore_cli import runs_cmd
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    (runs / "2026-04-20T10-00-00-aaaaaa.jsonl").write_text(
        '{"type":"run-start","schema_version":2}\n'
        '{"type":"run-end","schema_version":2}\n'
    )
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["show", "latest"])
    assert result.exit_code == 1
    assert "schema" in result.output.lower() or "upgrade" in result.output.lower()


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
    assert "a1b2c3" in result.stdout          # short suffix
    assert "1 new" in result.stdout          # notes cell


def test_runs_list_json(tmp_path, monkeypatch):
    from lore_cli import runs_cmd
    _seed_run(tmp_path)
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["list", "--json"])
    assert result.exit_code == 0
    # Each non-empty line is valid JSON.
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            json.loads(line)


def test_runs_list_schema_mismatch_dimmed(tmp_path, monkeypatch):
    from lore_cli import runs_cmd
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    (runs / "2026-04-20T10-00-00-xxxxxx.jsonl").write_text(
        '{"type":"run-start","schema_version":2}\n'
        '{"type":"run-end","schema_version":2,"notes_new":0,"notes_merged":0,"skipped":0,"errors":0,"duration_ms":0}\n'
    )
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["list"])
    assert result.exit_code == 0, result.output
    # List should render the row, with a schema mismatch note.
    assert "xxxxxx" in result.stdout
    assert ("schema" in result.stdout.lower() or "v2" in result.stdout.lower()
            or "upgrade" in result.stdout.lower())


def test_runs_tail_once_reads_to_run_end(tmp_path, monkeypatch):
    from lore_cli import runs_cmd
    live = tmp_path / ".lore" / "runs-live.jsonl"
    live.parent.mkdir(parents=True)
    live.write_text(
        json.dumps({"type": "run-start", "ts": "2026-04-20T14:32:05Z",
                    "run_id": "r1", "trigger": "hook"}) + "\n" +
        json.dumps({"type": "run-end", "ts": "2026-04-20T14:32:09Z",
                    "duration_ms": 4000, "notes_new": 1, "notes_merged": 0,
                    "skipped": 0, "errors": 0}) + "\n"
    )
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    monkeypatch.setattr(runs_cmd, "_POLL_INTERVAL_S", 0.01)
    result = runner.invoke(runs_cmd.app, ["tail", "--once"])
    assert result.exit_code == 0
    # The flat log contains both the start-run and the end record.
    assert "start-run" in result.stdout or "trigger" in result.stdout
    assert "end" in result.stdout


def test_runs_tail_missing_live_file(tmp_path, monkeypatch):
    from lore_cli import runs_cmd
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["tail", "--once"])
    assert result.exit_code == 0
    assert "no active" in result.stdout.lower() or "no run" in result.stdout.lower()


def test_runs_tail_handles_truncation_across_runs(tmp_path, monkeypatch):
    """runs-live.jsonl is truncated on each new run-start; tail should see
    the new run's records, not silently skip them."""
    from lore_cli import runs_cmd

    live = tmp_path / ".lore" / "runs-live.jsonl"
    live.parent.mkdir(parents=True)

    # Run A — a complete, short run.
    live.write_text(
        json.dumps({"type": "run-start", "ts": "2026-04-20T10:00:00Z",
                    "run_id": "runA", "trigger": "hook"}) + "\n" +
        json.dumps({"type": "run-end", "ts": "2026-04-20T10:00:01Z",
                    "duration_ms": 1000, "notes_new": 0, "notes_merged": 0,
                    "skipped": 0, "errors": 0}) + "\n"
    )

    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    monkeypatch.setattr(runs_cmd, "_POLL_INTERVAL_S", 0.01)

    # --once tail of run A.
    result_a = runner.invoke(runs_cmd.app, ["tail", "--once"])
    assert result_a.exit_code == 0
    assert "end" in result_a.stdout

    # Simulate run B: truncate and write new records.
    live.write_text(
        json.dumps({"type": "run-start", "ts": "2026-04-20T11:00:00Z",
                    "run_id": "runB", "trigger": "manual"}) + "\n" +
        json.dumps({"type": "run-end", "ts": "2026-04-20T11:00:02Z",
                    "duration_ms": 2000, "notes_new": 1, "notes_merged": 0,
                    "skipped": 0, "errors": 0}) + "\n"
    )

    # A fresh tail invocation (new process-equivalent) sees run B.
    result_b = runner.invoke(runs_cmd.app, ["tail", "--once"])
    assert result_b.exit_code == 0
    # Run B contains 1 new note, not 0.
    assert "1 new" in result_b.stdout or "manual" in result_b.stdout


# ---------------------------------------------------------------------------
# Shell completion helper
# ---------------------------------------------------------------------------


def test_complete_run_id_returns_candidates(tmp_path, monkeypatch):
    """_complete_run_id returns suffix matches and static aliases."""
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
    """_complete_run_id filters to matching prefix."""
    from lore_cli import runs_cmd
    from lore_core import config as cfg_mod

    _seed_run(tmp_path)
    monkeypatch.setattr(cfg_mod, "get_lore_root", lambda: tmp_path)

    results = runs_cmd._complete_run_id(None, None, "^")
    assert all(r.startswith("^") for r in results)
    assert "latest" not in results
    assert "a1b2c3" not in results


def test_complete_run_id_graceful_on_missing_root(tmp_path, monkeypatch):
    """_complete_run_id returns only static aliases when runs dir is absent."""
    from lore_cli import runs_cmd
    from lore_core import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "get_lore_root", lambda: tmp_path / "nonexistent")

    results = runs_cmd._complete_run_id(None, None, "")
    assert "latest" in results
    assert "^1" in results
