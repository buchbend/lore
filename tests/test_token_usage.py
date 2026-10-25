"""Token-usage statistics: model per message, prices, token records and their readers.

Covers ``lore_core.token_trace`` (served and requested model per phase),
``lore_core.token_prices``, ``lore_core.token_usage``, the SessionEnd hook
``lore hook token-usage`` and the summary form of ``lore trace tokens``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from lore_cli.hooks import hook_app
from lore_cli.trace_cmd import app as trace_app
from lore_core.token_prices import estimate_cost, price_for
from lore_core.token_trace import model_matches, trace_tokens
from lore_core.token_usage import (
    append_record,
    build_record,
    load_records,
    record_session,
    records_path,
    summarize,
)
from typer.testing import CliRunner

runner = CliRunner()

_NOW = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)


def _msg(mid: str, model: str | None, usage: dict, content: list | None = None, **extra) -> str:
    message = {"id": mid, "role": "assistant", "content": content or [], "usage": usage}
    if model is not None:
        message["model"] = model
    return json.dumps({"type": "assistant", "message": message, **extra})


def _usage(i: int = 0, o: int = 0, r: int = 0, w: int = 0, w1h: int = 0) -> dict:
    usage = {
        "input_tokens": i,
        "output_tokens": o,
        "cache_read_input_tokens": r,
        "cache_creation_input_tokens": w,
    }
    if w1h:
        usage["cache_creation"] = {
            "ephemeral_5m_input_tokens": w - w1h,
            "ephemeral_1h_input_tokens": w1h,
        }
    return usage


def _spawn(tool_id: str, description: str, **inp) -> dict:
    return {
        "type": "tool_use",
        "id": tool_id,
        "name": "Agent",
        "input": {"description": description, **inp},
    }


@pytest.fixture
def session(tmp_path: Path) -> Path:
    """Main thread on Opus spawning two subagents in their own files.

    ``impl`` asked for Sonnet and got it; ``review`` asked for Haiku and
    ran on Opus.
    """
    path = tmp_path / "sess-9.jsonl"
    lines = [
        _msg("m1", "claude-opus-5-5", _usage(10, 100, 1000, 200, w1h=50)),
        _msg(
            "m2",
            "claude-opus-5-5",
            _usage(1, 10, 100, 0),
            [
                _spawn("toolu_impl", "impl", model="claude-sonnet-5-5", effort="medium"),
                _spawn("toolu_rev", "review", model="haiku"),
            ],
        ),
        _msg("m3", "<synthetic>", _usage()),
    ]
    path.write_text("\n".join(lines) + "\n")
    sub = tmp_path / "sess-9" / "subagents"
    sub.mkdir(parents=True)
    for name, tool_id, model, usage in (
        ("agent-a", "toolu_impl", "claude-sonnet-5-5", _usage(5, 50, 500, 100)),
        ("agent-b", "toolu_rev", "claude-opus-5-5", _usage(2, 20, 200, 0)),
    ):
        (sub / f"{name}.jsonl").write_text(_msg(f"{name}-1", model, usage) + "\n")
        (sub / f"{name}.meta.json").write_text(json.dumps({"toolUseId": tool_id}))
    return path


# ---------------------------------------------------------------------------
# token_trace: served and requested model
# ---------------------------------------------------------------------------


def test_each_phase_splits_tokens_by_served_model(session: Path) -> None:
    rows = {p.name: p for p in trace_tokens(session)}
    main = rows["main"]
    assert set(main.models) == {"claude-opus-5-5"}  # <synthetic> carries no tokens
    assert main.models["claude-opus-5-5"].output == 110
    assert main.cache_write_1h == 50
    assert set(rows["agent:impl"].models) == {"claude-sonnet-5-5"}


def test_agent_rows_keep_the_requested_model_and_effort(session: Path) -> None:
    rows = {p.name: p for p in trace_tokens(session)}
    impl, review = rows["agent:impl"], rows["agent:review"]
    assert (impl.requested_model, impl.requested_effort) == ("claude-sonnet-5-5", "medium")
    assert not impl.model_mismatch
    assert review.requested_model == "haiku"
    assert review.model_mismatch
    assert impl.as_dict()["requested"] == {"model": "claude-sonnet-5-5", "effort": "medium"}


def test_a_line_without_a_model_counts_under_question_mark(tmp_path: Path) -> None:
    path = tmp_path / "old.jsonl"
    path.write_text(_msg("m1", None, _usage(1, 2, 3, 4)) + "\n")
    (main,) = trace_tokens(path)
    assert set(main.models) == {"?"}
    assert main.total == 10


@pytest.mark.parametrize(
    ("requested", "served", "expected"),
    [
        ("claude-sonnet-5-5", "claude-sonnet-5-5", True),
        ("claude-sonnet-5-5", "claude-sonnet-5-5[1m]", True),
        ("sonnet", "claude-sonnet-5-5", True),
        ("haiku", "claude-opus-5-5", False),
        ("claude-sonnet-5", "claude-opus-5-5", False),
        ("claude-sonnet-5", "claude-sonnet-5-5", False),
        ("claude-sonnet-5-5", "claude-sonnet-5", False),
        ("claude-haiku-4-5", "claude-haiku-4-5-20251001", True),
        ("inherit", "claude-opus-5-5", True),
    ],
)
def test_model_matches(requested: str, served: str, expected: bool) -> None:
    assert model_matches(requested, served) is expected


# ---------------------------------------------------------------------------
# token_prices
# ---------------------------------------------------------------------------


def test_price_lookup_tolerates_suffixes_and_prefixes() -> None:
    opus = price_for("claude-opus-5-5")
    assert opus == {"input": 4.0, "output": 20.0, "cache_read": 0.20}
    assert price_for("claude-opus-5-5[1m]") == opus
    assert price_for("us.anthropic.claude-opus-5-5") == opus
    assert price_for("claude-haiku-4-5-20251001") == price_for("claude-haiku-4-5")
    assert price_for("claude-opus-5-5-20260101") == opus
    # A newer model of a family is unpriced, not priced as its predecessor.
    assert price_for("claude-sonnet-5-7") is None
    assert price_for("gpt-5") is None


def test_price_override_wins_and_adds_models() -> None:
    overrides = {"claude-opus-5-5": {"input": 1, "output": 2, "cache_read": 0.1}}
    assert price_for("claude-opus-5-5", overrides)["input"] == 1.0
    assert price_for("my-model", {"my-model": {"input": 1, "output": 1, "cache_read": 1}})
    assert price_for("claude-opus-5-5", {"claude-opus-5-5": {"input": "x"}}) is None


def test_estimate_cost_bills_cache_writes_by_ttl() -> None:
    counts = {
        "input": 1_000_000,
        "output": 1_000_000,
        "cache_read": 1_000_000,
        "cache_write": 2_000_000,
        "cache_write_1h": 1_000_000,
    }
    # 4 + 20 + 0.20 + 1M * 4 * 1.25 + 1M * 4 * 2
    assert estimate_cost("claude-opus-5-5", counts) == pytest.approx(4 + 20 + 0.2 + 5 + 8)
    assert estimate_cost("unknown-model", counts) is None


# ---------------------------------------------------------------------------
# token_usage: the token records
# ---------------------------------------------------------------------------


def test_build_record_has_one_row_per_phase_and_model(session: Path) -> None:
    record = build_record("sess-9", session, cwd="/repo", now=_NOW)
    assert record["session"] == "sess-9"
    assert record["recorded_at"] == "2026-10-20T12:00:00Z"
    rows = {(r["phase"], r["model"]): r for r in record["rows"]}
    assert set(rows) == {
        ("main", "claude-opus-5-5"),
        ("agent:impl", "claude-sonnet-5-5"),
        ("agent:review", "claude-opus-5-5"),
    }
    assert rows[("agent:impl", "claude-sonnet-5-5")]["requested_effort"] == "medium"
    assert rows[("main", "claude-opus-5-5")]["cache_write_1h"] == 50


def test_the_last_record_for_a_session_wins(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    append_record(root, {"session": "a", "recorded_at": "2026-10-01T00:00:00Z", "rows": [1]})
    append_record(root, {"session": "b", "recorded_at": "2026-10-01T00:00:00Z", "rows": []})
    append_record(root, {"session": "a", "recorded_at": "2026-10-02T00:00:00Z", "rows": [2]})
    with records_path(root).open("a") as fh:
        fh.write("not json\n")
    records = load_records(root)
    assert [r["session"] for r in records] == ["b", "a"]
    assert records[1]["rows"] == [2]


def test_records_sit_beside_the_wiki_not_inside_it(tmp_path: Path) -> None:
    assert records_path(tmp_path) == tmp_path / ".lore" / "token-usage.jsonl"


def test_summarize_groups_by_model_phase_and_request(session: Path) -> None:
    record = build_record("sess-9", session, now=_NOW)
    old = {**record, "session": "old", "recorded_at": "2026-01-01T00:00:00Z"}
    summary = summarize([old, record], since=_NOW - timedelta(days=7))
    assert summary.sessions == 1
    assert set(summary.by_model) == {"claude-opus-5-5", "claude-sonnet-5-5"}
    assert set(summary.by_stage) == {"main", "untagged subagents"}
    assert set(summary.by_request) == {
        "untagged subagents · claude-sonnet-5-5 @ medium",
        "untagged subagents · haiku",
    }
    assert [(m.phase, m.requested, m.served) for m in summary.mismatches] == [
        ("agent:review", "haiku", "claude-opus-5-5")
    ]
    by_model_cost = sum(b.cost for b in summary.by_model.values())
    assert summary.total.cost == pytest.approx(by_model_cost)
    assert summary.total.cost > 0


def test_summarize_reports_unpriced_models() -> None:
    record = {
        "session": "s",
        "recorded_at": "2026-10-20T00:00:00Z",
        "rows": [{"phase": "main", "model": "mystery", "input": 5}],
    }
    summary = summarize([record])
    assert summary.unpriced_models == {"mystery"}
    assert summary.by_model["mystery"].unpriced == 1


# ---------------------------------------------------------------------------
# The SessionEnd hook
# ---------------------------------------------------------------------------


def _hook(payload: dict, monkeypatch, tmp_path: Path) -> list[tuple]:
    calls: list[tuple] = []
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "vault"))
    monkeypatch.setattr("lore_cli.hooks._spawn_token_usage_record", lambda *a: calls.append(a))
    result = runner.invoke(hook_app, ["token-usage"], input=json.dumps(payload))
    assert result.exit_code == 0
    return calls


def test_hook_hands_the_transcript_to_a_child(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    payload = {"session_id": "s1", "transcript_path": "/t/s1.jsonl", "cwd": "/repo"}
    calls = _hook(payload, monkeypatch, tmp_path)
    assert calls == [(tmp_path / "vault", "s1", "/t/s1.jsonl", "/repo")]


def test_hook_records_nothing_without_a_transcript(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    assert _hook({"session_id": "s1"}, monkeypatch, tmp_path) == []


def test_hook_records_nothing_while_muted(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.setattr("lore_cli.hooks._session_off_all", lambda: True)
    payload = {"session_id": "s1", "transcript_path": "/t/s1.jsonl"}
    assert _hook(payload, monkeypatch, tmp_path) == []


def test_child_appends_the_session_record(session: Path, monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "vault"
    monkeypatch.setenv("LORE_ROOT", str(root))
    args = ["token-usage-record", "--session", "sess-9", "--transcript", str(session)]
    result = runner.invoke(hook_app, [*args, "--cwd", "/repo"])
    assert result.exit_code == 0
    (record,) = load_records(root)
    assert record["session"] == "sess-9"
    assert record["cwd"] == "/repo"


def test_plugin_wires_the_hook_on_session_end() -> None:
    manifest = Path(__file__).parent.parent / ".claude-plugin" / "plugin.json"
    hooks = json.loads(manifest.read_text())["hooks"]
    commands = [h["command"] for grp in hooks["SessionEnd"] for h in grp["hooks"]]
    assert "lore hook token-usage" in commands


# ---------------------------------------------------------------------------
# `lore trace tokens` with no session: the summary
# ---------------------------------------------------------------------------


def test_summary_says_when_nothing_is_recorded(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "vault"))
    result = runner.invoke(trace_app, ["tokens"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "no sessions recorded" in result.output


def test_summary_table_and_json(session: Path, monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "vault"
    monkeypatch.setenv("LORE_ROOT", str(root))
    record_session(root, "sess-9", session)

    result = runner.invoke(trace_app, ["tokens", "--days", "0"], catch_exceptions=False)
    assert result.exit_code == 0
    for word in ("1 session,", "by model", "opus-5-5", "by stage", "untagged", "asked haiku"):
        assert word in result.output

    result = runner.invoke(trace_app, ["tokens", "--json"], catch_exceptions=False)
    data = json.loads(result.output)
    assert data["sessions"] == 1
    assert data["days"] == 30
    assert set(data["by_model"]) == {"claude-opus-5-5", "claude-sonnet-5-5"}
    assert data["total"]["cost_usd"] > 0


def test_session_table_shows_the_served_model(session: Path) -> None:
    result = runner.invoke(trace_app, ["tokens", str(session)], catch_exceptions=False)
    assert result.exit_code == 0
    assert "model" in result.output
    assert "sonnet-5-5" in result.output
    assert "asked haiku" in result.output
