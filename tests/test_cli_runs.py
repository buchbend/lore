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
