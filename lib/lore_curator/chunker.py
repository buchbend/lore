"""Beat-aligned session segmentation — the model emits indices, nothing else.

One cheap LLM call reads a *collapsed* view of the replayed session
(thinking dropped, tool calls named, tool results folded to line counts)
and proposes the turn indices at which a new logical chunk begins.
Deterministic code does the rest.

The model's entire output surface is a list of integers. It makes no
claims, so its errors degrade to suboptimal windows — never to false
facts. Nothing else it might emit is parsed or passed on.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lore_core.types import Turn

if TYPE_CHECKING:
    from lore_core.run_log import RunLogger

__all__ = [
    "Chunk",
    "boundary_lint",
    "collapsed_view",
    "normalize_chunks",
    "segment_session",
    "segment_tool_schema",
    "SEGMENT_MAX_ATTEMPTS",
    "SEGMENT_MAX_VIEW_CHARS",
    "FALLBACK_WINDOW_TURNS",
    "CHUNK_MIN_TURNS",
    "CHUNK_MAX_TURNS",
]

_TOOL_NAME = "segment_session"

# Two attempts, exactly as the chapter composer: one corrective retry on a
# deterministic lint miss, then the caller degrades instead of looping.
SEGMENT_MAX_ATTEMPTS = 2
SEGMENT_MAX_OUTPUT_TOKENS = 1000

# Collapsed-view budget for ONE segmentation call. A longer session is
# segmented in windows and the boundary lists are stitched. Deliberately far
# under the model's context: a local backend can silently return nothing on
# an oversized prompt, which would look exactly like a segmentation failure.
SEGMENT_MAX_VIEW_CHARS = 24_000

# Size band, in turns. A chunk is one extraction call downstream, so the
# band is what keeps that call worth its cost (a two-turn "beat" is not) and
# inside a small model's context (a 90-turn one is not). MAX >= 2*MIN, so an
# even split of an oversized chunk can never land back under MIN.
CHUNK_MIN_TURNS = 6
CHUNK_MAX_TURNS = 40

# What segmentation degrades to when the model fails or keeps returning
# unusable indices: plain fixed-size windows. Suboptimal beats, never a
# blocked pipeline. Sized inside the band so the normalizer leaves them be.
FALLBACK_WINDOW_TURNS = 30

_MALFORMED_FEEDBACK = (
    "The previous response carried no `boundaries` array. Call the tool with "
    "`boundaries` set to the turn indices where a new chunk begins — integers "
    "only, no text."
)


@dataclass(frozen=True)
class Chunk:
    """One logical chunk: an inclusive span of turn indices."""

    from_turn: int
    to_turn: int


# ---------------------------------------------------------------------------
# Boundary lint (deterministic)
# ---------------------------------------------------------------------------


def boundary_lint(boundaries: Sequence[Any], *, indices: list[int]) -> str:
    """Return corrective feedback for unusable boundaries, ``""`` when clean.

    A boundary is the index of the turn that *opens* a new chunk. It must be
    an integer turn index from the slice, must not be the first one (that
    turn always opens chunk 1, and listing it would cut an empty leading
    chunk), and the list must be strictly increasing. Coverage needs no lint:
    chunks are cut from the slice itself, so every turn always lands in
    exactly one chunk.

    An empty list is clean — it says the session is a single beat.
    """
    lo, hi = indices[0], indices[-1]
    valid = set(indices[1:])
    bad = [b for b in boundaries if not _is_index(b) or b not in valid]
    if bad:
        shown = ", ".join(str(b) for b in bad)
        return (
            f"Boundaries must be turn indices shown in the transcript, in the "
            f"range {lo + 1}-{hi} (the first turn @{lo} always opens the first "
            f"chunk and is never listed). These were not: {shown}."
        )
    if any(b <= a for a, b in zip(boundaries, boundaries[1:], strict=False)):
        shown = ", ".join(str(b) for b in boundaries)
        return (
            f"Boundaries must be strictly increasing — each one opens the next "
            f"chunk in order. The previous attempt returned: {shown}."
        )
    return ""


def _is_index(value: Any) -> bool:
    # bool is an int subclass — never a turn index.
    return isinstance(value, int) and not isinstance(value, bool)


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------


def segment_session(
    *,
    turns: list[Turn],
    llm_client: Any,
    model: str,
    logger: RunLogger | None = None,
    transcript_id: str = "",
) -> list[Chunk]:
    """Segment the replayed turns into beat-aligned chunks.

    A session whose collapsed view exceeds one call's budget is segmented in
    windows; the per-window chunks are concatenated (a window seam is itself
    a boundary — no chunk spans two windows) and the size band then runs over
    the whole stitched list, so a stub chunk at a seam merges away.

    Never raises and never returns nothing for a non-empty session: a model
    failure or unusable output degrades that window to fixed-size windows.
    """
    if not turns:
        return []
    stitched: list[Chunk] = []
    for window in _view_windows(turns):
        w_indices = [t.index for t in window]
        boundaries = _propose_boundaries(turns=window, llm_client=llm_client, model=model)
        if boundaries is None:
            if logger is not None:
                logger.emit(
                    "warning",
                    call="segment-session",
                    transcript_id=transcript_id,
                    message=(
                        f"no usable boundaries after {SEGMENT_MAX_ATTEMPTS} attempts; "
                        f"turns {w_indices[0]}-{w_indices[-1]} fall back to "
                        f"{FALLBACK_WINDOW_TURNS}-turn windows"
                    ),
                )
            stitched.extend(_fixed_windows(w_indices))
        else:
            stitched.extend(_chunks_from_boundaries(w_indices, boundaries))
    return normalize_chunks(stitched, indices=[t.index for t in turns])


def _view_windows(turns: list[Turn]) -> list[list[Turn]]:
    """Cut the turns into windows whose collapsed views fit one call.

    Greedy and deterministic. A single turn larger than the budget still gets
    its own window rather than being dropped — the per-turn truncation in the
    collapsed view already bounds how bad that can be.
    """
    windows: list[list[Turn]] = []
    current: list[Turn] = []
    size = 0
    for t in turns:
        cost = len(_collapse_turn(t)) + len(f"[{t.role}@{t.index}] \n")
        if current and size + cost > SEGMENT_MAX_VIEW_CHARS:
            windows.append(current)
            current, size = [], 0
        current.append(t)
        size += cost
    if current:
        windows.append(current)
    return windows


def _propose_boundaries(*, turns: list[Turn], llm_client: Any, model: str) -> list[int] | None:
    """Model-proposed boundaries, linted. ``None`` means "use the fallback".

    An empty *clean* list is a real answer — the session is one beat.
    """
    indices = [t.index for t in turns]
    feedback = ""
    for _ in range(SEGMENT_MAX_ATTEMPTS):
        prompt = _build_prompt(turns, retry_feedback=feedback)
        try:
            resp = llm_client.messages.create(
                model=model,
                max_tokens=SEGMENT_MAX_OUTPUT_TOKENS,
                tools=[segment_tool_schema()],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception:  # noqa: BLE001 - segmentation never blocks the pipeline
            continue
        raw = _extract_boundaries(resp)
        if raw is None:
            feedback = _MALFORMED_FEEDBACK
            continue
        feedback = boundary_lint(raw, indices=indices)
        if not feedback:
            return [int(b) for b in raw]
    return None


def normalize_chunks(
    chunks: list[Chunk],
    *,
    indices: list[int],
    min_turns: int = CHUNK_MIN_TURNS,
    max_turns: int = CHUNK_MAX_TURNS,
) -> list[Chunk]:
    """Force ``chunks`` into the size band. Pure, deterministic, total.

    Merging runs before splitting: a merge can push a chunk over the ceiling,
    while an even split can never drop a part under the floor, so this order
    needs one pass each. A single undersized chunk (a short session) has no
    neighbour to merge into and survives as it is.
    """
    positions = {idx: pos for pos, idx in enumerate(indices)}
    spans = [(positions[c.from_turn], positions[c.to_turn] + 1) for c in chunks]

    merged: list[tuple[int, int]] = []
    for lo, hi in spans:
        if merged and hi - lo < min_turns:
            merged[-1] = (merged[-1][0], hi)
        else:
            merged.append((lo, hi))
    # An undersized leading chunk has no predecessor: fold it forward instead.
    if len(merged) > 1 and merged[0][1] - merged[0][0] < min_turns:
        merged[1] = (merged[0][0], merged[1][1])
        merged.pop(0)

    out: list[Chunk] = []
    for lo, hi in merged:
        for split_lo, split_hi in _even_split(lo, hi, max_turns):
            out.append(Chunk(indices[split_lo], indices[split_hi - 1]))
    return out


def _even_split(lo: int, hi: int, max_turns: int) -> list[tuple[int, int]]:
    """Cut ``[lo, hi)`` into the fewest parts that fit ``max_turns``, evenly.

    Even parts rather than max-size ones plus a remainder: the remainder is
    the shape that produces a stub last chunk (41 turns -> 40 + 1).
    """
    size = hi - lo
    if size <= max_turns:
        return [(lo, hi)]
    parts = -(-size // max_turns)  # ceil
    base, extra = divmod(size, parts)
    out: list[tuple[int, int]] = []
    start = lo
    for i in range(parts):
        end = start + base + (1 if i < extra else 0)
        out.append((start, end))
        start = end
    return out


def _fixed_windows(indices: list[int]) -> list[Chunk]:
    """The fallback: cut the slice into fixed-size windows of turns."""
    return [
        Chunk(window[0], window[-1])
        for window in (
            indices[i : i + FALLBACK_WINDOW_TURNS]
            for i in range(0, len(indices), FALLBACK_WINDOW_TURNS)
        )
    ]


def _chunks_from_boundaries(indices: list[int], boundaries: list[int]) -> list[Chunk]:
    """Cut ``indices`` at every boundary; full coverage is structural."""
    cuts = [0] + [indices.index(b) for b in boundaries] + [len(indices)]
    return [
        Chunk(indices[lo], indices[hi - 1])
        for lo, hi in zip(cuts, cuts[1:], strict=False)
        if hi > lo
    ]


def _extract_boundaries(resp: Any) -> list[Any] | None:
    """The `boundaries` array as the model sent it; ``None`` when malformed.

    Values are NOT coerced or filtered here — the lint judges them, so a
    model that answers with words instead of indices earns its corrective
    retry instead of having its output quietly reinterpreted. Every other
    key in the payload is ignored: indices are the only output surface.
    """
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "tool_use":
            inp = getattr(block, "input", None)
            if isinstance(inp, dict) and isinstance(inp.get("boundaries"), list):
                return list(inp["boundaries"])
    return None


# ---------------------------------------------------------------------------
# Collapsed view
# ---------------------------------------------------------------------------

# Enough of a turn to recognise the beat it belongs to, not enough for one
# pasted file to dominate the view (or the segmentation model's context).
_TURN_MAX_CHARS = 400


def collapsed_view(turns: list[Turn]) -> str:
    """The cheap, low-token rendering of a session the segmenter reads.

    Thinking is dropped (deliberation is not a beat of the work), tool calls
    are named without their payload (a Write's `content` is the file, not a
    topic signal), and tool results are folded to a line count. What remains
    is the conversational spine — which is where topic shifts actually show.
    Turn indices are preserved verbatim: they are the only thing the model
    gives back.
    """
    lines: list[str] = []
    for t in turns:
        rendered = _collapse_turn(t)
        if rendered:
            lines.append(f"[{t.role}@{t.index}] {rendered}")
    return "\n".join(lines)


def _collapse_turn(turn: Turn) -> str:
    if turn.tool_call is not None:
        return f"<tool: {turn.tool_call.name}>"
    if turn.tool_result is not None:
        n = len((turn.tool_result.output or "").splitlines())
        flag = "error, " if turn.tool_result.is_error else ""
        return f"<result: {flag}{n} line{'' if n == 1 else 's'}>"
    text = (turn.text or "").strip()
    if not text:
        return ""  # thinking-only turns land here and are dropped
    if len(text) > _TURN_MAX_CHARS:
        dropped = len(text[_TURN_MAX_CHARS:].splitlines())
        text = text[:_TURN_MAX_CHARS].rstrip() + f"… (+{dropped} more lines)"
    return text


# ---------------------------------------------------------------------------
# Prompt + tool schema
# ---------------------------------------------------------------------------


def _build_prompt(turns: list[Turn], *, retry_feedback: str = "") -> str:
    parts: list[str] = []
    if retry_feedback:
        parts.extend(
            [
                "The previous attempt was rejected.",
                retry_feedback,
                "Return the boundaries again, corrected.",
                "",
            ]
        )
    first, last = turns[0].index, turns[-1].index
    parts.extend(
        [
            "Split this work session into its logical chunks — the beats of "
            "the work itself. A new chunk begins where the session turns to a "
            "different problem, artefact, or goal: a new task the user asks "
            "for, a pivot after something failed, a move from one file or "
            "system to another. Continuing the same problem — debugging it, "
            "reviewing it, discussing it — is the SAME chunk.",
            "",
            "Return ONLY the turn indices at which a new chunk begins, "
            "strictly increasing. Nothing else: no titles, no summaries, no "
            "labels. Do not list the first turn — it always opens the first "
            f"chunk. Every index must be between {first + 1} and {last}. If "
            "the whole session is one continuous beat, return an empty list.",
            "",
            "SESSION (each line is [role@turn]; thinking is omitted, tool "
            "results are folded to line counts):",
            "<<<SESSION BEGIN>>>",
            collapsed_view(turns),
            "<<<SESSION END>>>",
            "",
            f"Call the `{_TOOL_NAME}` tool exactly once.",
        ]
    )
    return "\n".join(parts)


def segment_tool_schema() -> dict[str, Any]:
    """Tool schema for the segmentation call — integers, nothing else."""
    return {
        "name": _TOOL_NAME,
        "description": (
            "Emit the turn indices at which a new logical chunk of the "
            "session begins. Indices only — no text of any kind."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "boundaries": {
                    "type": "array",
                    "description": (
                        "Turn indices where a new chunk starts, strictly "
                        "increasing. The first turn always starts chunk 1 "
                        "and must not be listed."
                    ),
                    "items": {"type": "integer"},
                }
            },
            "additionalProperties": False,
            "required": ["boundaries"],
        },
    }
