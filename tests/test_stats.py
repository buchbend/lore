"""Stage tags, epic attribution and the `lore stats` page.

Covers ``lore_core.stages``, the stage and target that
``lore_core.token_trace`` gives each row, the summary groups they feed, and
``lore_core.stats_page`` / ``lore stats``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lore_cli.stats_cmd import app as stats_app
from lore_core.stages import parse_ref, parse_tag, ref_label
from lore_core.stats_page import page_path, render, write_page
from lore_core.token_trace import trace_tokens
from lore_core.token_usage import append_record, build_record, summarize
from typer.testing import CliRunner

runner = CliRunner()

_NOW = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
_USAGE = {"input_tokens": 1, "output_tokens": 1000, "cache_read_input_tokens": 10}


def _assistant(mid: str, content: list | None = None, model: str = "claude-opus-5-5") -> str:
    message = {"id": mid, "model": model, "content": content or [], "usage": _USAGE}
    return json.dumps({"type": "assistant", "message": message})


def _user(text: str, **extra) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}, **extra})


def _command(name: str, args: str) -> str:
    return _user(
        f"<command-message>{name}</command-message>\n<command-name>/{name}</command-name>\n"
        f"<command-args>{args}</command-args>"
    )


def _spawn(tool_id: str, description: str, prompt: str = "do it") -> dict:
    return {
        "type": "tool_use",
        "id": tool_id,
        "name": "Agent",
        "input": {"description": description, "prompt": prompt, "model": "sonnet"},
    }


def _write(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n")
    return path


# ---------------------------------------------------------------------------
# lore_core.stages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[build:review] PR 12 of buchbend/lore#510", ("build:review", "buchbend/lore#510")),
        ("  [orient:explore] code map", ("orient:explore", None)),
        ("[build:implement] #511\nbody #999", ("build:implement", "#511")),
        ("review [build:review]", (None, None)),
        ("[TIER-DELEGATION.md](x)", (None, None)),
        (None, (None, None)),
    ],
)
def test_parse_tag(text, expected) -> None:
    assert parse_tag(text) == expected


def test_parse_ref_and_label() -> None:
    assert parse_ref("epic buchbend/lore#510") == "buchbend/lore#510"
    assert parse_ref("issue #42 please") == "#42"
    assert parse_ref("PR 42") is None
    assert ref_label("buchbend/lore#510") == "lore#510"
    assert ref_label("#510", "/home/me/git/lore") == "lore#510"
    assert ref_label("#510") == "#510"


# ---------------------------------------------------------------------------
# token_trace: stage and target
# ---------------------------------------------------------------------------


def test_a_slash_command_starts_a_skill_phase_with_its_target(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "s.jsonl",
        [
            _user("hello"),
            _assistant("m1"),
            _command("model", "sonnet"),  # built-in: no phase
            _command("lore-workflow:build", "epic buchbend/lore#510"),
            _assistant("m2", [_spawn("t1", "[build:review] PR 12")]),
            _assistant("m3", [_spawn("t2", "[build:implement] #511 feature")]),
            _assistant("m4", [_spawn("t3", "look around")]),
        ],
    )
    rows = {p.name: p for p in trace_tokens(path)}
    assert list(rows) == [
        "main",
        "skill:lore-workflow:build",
        "agent:[build:review] PR 12",
        "agent:[build:implement] #511 feature",
        "agent:look around",
    ]
    assert rows["main"].target is None
    assert rows["skill:lore-workflow:build"].target == "buchbend/lore#510"
    review = rows["agent:[build:review] PR 12"]
    assert (review.stage, review.target) == ("build:review", "buchbend/lore#510")
    implement = rows["agent:[build:implement] #511 feature"]
    assert (implement.stage, implement.target) == ("build:implement", "#511")
    untagged = rows["agent:look around"]
    assert (untagged.stage, untagged.target) == (None, "buchbend/lore#510")


def test_a_tag_in_the_prompt_counts_when_the_description_has_none(tmp_path: Path) -> None:
    spawn = _spawn("t1", "feature 3", prompt="[build:implement] buchbend/lore#510\n\nImplement")
    path = _write(tmp_path / "s.jsonl", [_assistant("m1", [spawn])])
    (_, agent) = trace_tokens(path)
    assert (agent.stage, agent.target) == ("build:implement", "buchbend/lore#510")


def test_a_teammate_session_takes_the_tag_of_its_first_prompt(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "s.jsonl",
        [
            _user("[build:implement] buchbend/lore#510\n\nImplement **x**"),
            _assistant(
                "m1",
                [
                    {
                        "type": "tool_use",
                        "id": "s",
                        "name": "Skill",
                        "input": {"skill": "lore-workflow:tdd"},
                    }
                ],
            ),
            _assistant("m2"),
        ],
    )
    main, tdd = trace_tokens(path)
    assert (main.stage, main.target) == ("build:implement", "buchbend/lore#510")
    assert (tdd.stage, tdd.target) == ("build:implement", "buchbend/lore#510")


def test_meta_and_sidechain_user_lines_set_nothing(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "s.jsonl",
        [
            _user("[build:review] #1", isMeta=True),
            _user("[build:review] #2", isSidechain=True),
            _user("plain request"),
            _assistant("m1"),
        ],
    )
    (main,) = trace_tokens(path)
    assert (main.stage, main.target) == (None, None)


def test_a_nested_spawn_inherits_its_parents_target(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "s.jsonl",
        [
            _command("lore-workflow:build", "epic buchbend/lore#510"),
            _assistant("m1", [_spawn("t1", "[build:review] PR")]),
        ],
    )
    sub = tmp_path / "s" / "subagents"
    sub.mkdir(parents=True)
    _write(sub / "agent-a.jsonl", [_assistant("a1", [_spawn("t2", "dig deeper")])])
    (sub / "agent-a.meta.json").write_text(json.dumps({"toolUseId": "t1"}))
    _write(sub / "agent-b.jsonl", [_assistant("b1")])
    (sub / "agent-b.meta.json").write_text(json.dumps({"toolUseId": "t2"}))
    rows = {p.name: p for p in trace_tokens(path)}
    assert rows["agent:dig deeper"].target == "buchbend/lore#510"
    assert rows["agent:dig deeper"].stage is None
    assert rows["agent:dig deeper"].output == 1000


# ---------------------------------------------------------------------------
# Records and summary
# ---------------------------------------------------------------------------


@pytest.fixture
def epic_session(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "s.jsonl",
        [
            _command("lore-workflow:build", "epic #510"),
            _assistant("m1", [_spawn("t1", "[build:review] PR 12")]),
            _assistant("m2", [_spawn("t2", "untagged helper")], model="claude-sonnet-5-5"),
            _assistant("m3"),
        ],
    )


def test_records_carry_stage_and_a_repo_target(epic_session: Path) -> None:
    record = build_record("s1", epic_session, cwd="/home/me/git/lore", now=_NOW)
    by_phase = {r["phase"]: r for r in record["rows"]}
    assert by_phase["skill:lore-workflow:build"]["target"] == "lore#510"
    assert by_phase["skill:lore-workflow:build"]["stage"] is None


def test_summary_groups_by_stage_target_day_and_session(epic_session: Path) -> None:
    record = build_record("s1", epic_session, cwd="/home/me/git/lore", now=_NOW)
    other = {
        "session": "s2",
        "recorded_at": "2026-10-18T09:00:00Z",
        "cwd": "/x",
        "rows": [{"phase": "main", "model": "claude-opus-5-5", "output": 10}],
    }
    summary = summarize([other, record])
    # No subagent files: the messages after a spawn count in that agent's row.
    assert set(summary.by_stage) == {
        "main",
        "skill:lore-workflow:build",
        "build:review",
        "untagged subagents",
    }
    assert set(summary.by_target) == {"lore#510", "no epic or issue"}
    assert set(summary.by_day) == {"2026-10-18", "2026-10-20"}
    assert [s.session for s in summary.per_session] == ["s1", "s2"]
    assert summary.per_session[0].targets == {"lore#510"}
    assert summary.latest == "2026-10-20T12:00:00Z"


def test_old_records_without_stage_or_target_still_sum() -> None:
    record = {
        "session": "old",
        "recorded_at": "2026-10-01T00:00:00Z",
        "rows": [{"phase": "agent:x", "model": "claude-opus-5-5", "output": 10}],
    }
    summary = summarize([record])
    assert set(summary.by_stage) == {"untagged subagents"}
    assert set(summary.by_target) == {"no epic or issue"}


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def _summary(epic_session: Path):
    record = build_record("s1", epic_session, cwd="/home/me/git/lore", now=_NOW)
    return summarize([record])


def test_the_page_is_deterministic_and_self_contained(epic_session: Path) -> None:
    page = render(_summary(epic_session), span="last 30 days", prices_as_of="2026-10-06")
    again = render(_summary(epic_session), span="last 30 days", prices_as_of="2026-10-06")
    assert page == again
    assert page.startswith("<!doctype html>")
    for absent in ("<script", "http://", "https://", "<link"):
        assert absent not in page
    for present in ("By workflow stage", "By epic or issue", "lore#510", "build:review"):
        assert present in page
    assert "prefers-color-scheme: dark" in page


def test_the_page_escapes_record_text() -> None:
    record = {
        "session": "<b>",
        "recorded_at": "2026-10-20T00:00:00Z",
        "cwd": "/x/<repo>",
        "rows": [{"phase": "agent:<x>", "stage": "<s>", "model": "claude-opus-5-5", "output": 9}],
    }
    page = render(summarize([record]), span="all", prices_as_of="2026-10-06")
    assert "<s>" not in page and "&lt;s&gt;" in page
    assert "<repo>" not in page


def test_an_empty_page_says_nothing_is_recorded() -> None:
    page = render(summarize([]), span="last 30 days", prices_as_of="2026-10-06")
    assert "No sessions recorded" in page


def test_write_page_sums_the_window(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    row = {"phase": "main", "model": "claude-opus-5-5", "output": 1_000_000}
    append_record(root, {"session": "new", "recorded_at": "2026-10-19T00:00:00Z", "rows": [row]})
    append_record(root, {"session": "old", "recorded_at": "2026-08-01T00:00:00Z", "rows": [row]})
    path = write_page(root, days=7, now=_NOW)
    assert path == page_path(root) == root / ".lore" / "stats.html"
    text = path.read_text()
    assert "$20.00" in text and "$40.00" not in text
    assert "$40.00" in write_page(root, days=0, now=_NOW).read_text()


# ---------------------------------------------------------------------------
# `lore stats`
# ---------------------------------------------------------------------------


def test_stats_writes_and_opens_the_page(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "vault"))
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    result = runner.invoke(stats_app, [], catch_exceptions=False)
    assert result.exit_code == 0
    page = tmp_path / "vault" / ".lore" / "stats.html"
    assert page.is_file()
    assert result.output.strip() == str(page)
    assert opened == [page.as_uri()]


def test_stats_no_open_only_writes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "vault"))
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    result = runner.invoke(stats_app, ["--no-open", "--days", "0"], catch_exceptions=False)
    assert result.exit_code == 0
    assert opened == []
