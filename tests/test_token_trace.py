"""`lore trace tokens` — token totals per skill or agent phase of one session."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from lore_cli.trace_cmd import app
from lore_core.token_trace import trace_tokens
from typer.testing import CliRunner

FIXTURE = Path(__file__).parent / "fixtures" / "token_trace_session.jsonl"
runner = CliRunner()


def _rows(path: Path = FIXTURE) -> dict[str, dict]:
    return {p.name: p.as_dict() for p in trace_tokens(path)}


def test_fixture_yields_main_plus_three_phases_in_order() -> None:
    names = [p.name for p in trace_tokens(FIXTURE)]
    assert names == ["main", "skill:tdd", "skill:review", "agent:explore repo"]


def test_repeated_message_id_counts_once() -> None:
    main = _rows()["main"]
    # m1 (two lines, one usage) + m2, the message that calls the first Skill.
    assert main["messages"] == 2
    assert main["input"] == 11
    assert main["output"] == 7
    assert main["cache_read"] == 103
    assert main["cache_write"] == 24
    assert main["total"] == 11 + 7 + 103 + 24


def test_phase_sums() -> None:
    rows = _rows()
    assert rows["skill:tdd"]["messages"] == 2  # m3, plus m4 that calls the next Skill
    assert rows["skill:tdd"]["input"] == 22
    assert rows["skill:review"]["messages"] == 2
    assert rows["skill:review"]["cache_read"] == 303


def test_sidechain_lines_go_to_the_latest_agent_phase() -> None:
    agent = _rows()["agent:explore repo"]
    # m7 (sidechain) and m8 (main thread after the agent call).
    assert agent["messages"] == 2
    assert agent["input"] == 45
    assert agent["cache_read"] == 405


def test_cli_json_by_path() -> None:
    result = runner.invoke(app, ["tokens", str(FIXTURE), "--json"], catch_exceptions=False)
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [p["name"] for p in data["phases"]][0] == "main"
    assert data["total"] == sum(p["total"] for p in data["phases"])


def test_cli_table_by_path() -> None:
    result = runner.invoke(app, ["tokens", str(FIXTURE)], catch_exceptions=False)
    assert result.exit_code == 0
    for word in ("phase", "cache read", "skill:tdd", "agent:explore repo"):
        assert word in result.output


def test_cli_resolves_session_id_through_the_ledger(tmp_path, monkeypatch) -> None:
    root = tmp_path / "vault"
    (root / ".lore").mkdir(parents=True)
    copy = tmp_path / "elsewhere" / "abc-123.jsonl"
    copy.parent.mkdir()
    shutil.copy(FIXTURE, copy)
    ledger = {
        "claude-code::abc-123": {
            "integration": "claude-code",
            "transcript_id": "abc-123",
            "path": str(copy),
            "directory": str(tmp_path),
            "last_mtime": "2026-09-01T00:00:00+00:00",
        }
    }
    (root / ".lore" / "transcript-ledger.json").write_text(json.dumps(ledger))
    monkeypatch.setenv("LORE_ROOT", str(root))
    result = runner.invoke(app, ["tokens", "abc-123", "--json"], catch_exceptions=False)
    assert result.exit_code == 0
    assert json.loads(result.output)["phases"][0]["name"] == "main"


def test_cli_missing_session_exits_nonzero_and_names_it(tmp_path, monkeypatch) -> None:
    root = tmp_path / "vault"
    (root / ".lore").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    result = runner.invoke(app, ["tokens", "no-such-session"], catch_exceptions=False)
    assert result.exit_code != 0
    assert "no-such-session" in result.output


REAL = Path(__file__).parent / "fixtures" / "token_trace_real" / "sess-1.jsonl"


def test_subagent_files_fill_the_agent_row_and_main_keeps_its_own_turns() -> None:
    rows = _rows(REAL)
    # m1 (spawns the agent) and m2 (orchestrator turn after the spawn) stay in main.
    assert rows["main"]["messages"] == 2
    assert rows["main"]["input"] == 11
    # agent-x holds s1 (two lines, one message) and s2.
    assert rows["agent:explore repo"]["messages"] == 2
    assert rows["agent:explore repo"]["input"] == 300


def test_nested_agent_gets_its_own_row_by_tool_use_id() -> None:
    row = _rows(REAL)["agent:deep dive"]
    assert (row["messages"], row["output"]) == (1, 7)


def test_subagent_file_without_a_spawn_gets_a_row_from_its_meta() -> None:
    assert _rows(REAL)["agent:orphan job"]["cache_read"] == 5


def test_narrow_console_keeps_numbers_whole(monkeypatch) -> None:
    import io

    from lore_cli import trace_cmd
    from rich.console import Console

    buf = io.StringIO()
    monkeypatch.setattr(trace_cmd, "console", Console(width=80, file=buf))
    result = runner.invoke(app, ["tokens", str(FIXTURE)], catch_exceptions=False)
    assert result.exit_code == 0
    for number in ("1270", "405"):
        assert number in buf.getvalue()
    assert "…" not in buf.getvalue()
