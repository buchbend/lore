"""Deterministic features extracted from a transcript slice.

The feature extractor accumulates host-agnostic signals by reading
:data:`ToolCall.category` rather than ``name``, so the same rules apply
to Claude Code, Cursor, Copilot, and any future host.

:func:`classify_cascade` applies hard rules over the features to produce
one of three labels:

- ``"trivial"`` — strong negative, the LLM can be skipped entirely
- ``"substantive"`` — strong positive, LLM is still needed for a summary
  but not for the noteworthy verdict
- ``"uncertain"`` — soft signals don't decide; defer to the LLM

Hand-picked conservative defaults ship in Phase A. Phase A.5 calibrates
against a small gold-labelled set bootstrapped from the existing ledger.
Thresholds deliberately stay in the module as constants so the shadow-run
logger captures the same feature values the live cascade would have seen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from lore_core.types import Turn

CascadeLabel = Literal["trivial", "uncertain", "substantive"]


# ---------------------------------------------------------------------------
# Hand-picked thresholds (Phase A — pre-calibration)
# ---------------------------------------------------------------------------

# Below N turns AND no edits → trivial. Catches "what's the time" / "ls /" /
# one-shot questions. Four is conservative; a real debugging round trip
# will blow past it.
_TRIVIAL_TURN_MAX = 4

# At or above this many file_edit calls → substantive without LLM verdict.
# Three is the smallest number that looks like deliberate refactoring rather
# than a one-liner; tune down to 2 after calibration if false-negatives dominate.
_SUBSTANTIVE_EDIT_MIN = 3

# Or: edits spanning this many distinct files → substantive (multi-file
# change is almost always a real session).
_SUBSTANTIVE_FILES_EDITED_MIN = 2

# Below this length, assistant "substantive" text is too short to count.
# Captures "ok" / "yes" / "done" / single-sentence acknowledgments.
_TINY_ASSISTANT_TEXT_MAX = 100

# A slice with this many or more shell_exec calls is "Bash-heavy" —
# typically pytest/git/npm/make/cargo activity. Treat as uncertain
# rather than trivial; the LLM should look at the narrative. One-off
# ``ls`` or ``pwd`` at the start of a slice is still allowed to fall
# through to trivial under the _TRIVIAL_TURN_MAX rule.
_SHELL_HEAVY_MIN = 3


@dataclass(frozen=True)
class SliceFeatures:
    """Raw signal counts over a Turn slice. All fields are deterministic —
    same turns in, same features out, no network, no randomness.

    Downstream consumers log these alongside the LLM verdict so Phase A.5
    can calibrate thresholds against real decisions.
    """
    total_turns: int
    user_text_turns: int
    assistant_text_turns: int
    file_edit_count: int
    file_read_count: int
    search_count: int
    shell_exec_count: int
    agent_spawn_count: int
    plan_exit_count: int
    version_control_count: int
    other_tool_count: int
    distinct_files_edited: int
    distinct_files_read: int
    total_edit_new_string_chars: int
    max_assistant_text_chars: int
    elapsed_seconds: float
    tool_call_total: int


@dataclass(frozen=True)
class CascadeVerdict:
    """Result of the feature-based cascade.

    ``reason`` is a short code identifying which rule fired
    (e.g. ``"plan_exit"``, ``"multi_edit"``, ``"short_no_edits"``,
    ``"no_hard_rule_fired"``) — stable enough for log analysis and
    threshold calibration.
    """
    label: CascadeLabel
    reason: str
    features: SliceFeatures


def compute_features(turns: list[Turn]) -> SliceFeatures:
    """Extract counts over a Turn slice by canonical tool category.

    Empty slice → all-zero features. Turns whose ``tool_call.category`` is
    ``"other"`` contribute to ``tool_call_total`` and ``other_tool_count``
    but not to any specific-category counter.
    """
    user_text = assistant_text = 0
    edits = reads = searches = shells = agents = plans = vc = other_tools = 0
    files_edited: set[str] = set()
    files_read: set[str] = set()
    edit_chars = 0
    max_text = 0
    tool_total = 0
    first_ts = last_ts = None

    for t in turns:
        if t.timestamp is not None:
            if first_ts is None:
                first_ts = t.timestamp
            last_ts = t.timestamp

        if t.text is not None:
            if t.role == "user":
                user_text += 1
            elif t.role == "assistant":
                assistant_text += 1
                text_len = len(t.text)
                if text_len > max_text:
                    max_text = text_len

        if t.tool_call is not None:
            tool_total += 1
            category = t.tool_call.category
            inp = t.tool_call.input if isinstance(t.tool_call.input, dict) else {}
            file_path = inp.get("file_path") if isinstance(inp.get("file_path"), str) else ""

            if category == "file_edit":
                edits += 1
                if file_path:
                    files_edited.add(file_path)
                new_string = inp.get("new_string") or inp.get("content") or ""
                if isinstance(new_string, str):
                    edit_chars += len(new_string)
            elif category == "file_read":
                reads += 1
                if file_path:
                    files_read.add(file_path)
            elif category == "search":
                searches += 1
            elif category == "shell_exec":
                shells += 1
            elif category == "agent_spawn":
                agents += 1
            elif category == "plan_exit":
                plans += 1
            elif category == "version_control":
                vc += 1
            else:
                other_tools += 1

    elapsed = 0.0
    if first_ts is not None and last_ts is not None and last_ts > first_ts:
        elapsed = (last_ts - first_ts).total_seconds()

    return SliceFeatures(
        total_turns=len(turns),
        user_text_turns=user_text,
        assistant_text_turns=assistant_text,
        file_edit_count=edits,
        file_read_count=reads,
        search_count=searches,
        shell_exec_count=shells,
        agent_spawn_count=agents,
        plan_exit_count=plans,
        version_control_count=vc,
        other_tool_count=other_tools,
        distinct_files_edited=len(files_edited),
        distinct_files_read=len(files_read),
        total_edit_new_string_chars=edit_chars,
        max_assistant_text_chars=max_text,
        elapsed_seconds=elapsed,
        tool_call_total=tool_total,
    )


def classify_cascade(turns: list[Turn]) -> CascadeVerdict:
    """Classify a slice via feature-based hard rules.

    Rule order matters — the first match wins:

    1. Strong positive: plan_exit, agent_spawn, many edits, multi-file edits,
       version_control → ``"substantive"``.
    2. Strong negative: short + no edits / no work at all / no work +
       tiny text → ``"trivial"``.
    3. Otherwise → ``"uncertain"``, the LLM decides.
    """
    f = compute_features(turns)

    # ---- strong positives ----------------------------------------------
    if f.plan_exit_count > 0:
        return CascadeVerdict("substantive", "plan_exit", f)
    if f.agent_spawn_count > 0:
        return CascadeVerdict("substantive", "agent_spawn", f)
    if f.version_control_count > 0:
        return CascadeVerdict("substantive", "version_control", f)
    if f.file_edit_count >= _SUBSTANTIVE_EDIT_MIN:
        return CascadeVerdict("substantive", "multi_edit", f)
    if f.distinct_files_edited >= _SUBSTANTIVE_FILES_EDITED_MIN:
        return CascadeVerdict("substantive", "multi_file_edit", f)

    # ---- strong negatives ----------------------------------------------
    no_work = (f.file_edit_count == 0 and f.agent_spawn_count == 0
               and f.plan_exit_count == 0 and f.version_control_count == 0)
    # Shell-heavy sessions (pytest, git, npm, make…) look like "no work"
    # to the per-category counters but are usually real debugging. Defer
    # to the LLM rather than rule-out.
    shell_heavy = f.shell_exec_count >= _SHELL_HEAVY_MIN

    if no_work and f.total_turns <= _TRIVIAL_TURN_MAX:
        return CascadeVerdict("trivial", "short_no_edits", f)
    if no_work and not shell_heavy and f.assistant_text_turns == 0:
        return CascadeVerdict("trivial", "no_work_no_text", f)
    if no_work and not shell_heavy and f.max_assistant_text_chars < _TINY_ASSISTANT_TEXT_MAX:
        return CascadeVerdict("trivial", "no_work_tiny_text", f)

    # ---- middle band → LLM ---------------------------------------------
    return CascadeVerdict("uncertain", "no_hard_rule_fired", f)
