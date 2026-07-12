"""Beat-aligned session segmentation — stub-LLM replay contract tests.

Every LLM interaction is faked. The segmenter's whole model-output surface
is a list of turn indices, so these tests assert boundary handling: lints,
the single corrective retry, the size band, windowed stitching, and the
fixed-window fallback. No prose ever crosses the boundary, and nothing here
judges model quality.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from lore_core.types import ToolCall, ToolResult, Turn
from lore_curator.chunker import (
    CHUNK_MAX_TURNS,
    CHUNK_MIN_TURNS,
    FALLBACK_WINDOW_TURNS,
    SEGMENT_MAX_ATTEMPTS,
    SEGMENT_MAX_VIEW_CHARS,
    Chunk,
    boundary_lint,
    collapsed_view,
    normalize_chunks,
    segment_session,
    segment_tool_schema,
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
    def __init__(self, payloads: list[dict[str, Any] | None]) -> None:
        self.messages = _RecordingMessages(payloads)


def _prompt_text(call: dict[str, Any]) -> str:
    return call["messages"][0]["content"]


def _turns(count: int, *, start: int = 0) -> list[Turn]:
    """A plain text-turn transcript, alternating roles."""
    return [
        Turn(
            index=i,
            timestamp=datetime.now(UTC),
            role="user" if i % 2 == 0 else "assistant",
            text=f"turn {i} text",
        )
        for i in range(start, start + count)
    ]


# ---------------------------------------------------------------------------
# Segmentation (the beat-aligned happy path)
# ---------------------------------------------------------------------------


def test_segments_at_the_boundaries_the_model_proposes():
    turns = _turns(30)
    client = _FakeClient([{"boundaries": [10, 20]}])

    chunks = segment_session(turns=turns, llm_client=client, model="m")

    assert chunks == [Chunk(0, 9), Chunk(10, 19), Chunk(20, 29)]
    assert len(client.messages.calls) == 1


def _topic_shift_transcript() -> list[Turn]:
    """A replayed session with three unmistakable beats.

    Beat 1 (@0-@7) chases a token-refresh bug, beat 2 (@8-@15) switches to
    the deploy docs, beat 3 (@16-@23) to a flaky CI job. The shift turns are
    @8 and @16 — the two the segmenter should propose.
    """
    beats = [
        [
            "the refresh token expires an hour early in staging",
            "reading the token issuer",
            "the clock skew allowance is subtracted twice",
            "confirming against the staging logs",
            "yes — both the issuer and the validator subtract it",
            "dropping the subtraction in the validator",
            "tests pass",
            "committed as 4f2ab1c",
        ],
        [
            "different thing now: the deploy doc is out of date",
            "reading docs/deploy.md",
            "it still names the old bastion host",
            "and the secret is described as env-only",
            "rewriting both sections",
            "also adding the trust-boundary note we discussed",
            "docs read cleanly now",
            "pushed to the docs branch",
        ],
        [
            "last thing — the nightly CI job is flaky",
            "pulling the last ten runs",
            "it only fails when the fixture DB is seeded in parallel",
            "so it is a real race, not infrastructure",
            "serialising the seed step",
            "ten green runs in a row",
            "opened PR #61 with the fix",
            "that closes it",
        ],
    ]
    flat = [line for beat in beats for line in beat]
    return [
        Turn(
            index=i,
            timestamp=datetime.now(UTC),
            role="user" if i % 2 == 0 else "assistant",
            text=line,
        )
        for i, line in enumerate(flat)
    ]


def test_beat_aligned_bounds_for_a_topic_shifting_transcript():
    turns = _topic_shift_transcript()
    client = _FakeClient([{"boundaries": [8, 16]}])

    chunks = segment_session(turns=turns, llm_client=client, model="m")

    assert chunks == [Chunk(0, 7), Chunk(8, 15), Chunk(16, 23)]
    # The shift turns are visible to the model — it can only propose what it sees.
    prompt = _prompt_text(client.messages.calls[0])
    assert "[user@8] different thing now: the deploy doc is out of date" in prompt
    assert "[user@16] last thing — the nightly CI job is flaky" in prompt


# ---------------------------------------------------------------------------
# Boundary lint (deterministic, no LLM)
# ---------------------------------------------------------------------------


def test_boundary_lint_accepts_in_range_monotone_indices():
    assert boundary_lint([10, 20], indices=list(range(30))) == ""


def test_boundary_lint_rejects_non_monotone_indices():
    assert "increasing" in boundary_lint([20, 10], indices=list(range(30)))


def test_boundary_lint_rejects_repeated_indices():
    assert "increasing" in boundary_lint([10, 10], indices=list(range(30)))


def test_boundary_lint_rejects_out_of_range_indices():
    assert "range" in boundary_lint([10, 99], indices=list(range(30)))


def test_boundary_lint_rejects_the_first_turn_as_a_boundary():
    # The first turn always opens chunk 1; listing it would cut an empty chunk.
    assert boundary_lint([0, 10], indices=list(range(30))) != ""


def test_dirty_boundaries_earn_one_corrective_retry():
    turns = _turns(30)
    client = _FakeClient([{"boundaries": [20, 10]}, {"boundaries": [12, 24]}])

    chunks = segment_session(turns=turns, llm_client=client, model="m")

    assert chunks == [Chunk(0, 11), Chunk(12, 23), Chunk(24, 29)]
    assert len(client.messages.calls) == 2
    retry_prompt = _prompt_text(client.messages.calls[1])
    assert "increasing" in retry_prompt


# ---------------------------------------------------------------------------
# Fallback: the pipeline never blocks on segmentation quality
# ---------------------------------------------------------------------------


def test_two_dirty_attempts_fall_back_to_fixed_windows():
    turns = _turns(70)
    client = _FakeClient([{"boundaries": [99]}, {"boundaries": [40, 5]}])

    chunks = segment_session(turns=turns, llm_client=client, model="m")

    assert len(client.messages.calls) == SEGMENT_MAX_ATTEMPTS
    assert chunks == [Chunk(0, 29), Chunk(30, 59), Chunk(60, 69)]
    assert FALLBACK_WINDOW_TURNS == 30


def test_model_failure_yields_fixed_windows_and_never_raises():
    turns = _turns(70)
    client = _FakeClient([None, None])

    chunks = segment_session(turns=turns, llm_client=client, model="m")

    assert chunks == [Chunk(0, 29), Chunk(30, 59), Chunk(60, 69)]


def test_malformed_payload_yields_fixed_windows():
    # No boundaries key at all, then prose where indices belong.
    turns = _turns(70)
    client = _FakeClient([{"chunks": "one, two"}, {"boundaries": ["ten", "twenty"]}])

    chunks = segment_session(turns=turns, llm_client=client, model="m")

    assert chunks == [Chunk(0, 29), Chunk(30, 59), Chunk(60, 69)]


def test_a_short_session_is_one_chunk():
    turns = _turns(4)
    client = _FakeClient([None])

    assert segment_session(turns=turns, llm_client=client, model="m") == [Chunk(0, 3)]


def test_no_turns_yields_no_chunks_and_no_llm_call():
    client = _FakeClient([])

    assert segment_session(turns=[], llm_client=client, model="m") == []
    assert client.messages.calls == []


# ---------------------------------------------------------------------------
# Size band (deterministic, no LLM)
# ---------------------------------------------------------------------------


def _sizes(chunks: list[Chunk]) -> list[int]:
    return [c.to_turn - c.from_turn + 1 for c in chunks]


def test_undersized_chunk_merges_into_its_predecessor():
    indices = list(range(30))
    chunks = [Chunk(0, 9), Chunk(10, 11), Chunk(12, 29)]  # middle chunk: 2 turns

    assert normalize_chunks(chunks, indices=indices) == [Chunk(0, 11), Chunk(12, 29)]


def test_undersized_leading_chunk_merges_into_its_successor():
    indices = list(range(30))
    chunks = [Chunk(0, 1), Chunk(2, 15), Chunk(16, 29)]

    assert normalize_chunks(chunks, indices=indices) == [Chunk(0, 15), Chunk(16, 29)]


def test_a_lone_undersized_chunk_survives():
    # A four-turn session is still a session; there is nothing to merge into.
    assert normalize_chunks([Chunk(0, 3)], indices=list(range(4))) == [Chunk(0, 3)]


def test_oversized_chunk_splits_into_even_parts():
    indices = list(range(100))

    chunks = normalize_chunks([Chunk(0, 99)], indices=indices)

    # 100 turns / 40-turn ceiling -> three parts, as even as they divide.
    assert _sizes(chunks) == [34, 33, 33]
    assert chunks == [Chunk(0, 33), Chunk(34, 66), Chunk(67, 99)]
    assert all(size <= CHUNK_MAX_TURNS for size in _sizes(chunks))


def test_normalization_is_deterministic():
    indices = list(range(100))
    dirty = [Chunk(0, 2), Chunk(3, 88), Chunk(89, 99)]

    first = normalize_chunks(dirty, indices=indices)
    second = normalize_chunks(dirty, indices=indices)

    assert first == second
    assert all(CHUNK_MIN_TURNS <= size <= CHUNK_MAX_TURNS for size in _sizes(first))


def test_the_band_is_enforced_on_model_boundaries():
    turns = _turns(60)
    # Model proposes a 2-turn beat and a 56-turn monster.
    client = _FakeClient([{"boundaries": [2, 4]}])

    chunks = segment_session(turns=turns, llm_client=client, model="m")

    assert chunks[0].from_turn == 0
    assert chunks[-1].to_turn == 59
    assert all(size <= CHUNK_MAX_TURNS for size in _sizes(chunks))
    assert all(size >= CHUNK_MIN_TURNS for size in _sizes(chunks))


# ---------------------------------------------------------------------------
# Collapsed view (what the model actually reads)
# ---------------------------------------------------------------------------


def _turn(index: int, **kw: Any) -> Turn:
    kw.setdefault("role", "assistant")
    return Turn(index=index, timestamp=datetime.now(UTC), **kw)


def test_collapsed_view_drops_thinking():
    turns = [
        _turn(0, role="user", text="segment this"),
        _turn(1, reasoning="the user probably wants X, but maybe Y, let me weigh…"),
        _turn(2, text="done"),
    ]

    view = collapsed_view(turns)

    assert "weigh" not in view
    assert "@1" not in view
    assert "[user@0] segment this" in view
    assert "[assistant@2] done" in view


def test_collapsed_view_folds_tool_results_to_line_counts():
    turns = [
        _turn(0, role="user", text="run the tests"),
        _turn(
            1,
            role="tool_result",
            tool_result=ToolResult(
                tool_call_id="t1", output="\n".join(f"line {i}" for i in range(12))
            ),
        ),
    ]

    view = collapsed_view(turns)

    assert "12 lines" in view
    assert "line 7" not in view


def test_collapsed_view_names_tool_calls_without_their_payload():
    turns = [
        _turn(
            0,
            tool_call=ToolCall(name="Write", input={"content": "SECRET PAYLOAD BODY"}, id="t1"),
        )
    ]

    view = collapsed_view(turns)

    assert "Write" in view
    assert "SECRET PAYLOAD BODY" not in view


def test_collapsed_view_truncates_a_long_pasted_turn():
    turns = [_turn(0, role="user", text="x" * 5000)]

    view = collapsed_view(turns)

    assert len(view) < 1000


def test_the_prompt_carries_the_collapsed_view_not_the_raw_turns():
    turns = [
        _turn(0, role="user", text="start"),
        _turn(1, reasoning="private deliberation"),
        *_turns(10, start=2),
    ]
    client = _FakeClient([{"boundaries": []}])

    segment_session(turns=turns, llm_client=client, model="m")

    prompt = _prompt_text(client.messages.calls[0])
    assert "private deliberation" not in prompt
    assert "[user@0] start" in prompt


# ---------------------------------------------------------------------------
# The output surface is integers, and nothing else
# ---------------------------------------------------------------------------


def test_tool_schema_accepts_integers_only():
    schema = segment_tool_schema()
    props = schema["input_schema"]["properties"]

    assert list(props) == ["boundaries"]
    assert props["boundaries"]["items"] == {"type": "integer"}
    assert schema["input_schema"]["additionalProperties"] is False


def test_prose_alongside_the_indices_is_never_consumed():
    turns = _turns(30)
    client = _FakeClient(
        [
            {
                "boundaries": [10, 20],
                "summary": "The session refactored the widget and shipped it.",
                "titles": ["Refactor", "Ship"],
            }
        ]
    )

    chunks = segment_session(turns=turns, llm_client=client, model="m")

    assert chunks == [Chunk(0, 9), Chunk(10, 19), Chunk(20, 29)]
    # Chunks carry turn indices — there is nowhere for the prose to go.
    assert all(isinstance(v, int) for c in chunks for v in (c.from_turn, c.to_turn))


# ---------------------------------------------------------------------------
# Windowed segmentation (a collapsed view too big for one call)
# ---------------------------------------------------------------------------


def _fat_turns(count: int) -> list[Turn]:
    """Turns whose collapsed view overflows one segmentation call."""
    return [
        Turn(
            index=i,
            timestamp=datetime.now(UTC),
            role="user" if i % 2 == 0 else "assistant",
            text=f"turn {i} " + "padding " * 200,
        )
        for i in range(count)
    ]


def _covers(chunks: list[Chunk], indices: list[int]) -> bool:
    """Chunks tile the slice exactly: no gap, no overlap, no reorder."""
    if not chunks:
        return not indices
    if chunks[0].from_turn != indices[0] or chunks[-1].to_turn != indices[-1]:
        return False
    return all(b.from_turn == a.to_turn + 1 for a, b in zip(chunks, chunks[1:], strict=False))


def test_an_oversized_view_is_segmented_in_windows_and_stitched():
    turns = _fat_turns(120)
    assert len(collapsed_view(turns)) > SEGMENT_MAX_VIEW_CHARS

    # One payload per window; boundaries are relative to nothing — they are
    # absolute turn indices, and each window only ever sees its own.
    client = _FakeClient([{"boundaries": [30]}, {"boundaries": [90]}, {"boundaries": []}])

    chunks = segment_session(turns=turns, llm_client=client, model="m")

    assert len(client.messages.calls) > 1
    assert _covers(chunks, [t.index for t in turns])
    assert all(size <= CHUNK_MAX_TURNS for size in _sizes(chunks))
    # The model's beats survive stitching: 30 and 90 still open chunks.
    starts = {c.from_turn for c in chunks}
    assert {30, 90} <= starts


def test_each_window_only_sees_its_own_turns():
    turns = _fat_turns(120)
    client = _FakeClient([{"boundaries": []}, {"boundaries": []}, {"boundaries": []}])

    segment_session(turns=turns, llm_client=client, model="m")

    prompts = [_prompt_text(c) for c in client.messages.calls]
    assert "[user@0]" in prompts[0]
    assert "[user@0]" not in prompts[1]
    assert all(len(p) < SEGMENT_MAX_VIEW_CHARS * 2 for p in prompts)


def test_the_fallback_reports_itself():
    # A silent degrade is an invisible quality regression: the close path's
    # logger must see that these chunks are windows, not beats.
    class _Logger:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict[str, Any]]] = []

        def emit(self, event: str, **fields: Any) -> None:
            self.events.append((event, fields))

    logger = _Logger()
    client = _FakeClient([None, None])

    segment_session(turns=_turns(70), llm_client=client, model="m", logger=logger)

    assert any(event == "warning" for event, _ in logger.events)
    assert any(f.get("call") == "segment-session" for _, f in logger.events)


def test_a_failed_window_falls_back_without_losing_the_others():
    turns = _fat_turns(120)
    # First window: two failed attempts. Later windows answer cleanly.
    client = _FakeClient([None, None, {"boundaries": [90]}, {"boundaries": []}])

    chunks = segment_session(turns=turns, llm_client=client, model="m")

    assert _covers(chunks, [t.index for t in turns])
    assert 90 in {c.from_turn for c in chunks}
