"""Typed-fact extraction — stub-LLM replay contract tests.

Every LLM interaction is faked. These tests assert the *call contract* —
one call per chunk, the fact table threaded forward, one corrective retry
per deterministic lint, quotes code-attached from the anchor turn, the
gate in the path, one bounded headline call at the end — and NEVER the
quality of an extracted fact. No LLM-as-judge appears here.

The model's output surface is structured data only: kinds, thread keys,
refs, a short text, a why, and one anchor. It never writes a quote and it
never writes the note's authority phrasing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from lore_core.note_document import Fact, Ref
from lore_core.publish_gate import GateResult
from lore_core.types import ToolCall, ToolResult, Turn
from lore_curator.chunker import Chunk
from lore_curator.fact_extract import (
    EXTRACT_MAX_ATTEMPTS,
    ExtractStatus,
    chunk_view,
    decision_why_lint,
    extract_chunk,
    extract_session,
    fact_anchor_lint,
    fact_kind_lint,
    fact_table,
    fact_tool_schema,
    headline_lint,
)

# ---------------------------------------------------------------------------
# Stub LLM (records call kwargs, replays queued tool_use payloads)
# ---------------------------------------------------------------------------


class _FakeBlock:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.input = payload


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], model: str = "test-model") -> None:
        self.content = [_FakeBlock(payload)]
        self.model = model


class _RecordingMessages:
    """Queue of payloads; ``None`` in the queue simulates a raising call."""

    def __init__(self, payloads: list[dict[str, Any] | None]) -> None:
        self._payloads = list(payloads)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        payload = self._payloads.pop(0) if self._payloads else {}
        if payload is None:
            raise RuntimeError("simulated LLM failure")
        return _FakeResponse(payload)


class _FakeClient:
    def __init__(self, messages: _RecordingMessages) -> None:
        self.messages = messages


def _client(payloads: list[dict[str, Any] | None]) -> tuple[_FakeClient, _RecordingMessages]:
    messages = _RecordingMessages(payloads)
    return _FakeClient(messages), messages


def _prompt_text(call: dict[str, Any]) -> str:
    return call["messages"][0]["content"]


# ---------------------------------------------------------------------------
# Turn fixtures
# ---------------------------------------------------------------------------


def _turn(index: int, role: str = "user", text: str | None = "t") -> Turn:
    return Turn(index=index, timestamp=datetime(2026, 7, 12, tzinfo=UTC), role=role, text=text)


def _tool_turn(index: int, name: str, **inp: Any) -> Turn:
    return Turn(
        index=index,
        timestamp=datetime(2026, 7, 12, tzinfo=UTC),
        role="assistant",
        tool_call=ToolCall(name=name, input=dict(inp)),
    )


def _result_turn(index: int, output: str, *, is_error: bool = False) -> Turn:
    return Turn(
        index=index,
        timestamp=datetime(2026, 7, 12, tzinfo=UTC),
        role="tool_result",
        tool_result=ToolResult(tool_call_id=None, output=output, is_error=is_error),
    )


def _turns(n: int = 12) -> list[Turn]:
    return [_turn(i, text=f"turn {i} text") for i in range(1, n + 1)]


def _fact_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": "done",
        "text": "Segmentation landed as an indices-only call.",
        "thread": "segmentation",
        "refs": [{"type": "pr", "value": "288"}],
        "anchor": 3,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Lints (deterministic, no LLM)
# ---------------------------------------------------------------------------


def test_anchor_lint_flags_anchors_outside_the_chunk():
    facts = [
        Fact(kind="done", text="in", anchor_turn=5),
        Fact(kind="done", text="out", anchor_turn=99),
        Fact(kind="done", text="missing", anchor_turn=-1),
    ]
    assert fact_anchor_lint(facts, from_turn=1, to_turn=10) == [(1, 99), (2, -1)]


def test_kind_lint_flags_kinds_outside_the_enum():
    facts = [
        Fact(kind="done", text="ok", anchor_turn=1),
        Fact(kind="milestone", text="invented", anchor_turn=2),
        Fact(kind="", text="absent", anchor_turn=3),
    ]
    assert fact_kind_lint(facts) == [(1, "milestone"), (2, "")]


def test_decision_why_lint_flags_decisions_with_no_why():
    facts = [
        Fact(kind="decision", text="End-mode extraction.", anchor_turn=1, why="Backward only."),
        Fact(kind="decision", text="Sequential calls.", anchor_turn=2),
        Fact(kind="finding", text="A finding needs no why.", anchor_turn=3),
    ]
    assert decision_why_lint(facts) == [1]


def test_headline_lint_rejects_a_ref_absent_from_the_fact_table():
    facts = [Fact(kind="done", text="Segmentation landed.", anchor_turn=3, refs=[Ref("pr", "288")])]

    assert headline_lint("Segmentation landed in #288.", facts) == ""
    feedback = headline_lint("Segmentation landed in #999.", facts)
    assert "#999" in feedback


def test_headline_lint_rejects_a_thread_absent_from_the_fact_table():
    facts = [Fact(kind="done", text="Segmentation landed.", anchor_turn=3, thread="segmentation")]

    assert headline_lint("Work on `segmentation` closed out.", facts) == ""
    assert "`auth-refactor`" in headline_lint("Work on `auth-refactor` closed out.", facts)


# ---------------------------------------------------------------------------
# Chunk view — the transcript the extractor reads
# ---------------------------------------------------------------------------


def test_chunk_view_keeps_tool_payloads_where_refs_actually_live():
    turns = [
        _turn(1, text="ship it"),
        _tool_turn(2, "Bash", command="git commit -m 'beat-aligned segmentation'"),
        _result_turn(3, "[main 41cab11] beat-aligned segmentation"),
    ]
    view = chunk_view(turns)

    assert "git commit -m 'beat-aligned segmentation'" in view
    assert "41cab11" in view
    assert "@2" in view


# ---------------------------------------------------------------------------
# One extraction call per chunk
# ---------------------------------------------------------------------------


def test_extraction_yields_typed_facts_with_kinds_threads_refs_and_anchors():
    client, messages = _client(
        [
            {
                "facts": [
                    _fact_payload(),
                    _fact_payload(
                        kind="decision",
                        text="Extraction runs at session end.",
                        thread="pipeline",
                        refs=[],
                        why="Which facts matter is only knowable backward.",
                        anchor=5,
                    ),
                ]
            }
        ]
    )
    result = extract_chunk(
        chunk=Chunk(1, 12),
        turns=_turns(),
        llm_client=client,
        model="m",
    )

    assert result.status is ExtractStatus.EXTRACTED
    assert len(messages.calls) == 1
    assert [f.kind for f in result.facts] == ["done", "decision"]
    assert [f.thread for f in result.facts] == ["segmentation", "pipeline"]
    assert result.facts[0].refs == [Ref("pr", "288")]
    assert [f.anchor_turn for f in result.facts] == [3, 5]
    assert result.facts[1].why == "Which facts matter is only knowable backward."


def test_quotes_are_code_attached_verbatim_from_the_anchor_turn():
    """The model has no quote field; the quote comes from the transcript."""
    turns = [
        _turn(1, text="ship it"),
        _tool_turn(2, "Bash", command="gh pr merge 288 --squash"),
        _turn(3, text="merged"),
    ]
    client, _ = _client([{"facts": [_fact_payload(anchor=2, quote="a quote I made up")]}])
    result = extract_chunk(chunk=Chunk(1, 3), turns=turns, llm_client=client, model="m")

    assert "gh pr merge 288 --squash" in result.facts[0].quote
    assert "a quote I made up" not in result.facts[0].quote
    item_props = fact_tool_schema()["input_schema"]["properties"]["facts"]["items"]["properties"]
    assert "quote" not in item_props


def test_zero_facts_is_a_terminal_empty_answer_never_retried():
    client, messages = _client([{"facts": []}])
    result = extract_chunk(chunk=Chunk(1, 12), turns=_turns(), llm_client=client, model="m")

    assert result.status is ExtractStatus.EMPTY
    assert len(messages.calls) == 1


# ---------------------------------------------------------------------------
# One corrective retry per lint
# ---------------------------------------------------------------------------


def test_anchor_lint_earns_exactly_one_corrective_retry():
    client, messages = _client(
        [{"facts": [_fact_payload(anchor=99)]}, {"facts": [_fact_payload(anchor=3)]}]
    )
    result = extract_chunk(chunk=Chunk(1, 12), turns=_turns(), llm_client=client, model="m")

    assert result.status is ExtractStatus.EXTRACTED
    assert len(messages.calls) == EXTRACT_MAX_ATTEMPTS == 2
    retry_prompt = _prompt_text(messages.calls[1])
    assert "1-12" in retry_prompt
    assert "99" in retry_prompt


def test_kind_lint_earns_exactly_one_corrective_retry():
    client, messages = _client(
        [{"facts": [_fact_payload(kind="milestone")]}, {"facts": [_fact_payload(kind="done")]}]
    )
    result = extract_chunk(chunk=Chunk(1, 12), turns=_turns(), llm_client=client, model="m")

    assert result.status is ExtractStatus.EXTRACTED
    assert len(messages.calls) == 2
    retry_prompt = _prompt_text(messages.calls[1])
    assert "milestone" in retry_prompt
    assert "progress" in retry_prompt


def test_decision_without_why_earns_exactly_one_corrective_retry():
    client, messages = _client(
        [
            {"facts": [_fact_payload(kind="decision", why="")]},
            {"facts": [_fact_payload(kind="decision", why="Backward-only knowledge.")]},
        ]
    )
    result = extract_chunk(chunk=Chunk(1, 12), turns=_turns(), llm_client=client, model="m")

    assert result.status is ExtractStatus.EXTRACTED
    assert len(messages.calls) == 2
    assert "why" in _prompt_text(messages.calls[1]).lower()
    assert result.facts[0].why == "Backward-only knowledge."


def test_a_second_lint_miss_fails_the_chunk_without_a_third_call():
    client, messages = _client(
        [{"facts": [_fact_payload(anchor=99)]}, {"facts": [_fact_payload(anchor=98)]}]
    )
    result = extract_chunk(chunk=Chunk(1, 12), turns=_turns(), llm_client=client, model="m")

    assert result.status is ExtractStatus.FAILED
    assert len(messages.calls) == EXTRACT_MAX_ATTEMPTS
    assert result.facts == []


def test_an_llm_failure_never_raises_out_of_extraction():
    client, messages = _client([None, None])
    result = extract_chunk(chunk=Chunk(1, 12), turns=_turns(), llm_client=client, model="m")

    assert result.status is ExtractStatus.FAILED
    assert len(messages.calls) == EXTRACT_MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# The publish gate stays in the path
# ---------------------------------------------------------------------------


class _WithholdingGate:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def evaluate(self, chapter_text: str) -> GateResult:
        self.seen.append(chapter_text)
        return GateResult.withheld("secret", "the text carries an API key")


def test_a_withheld_extraction_carries_its_rendered_text_for_quarantine():
    gate = _WithholdingGate()
    client, messages = _client([{"facts": [_fact_payload()]}, {"facts": [_fact_payload()]}])
    result = extract_chunk(
        chunk=Chunk(1, 12), turns=_turns(), llm_client=client, model="m", gate=gate
    )

    assert result.status is ExtractStatus.WITHHELD
    assert result.withheld_category == "secret"
    assert "Segmentation landed as an indices-only call." in result.withheld_text
    assert len(messages.calls) == EXTRACT_MAX_ATTEMPTS
    assert "the text carries an API key" in _prompt_text(messages.calls[1])


# ---------------------------------------------------------------------------
# Fact-table threading across chunks
# ---------------------------------------------------------------------------


def test_the_fact_table_from_earlier_chunks_reaches_the_next_call():
    client, messages = _client(
        [
            {"facts": [_fact_payload(kind="progress", text="Chunker sketched.", anchor=2)]},
            {"facts": [_fact_payload(kind="done", text="Chunker merged.", anchor=8)]},
        ]
    )
    out = extract_session(
        chunks=[Chunk(1, 6), Chunk(7, 12)],
        turns=_turns(),
        llm_client=client,
        model="m",
        headline=False,
    )

    first_prompt, second_prompt = (_prompt_text(c) for c in messages.calls[:2])
    assert "Chunker sketched." not in first_prompt
    assert "Chunker sketched." in second_prompt  # thread continuity, forward only
    assert "segmentation" in second_prompt
    assert [f.thread for f in out.facts] == ["segmentation", "segmentation"]
    assert [f.kind for f in out.facts] == ["progress", "done"]


def test_fact_table_content_never_surfaces_as_a_new_fact():
    """Two guards: the anchor lint kills a re-anchored echo, dedup kills a copy."""
    earlier = _fact_payload(kind="progress", text="Chunker sketched.", anchor=2)
    # Chunk 2 echoes the table entry, re-anchored into its own range so it
    # would slip past the anchor lint.
    client, messages = _client(
        [
            {"facts": [earlier]},
            {"facts": [_fact_payload(kind="progress", text="Chunker sketched.", anchor=8)]},
        ]
    )
    out = extract_session(
        chunks=[Chunk(1, 6), Chunk(7, 12)],
        turns=_turns(),
        llm_client=client,
        model="m",
        headline=False,
    )

    assert len(messages.calls) == 2  # the echo is dropped, not retried
    assert [f.text for f in out.facts] == ["Chunker sketched."]
    assert out.results[1].status is ExtractStatus.EMPTY

    # ... and an echo that keeps its original anchor is structurally
    # impossible: that anchor lies outside the chunk, so the lint rejects it.
    assert fact_anchor_lint(
        [Fact(kind="progress", text="Chunker sketched.", anchor_turn=2)], from_turn=7, to_turn=12
    ) == [(0, 2)]


def test_the_fact_table_renders_thread_kind_anchor_and_refs():
    table = fact_table(
        [
            Fact(
                kind="done",
                text="Segmentation landed.",
                anchor_turn=3,
                thread="segmentation",
                refs=[Ref("pr", "288")],
            )
        ]
    )
    assert "segmentation" in table
    assert "done" in table
    assert "@3" in table
    assert "pr:288" in table


# ---------------------------------------------------------------------------
# The bounded headline call
# ---------------------------------------------------------------------------


def test_the_headline_is_one_bounded_call_after_the_final_chunk():
    client, messages = _client(
        [
            {"facts": [_fact_payload()]},
            {"headline": "Beat-aligned segmentation shipped in #288."},
        ]
    )
    out = extract_session(
        chunks=[Chunk(1, 12)],
        turns=_turns(),
        llm_client=client,
        model="m",
    )

    assert out.headline == "Beat-aligned segmentation shipped in #288."
    assert len(messages.calls) == 2
    headline_prompt = _prompt_text(messages.calls[1])
    assert "Segmentation landed as an indices-only call." in headline_prompt


def test_a_headline_naming_an_absent_ref_earns_one_retry_then_is_dropped():
    client, messages = _client(
        [
            {"facts": [_fact_payload()]},
            {"headline": "Shipped in #999."},
            {"headline": "Still shipped in #999."},
        ]
    )
    out = extract_session(chunks=[Chunk(1, 12)], turns=_turns(), llm_client=client, model="m")

    assert out.headline == ""  # dropped rather than published with a false ref
    assert len(messages.calls) == 3  # 1 extraction + 2 headline attempts
    assert "#999" in _prompt_text(messages.calls[2])
    assert out.facts  # the facts survive a failed headline


# ---------------------------------------------------------------------------
# Prompt contract: the rules PRD 0008 fixes must reach the model
# ---------------------------------------------------------------------------


def test_the_extraction_prompt_carries_the_rules_the_prd_fixes():
    client, messages = _client([{"facts": [_fact_payload()]}])
    extract_chunk(chunk=Chunk(1, 12), turns=_turns(), llm_client=client, model="m")
    prompt = _prompt_text(messages.calls[0]).lower()

    assert "terminal" in prompt  # terminal-state rule
    assert "month" in prompt  # the month test
    assert "supervis" in prompt  # the supervision clause
    for kind in ("progress", "done", "decision", "finding", "open"):
        assert kind in prompt


def test_an_orchestration_session_yields_progress_en_route_and_done_at_the_end():
    """En-route edits are `progress`; only the terminal PR merge is `done`.

    The stub replays what a rule-following model returns; what is asserted
    here is that extraction preserves the kinds, threads them onto one key,
    and code-attaches the terminal turn as the `done` fact's quote — and
    that the prompt the model was handed carries the terminal-state and
    supervision rules.
    """
    turns = [
        _turn(1, text="orchestrate the epic: dispatch a teammate per sub-issue"),
        _tool_turn(2, "Edit", file_path="lib/lore_curator/chunker.py"),
        _turn(3, "assistant", "teammate reports the chunker is green"),
        _tool_turn(4, "Bash", command="gh pr merge 288 --squash"),
        _result_turn(5, "Squashed and merged pull request #288"),
    ]
    client, messages = _client(
        [
            {
                "facts": [
                    _fact_payload(
                        kind="progress",
                        text="The chunker took shape in lib/lore_curator/chunker.py.",
                        thread="chunker",
                        refs=[{"type": "file", "value": "lib/lore_curator/chunker.py"}],
                        anchor=2,
                    ),
                    _fact_payload(
                        kind="done",
                        text="Beat-aligned segmentation merged.",
                        thread="chunker",
                        refs=[{"type": "pr", "value": "288"}],
                        anchor=4,
                    ),
                ]
            }
        ]
    )
    result = extract_chunk(chunk=Chunk(1, 5), turns=turns, llm_client=client, model="m")

    assert [f.kind for f in result.facts] == ["progress", "done"]
    assert {f.thread for f in result.facts} == {"chunker"}
    assert "gh pr merge 288 --squash" in result.facts[1].quote
    prompt = _prompt_text(messages.calls[0])
    assert "deliverable" in prompt.lower()  # supervision clause: not the choreography


# ---------------------------------------------------------------------------
# Structural guarantee
# ---------------------------------------------------------------------------


def test_extraction_never_writes_to_disk():
    """Extraction produces data; the ledger and the note are written elsewhere."""
    from pathlib import Path

    from lore_curator import fact_extract

    src = Path(fact_extract.__file__).read_text()
    for forbidden in ("write_text", "atomic_write", "append_facts"):
        assert forbidden not in src, f"fact_extract must not reference {forbidden!r}"
