"""Token record from OpenCode session files (synthesized from the 2.x types)."""

from __future__ import annotations

import json
from pathlib import Path

import opencode_session as oc
import pytest
from lore_core.token_trace import trace_tokens
from lore_core.token_usage import build_record, summarize


@pytest.fixture()
def session(tmp_path: Path) -> Path:
    folder, cwd = tmp_path / "oc", tmp_path / "proj"
    spawn = oc.tool(
        "subagent",
        "call_s",
        {
            "agent": "lore-mid",
            "description": "[build:implement] buchbend/lore#613 capture",
            "prompt": "Implement the capture.",
        },
    )
    main = oc.write_session(
        folder,
        "ses_main",
        cwd,
        [
            oc.user("go"),
            oc.assistant("m1", [oc.text("a")], tokens=(10, 5, 2, 100, 20), cost=0.5),
            oc.assistant("m2", [spawn], tokens=(7, 3, 0, 50, 0), cost=0.25),
            # the same message listed twice (a second flush): counts once
            oc.assistant("m2", [spawn], tokens=(7, 3, 0, 50, 0), cost=0.25),
            oc.idle(),
        ],
    )
    oc.write_session(
        folder,
        "ses_child",
        cwd,
        [
            oc.user("You are a subagent spawned by another session.\nImplement the capture."),
            oc.assistant(
                "c1",
                [oc.text("done")],
                tokens=(40, 9, 4, 300, 60),
                cost=1.0,
                model="claude-haiku-4-5",
            ),
        ],
        parent="ses_main",
    )
    return main


def test_main_row_counts_input_output_reasoning_cache_and_cost(session: Path) -> None:
    main = trace_tokens(session)[0]
    assert main.name == "main"
    assert (main.messages, main.input, main.output) == (2, 17, 8)
    assert (main.cache_read, main.cache_write, main.reasoning, main.cost) == (150, 20, 2, 0.75)


def test_subagent_call_makes_a_tagged_row_from_the_child_session(session: Path) -> None:
    rows = {p.name: p for p in trace_tokens(session)}
    agent = rows["agent:[build:implement] buchbend/lore#613 capture"]
    assert (agent.stage, agent.target) == ("build:implement", "buchbend/lore#613")
    assert (agent.messages, agent.input, agent.reasoning, agent.cost) == (1, 40, 4, 1.0)
    assert set(agent.models) == {"claude-haiku-4-5"}


def test_record_carries_reasoning_and_the_cost_opencode_reported(session: Path) -> None:
    record = build_record("ses_main", session, cwd="")
    by_stage = {r["stage"]: r for r in record["rows"]}
    assert by_stage["build:implement"]["reasoning"] == 4
    assert by_stage[None]["cost"] == 0.75
    summary = summarize([record])
    assert summary.by_stage["build:implement"].cost == 1.0  # reported, not estimated
    assert summary.total.cost == 1.75
    json.dumps(summary.as_dict())


def test_a_claude_code_transcript_still_traces_as_before() -> None:
    fixture = Path(__file__).parent / "fixtures" / "token_trace_session.jsonl"
    assert trace_tokens(fixture)[0].reasoning == 0


def _record(**row) -> dict:
    base = {
        "phase": "main",
        "model": "claude-opus-5-5",
        "messages": 1,
        "input": 1000,
        "output": 500,
    }
    return {"session": "s", "recorded_at": "2026-01-01T00:00:00Z", "rows": [{**base, **row}]}


def test_reasoning_tokens_count_in_the_phase_and_bucket_totals(session: Path) -> None:
    main = trace_tokens(session)[0]
    assert main.total == 17 + 8 + 150 + 20 + 2
    assert main.counts()["total"] == main.total
    assert summarize([_record(reasoning=300)]).total.total == 1800


def test_a_zero_reported_cost_with_tokens_keeps_the_estimate() -> None:
    estimate = 1000 * 4.0 / 1e6 + 500 * 20.0 / 1e6
    assert summarize([_record(cost=0)]).total.cost == pytest.approx(estimate)
    assert summarize([_record(cost=0.5)]).total.cost == 0.5
    assert summarize([_record(cost=0, input=0, output=0)]).total.cost == 0


def test_claude_code_rows_keep_their_totals() -> None:
    assert summarize([_record()]).total.total == 1500
