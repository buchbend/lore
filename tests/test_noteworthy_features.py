"""Tests for lore_core.noteworthy_features — deterministic signals
extracted from a transcript slice before any LLM call.

The feature extractor operates on :data:`ToolCall.category` (host-agnostic),
not on tool names, so the same signals apply across Claude Code, Cursor,
Copilot, and any future host.

Two surfaces are covered:
- :func:`compute_features` — pure accumulation over a Turn list
- :func:`classify_cascade` — hard rules + score → trivial / uncertain / substantive
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lore_core.types import ToolCall, ToolResult, Turn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_NOW = datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)


def _t(index: int, role: str, *, text=None, tool_call=None, tool_result=None,
       minute_offset: int = 0) -> Turn:
    return Turn(
        index=index,
        timestamp=_NOW + timedelta(minutes=minute_offset),
        role=role,
        text=text,
        tool_call=tool_call,
        tool_result=tool_result,
    )


def _tc(name: str, category: str, file_path: str = "/x",
        new_string: str = "") -> ToolCall:
    inp = {"file_path": file_path}
    if new_string:
        inp["new_string"] = new_string
    return ToolCall(name=name, input=inp, id="tc", category=category)


# ---------------------------------------------------------------------------
# compute_features — signal accumulation
# ---------------------------------------------------------------------------


def test_compute_features_on_empty_slice_is_all_zeros():
    from lore_core.noteworthy_features import compute_features

    f = compute_features([])
    assert f.total_turns == 0
    assert f.file_edit_count == 0
    assert f.distinct_files_edited == 0
    assert f.elapsed_seconds == 0.0


def test_compute_features_counts_categorised_tool_calls():
    from lore_core.noteworthy_features import compute_features

    turns = [
        _t(0, "user", text="Please refactor"),
        _t(1, "assistant", tool_call=_tc("Read", "file_read", "/a.py")),
        _t(2, "assistant", tool_call=_tc("Edit", "file_edit", "/a.py", "body")),
        _t(3, "assistant", tool_call=_tc("Edit", "file_edit", "/b.py", "body")),
        _t(4, "assistant", tool_call=_tc("Grep", "search")),
        _t(5, "assistant", tool_call=_tc("Bash", "shell_exec")),
        _t(6, "assistant", tool_call=_tc("Task", "agent_spawn")),
        _t(7, "assistant", tool_call=_tc("ExitPlanMode", "plan_exit")),
        _t(8, "assistant", text="Done."),
    ]
    f = compute_features(turns)

    assert f.total_turns == 9
    assert f.user_text_turns == 1
    assert f.assistant_text_turns == 1
    assert f.file_edit_count == 2
    assert f.file_read_count == 1
    assert f.search_count == 1
    assert f.shell_exec_count == 1
    assert f.agent_spawn_count == 1
    assert f.plan_exit_count == 1
    assert f.distinct_files_edited == 2   # /a.py + /b.py
    assert f.distinct_files_read == 1     # /a.py
    assert f.tool_call_total == 7


def test_compute_features_unknown_category_is_neutral():
    """Turns with category='other' (unknown tool) must not contribute
    to any specific counter — they're neutral signal."""
    from lore_core.noteworthy_features import compute_features

    turns = [
        _t(0, "assistant", tool_call=_tc("FutureTool", "other")),
    ]
    f = compute_features(turns)

    assert f.tool_call_total == 1
    assert f.file_edit_count == 0
    assert f.file_read_count == 0
    assert f.search_count == 0


def test_compute_features_elapsed_seconds_from_timestamps():
    from lore_core.noteworthy_features import compute_features

    turns = [
        _t(0, "user", text="start", minute_offset=0),
        _t(1, "assistant", text="end", minute_offset=45),
    ]
    f = compute_features(turns)

    assert f.elapsed_seconds == pytest.approx(45 * 60)


def test_compute_features_new_string_chars_summed():
    from lore_core.noteworthy_features import compute_features

    turns = [
        _t(0, "assistant", tool_call=_tc("Edit", "file_edit", "/a.py", "x" * 100)),
        _t(1, "assistant", tool_call=_tc("Edit", "file_edit", "/a.py", "y" * 200)),
    ]
    f = compute_features(turns)

    assert f.total_edit_new_string_chars == 300


def test_compute_features_max_assistant_text_tracked():
    from lore_core.noteworthy_features import compute_features

    turns = [
        _t(0, "assistant", text="short"),
        _t(1, "assistant", text="x" * 500),
        _t(2, "assistant", text="medium-ish text"),
    ]
    f = compute_features(turns)

    assert f.max_assistant_text_chars == 500


# ---------------------------------------------------------------------------
# classify_cascade — hard rules
# ---------------------------------------------------------------------------


