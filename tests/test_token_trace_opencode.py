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


def test_the_estimate_prices_reasoning_tokens_at_the_output_rate() -> None:
    base = 1000 * 4.0 / 1e6 + 500 * 20.0 / 1e6
    assert summarize([_record(cost=0)]).total.cost == pytest.approx(base)  # no reasoning: same
    with_reasoning = summarize([_record(cost=0, reasoning=300)]).total.cost
    assert with_reasoning == pytest.approx(base + 300 * 20.0 / 1e6)


def test_a_reasoning_only_row_with_zero_reported_cost_keeps_the_estimate() -> None:
    row = {"cost": 0, "input": 0, "output": 0, "reasoning": 1000}
    assert summarize([_record(**row)]).total.cost == pytest.approx(1000 * 20.0 / 1e6)


def test_two_subagent_calls_with_one_prompt_match_two_child_sessions(tmp_path: Path) -> None:
    folder, cwd = tmp_path / "oc", tmp_path / "proj"
    spawns = [
        oc.tool(
            "subagent",
            f"call_{i}",
            {"agent": "lore-mid", "description": f"d{i}", "prompt": "Same."},
        )
        for i in (1, 2)
    ]
    main = oc.write_session(
        folder,
        "ses_main",
        cwd,
        [oc.user("go"), oc.assistant("m1", [spawns[0]]), oc.assistant("m2", [spawns[1]])],
    )
    # OpenCode 2.x IDs sort newest-first: the second spawn's child has the smaller ID.
    for sid, i in (("ses_b", 1), ("ses_a", 2)):
        oc.write_session(
            folder,
            sid,
            cwd,
            [
                oc.user("Same.", created=1_700_000_000_000 + i),
                oc.assistant(f"c{i}", [oc.text("x")], tokens=(i, 0, 0, 0, 0)),
            ],
            parent="ses_main",
        )
    rows = {p.name: p.input for p in trace_tokens(main) if p.name.startswith("agent:")}
    assert rows == {"agent:d1": 1, "agent:d2": 2}


def test_an_empty_child_session_file_does_not_break_the_trace(tmp_path: Path) -> None:
    folder, cwd = tmp_path / "oc", tmp_path / "proj"
    spawn = oc.tool("subagent", "call_1", {"agent": "lore-mid", "prompt": "Do it."})
    main = oc.write_session(folder, "ses_main", cwd, [oc.user("go"), oc.assistant("m1", [spawn])])
    oc.write_session(folder, "ses_empty", cwd, [], parent="ses_main")
    oc.write_session(
        folder,
        "ses_ok",
        cwd,
        [oc.user("Do it."), oc.assistant("c1", [oc.text("x")], tokens=(3, 0, 0, 0, 0))],
        parent="ses_main",
    )
    rows = [p for p in trace_tokens(main) if p.name.startswith("agent:")]
    assert [r.input for r in rows if r.input] == [3]


def _spawn(i: int, prompt: str, child: str | None = None, call_id: str | None = None) -> dict:
    part = oc.tool("subagent", call_id or f"call_{i}", {"description": f"d{i}", "prompt": prompt})
    if call_id is None and child is None:
        part.pop("id")
    if child:
        part["state"]["metadata"] = {"sessionID": child}
    return part


def _child(folder: Path, cwd: Path, sid: str, text: str, tokens: int, at: int = 0) -> None:
    oc.write_session(
        folder,
        sid,
        cwd,
        [
            oc.user(text, created=1_700_000_000_000 + at),
            oc.assistant(f"c_{sid}", [oc.text("x")], tokens=(tokens, 0, 0, 0, 0)),
        ],
        parent="ses_main",
    )


def _agents(main: Path) -> dict[str, int]:
    return {p.name: p.input for p in trace_tokens(main) if p.name.startswith("agent:")}


def test_a_prompt_that_is_only_part_of_the_child_text_does_not_match(tmp_path: Path) -> None:
    folder, cwd = tmp_path / "oc", tmp_path / "proj"
    main = oc.write_session(
        folder, "ses_main", cwd, [oc.user("go"), oc.assistant("m1", [_spawn(1, "Fix it.")])]
    )
    preamble = "You are a subagent spawned by another session.\n"
    _child(folder, cwd, "ses_x", preamble + "Fix it. Then write the docs.", 5)
    assert _agents(main) == {"agent:d1": 0, "agent:subagent": 5}


def test_a_call_matches_the_child_its_metadata_names(tmp_path: Path) -> None:
    folder, cwd = tmp_path / "oc", tmp_path / "proj"
    spawns = [_spawn(1, "Same.", child="ses_b"), _spawn(2, "Same.", child="ses_a")]
    main = oc.write_session(folder, "ses_main", cwd, [oc.user("go"), oc.assistant("m1", spawns)])
    # spawn order disagrees with the metadata: the metadata wins
    _child(folder, cwd, "ses_a", "Same.", 2, at=1)
    _child(folder, cwd, "ses_b", "Same.", 1, at=2)
    assert _agents(main) == {"agent:d1": 1, "agent:d2": 2}


def test_two_calls_with_no_id_match_two_children(tmp_path: Path) -> None:
    folder, cwd = tmp_path / "oc", tmp_path / "proj"
    main = oc.write_session(
        folder,
        "ses_main",
        cwd,
        [oc.user("go"), oc.assistant("m1", [_spawn(1, "One."), _spawn(2, "Two.")])],
    )
    _child(folder, cwd, "ses_a", "One.", 1, at=1)
    _child(folder, cwd, "ses_b", "Two.", 2, at=2)
    assert _agents(main) == {"agent:d1": 1, "agent:d2": 2}


def test_a_utf8_session_file_reads_under_a_non_utf8_locale(
    session: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import locale

    text = session.read_text(encoding="utf-8").replace('"go"', '"gö ✓"')
    session.write_text(text, encoding="utf-8")
    monkeypatch.setattr(locale, "getpreferredencoding", lambda *a, **k: "ascii")
    monkeypatch.setattr(locale, "getencoding", lambda: "ascii", raising=False)
    assert trace_tokens(session)[0].messages == 2


def test_a_row_with_only_1h_cache_writes_keeps_the_estimate() -> None:
    row = {"cost": 0, "input": 0, "output": 0, "cache_write_1h": 1_000_000}
    assert summarize([_record(**row)]).total.cost > 0