def test_cascade_plan_exit_is_always_substantive():
    """ExitPlanMode means a plan was formed and approved — no LLM verdict
    needed to know this slice is noteworthy."""
    from lore_core.noteworthy_features import classify_cascade

    turns = [
        _t(0, "user", text="Plan this"),
        _t(1, "assistant", tool_call=_tc("ExitPlanMode", "plan_exit")),
    ]
    v = classify_cascade(turns)
    assert v.label == "substantive"
    assert "plan_exit" in v.reason


def test_cascade_agent_spawn_is_substantive():
    from lore_core.noteworthy_features import classify_cascade

    turns = [
        _t(0, "user", text="Delegate this"),
        _t(1, "assistant", tool_call=_tc("Task", "agent_spawn")),
    ]
    v = classify_cascade(turns)
    assert v.label == "substantive"
    assert "agent_spawn" in v.reason


def test_cascade_many_edits_is_substantive():
    from lore_core.noteworthy_features import classify_cascade

    turns = [
        _t(i, "assistant", tool_call=_tc("Edit", "file_edit", "/a.py", "body"))
        for i in range(5)
    ]
    v = classify_cascade(turns)
    assert v.label == "substantive"


def test_cascade_multiple_distinct_files_edited_is_substantive():
    """Uses exactly 2 edits on 2 files so the multi_edit rule (3+ edits)
    does NOT fire — this test specifically exercises multi_file_edit."""
    from lore_core.noteworthy_features import classify_cascade

    turns = [
        _t(0, "assistant", tool_call=_tc("Edit", "file_edit", "/a.py", "body")),
        _t(1, "assistant", tool_call=_tc("Edit", "file_edit", "/b.py", "body")),
    ]
    v = classify_cascade(turns)
    assert v.label == "substantive"
    assert v.reason == "multi_file_edit"


def test_cascade_short_slice_with_no_edits_is_trivial():
    """Classic 'what's the time' — 1-3 turns, no edits, no work signal."""
    from lore_core.noteworthy_features import classify_cascade

    turns = [
        _t(0, "user", text="ls"),
        _t(1, "assistant", tool_call=_tc("Bash", "shell_exec")),
        _t(2, "tool_result", tool_result=ToolResult("tc", "a b c")),
    ]
    v = classify_cascade(turns)
    assert v.label == "trivial"


def test_cascade_readonly_exploration_no_substantive_text_is_trivial():
    """Many reads, no edits, short assistant text — exploration that
    didn't lead anywhere."""
    from lore_core.noteworthy_features import classify_cascade

    turns = [
        _t(0, "user", text="what's in here"),
        _t(1, "assistant", tool_call=_tc("Read", "file_read", "/a.py")),
        _t(2, "assistant", tool_call=_tc("Read", "file_read", "/b.py")),
        _t(3, "assistant", tool_call=_tc("Read", "file_read", "/c.py")),
        _t(4, "assistant", text="ok"),
    ]
    v = classify_cascade(turns)
    assert v.label == "trivial"


def test_cascade_shell_heavy_session_without_edits_is_uncertain():
    """pytest → fail → investigate → … with many Bash calls and no edits
    is real debugging work, not trivial exploration. Leave to the LLM."""
    from lore_core.noteworthy_features import classify_cascade

    turns = [
        _t(0, "user", text="run the test suite and tell me what's failing"),
        _t(1, "assistant", tool_call=_tc("Bash", "shell_exec")),
        _t(2, "assistant", tool_call=_tc("Bash", "shell_exec")),
        _t(3, "assistant", tool_call=_tc("Bash", "shell_exec")),
        _t(4, "assistant", tool_call=_tc("Bash", "shell_exec")),
        _t(5, "assistant", text="Three tests fail; root cause is X."),
    ]
    v = classify_cascade(turns)
    assert v.label == "uncertain", v.reason


def test_cascade_medium_work_without_hard_signal_is_uncertain():
    """One edit, some context, no plan/agent/multi — LLM should decide."""
    from lore_core.noteworthy_features import classify_cascade

    turns = [
        _t(0, "user", text="fix the thing please, I think it's in the helper"),
        _t(1, "assistant", text="Let me look."),
        _t(2, "assistant", tool_call=_tc("Read", "file_read", "/a.py")),
        _t(3, "assistant", tool_call=_tc("Edit", "file_edit", "/a.py", "patch")),
        _t(4, "assistant", text="Fixed — the helper was using the wrong arg."),
    ]
    v = classify_cascade(turns)
    assert v.label == "uncertain"


# ---------------------------------------------------------------------------
# CascadeVerdict shape
# ---------------------------------------------------------------------------


def test_cascade_verdict_carries_features():
    """Callers want to log the feature vector next to the verdict for
    shadow-run calibration."""
    from lore_core.noteworthy_features import classify_cascade

    turns = [_t(0, "assistant", tool_call=_tc("ExitPlanMode", "plan_exit"))]
    v = classify_cascade(turns)
    assert v.features is not None
    assert v.features.plan_exit_count == 1
