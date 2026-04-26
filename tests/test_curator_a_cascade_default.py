"""Cascade default (v0.6.0+) end-to-end coverage.

The autouse fixture in ``conftest.py`` forces ``LORE_NOTEWORTHY_MODE=
llm_only`` on every test, which masks the production default. This file
opts out so the cascade behaviour itself is exercised:

* trivial slice (≤4 turns, no edits) → cascade short-circuits the LLM.
* substantive slice (≥3 edits) → cascade trusts the verdict, but the
  LLM is still invoked for the summary.

Without this, the cascade-default codepath has only unit-level coverage
in ``test_noteworthy.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lore_core.types import Turn
from lore_curator.noteworthy import NoteworthyResult, classify_slice


# ---------------------------------------------------------------------------
# Opt out of the autouse llm_only override per the conftest migration policy.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_cascade_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wins over the autouse `_default_noteworthy_mode_llm_only` fixture
    via per-test monkeypatch precedence — pytest applies fixtures in a
    LIFO order on each test, so the locally-scoped override here lands
    after the conftest one and effectively replaces it."""
    monkeypatch.delenv("LORE_NOTEWORTHY_MODE", raising=False)


# ---------------------------------------------------------------------------
# Fake LLM client (mirrors the shape used in test_noteworthy.py)
# ---------------------------------------------------------------------------


class _ToolUseBlock:
    def __init__(self, data: dict) -> None:
        self.type = "tool_use"
        self.input = data
        self.text = None


class _Response:
    def __init__(self, data: dict) -> None:
        self.content = [_ToolUseBlock(data)]


class _Messages:
    def __init__(self, data: dict) -> None:
        self._data = data
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Response(self._data)


class _FakeClient:
    def __init__(self, data: dict) -> None:
        self.messages = _Messages(data)


def _resolver(tier: str) -> str:
    return {"middle": "claude-sonnet-4-6", "simple": "claude-haiku-4-5"}[tier]


def _trivial_turns() -> list[Turn]:
    """≤4 turns, no edits → cascade should label this trivial."""
    return [
        Turn(index=0, timestamp=None, role="user", text="hello"),
        Turn(index=1, timestamp=None, role="assistant", text="hi"),
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cascade_default_trivial_slice_skips_llm() -> None:
    """Cascade should short-circuit on a clearly-trivial slice — the
    fake LLM client must not see a single .messages.create() call."""
    client = _FakeClient({})  # data unused — should never be requested

    result = classify_slice(
        _trivial_turns(),
        model_resolver=_resolver,
        llm_client=client,
    )

    assert isinstance(result, NoteworthyResult)
    assert result.noteworthy is False
    assert result.reason.startswith("cascade_trivial:")
    assert client.messages.calls == [], (
        "cascade default should not invoke the LLM on a trivial slice"
    )


def test_cascade_default_resolves_to_cascade_when_env_unset() -> None:
    """Belt-and-braces: confirm the resolver itself reports cascade."""
    from lore_curator.noteworthy import _resolve_mode

    assert _resolve_mode() == "cascade"


def test_cascade_substantive_slice_still_calls_llm_for_summary() -> None:
    """Even when the cascade *would* label a slice substantive, the LLM
    is still invoked to produce the title/summary/bullets — only the
    *trivial* short-circuit skips the LLM entirely."""
    from lore_core.types import ToolCall

    # Each Turn carries at most one ToolCall, so we use one assistant
    # turn per edit. ≥3 edits across ≥2 distinct files trips the
    # substantive label per _SUBSTANTIVE_EDIT_MIN = 3 /
    # _SUBSTANTIVE_FILES_EDITED_MIN = 2 in noteworthy_features.
    def _edit_turn(idx: int, path: str) -> Turn:
        return Turn(
            index=idx, timestamp=None, role="assistant",
            text=f"editing {path}",
            tool_call=ToolCall(
                name="Edit", input={"file_path": path}, category="file_edit"
            ),
        )

    turns = [
        Turn(index=0, timestamp=None, role="user", text="refactor please"),
        _edit_turn(1, "a.py"),
        _edit_turn(2, "b.py"),
        _edit_turn(3, "c.py"),
        Turn(index=4, timestamp=None, role="assistant", text="done"),
    ]

    client = _FakeClient({
        "noteworthy": True,
        "reason": "refactor across files",
        "title": "Three edits",
        "summary": "Made three coordinated changes.",
    })

    out = classify_slice(turns, model_resolver=_resolver, llm_client=client)

    assert out.noteworthy is True
    assert out.title == "Three edits"
    assert len(client.messages.calls) == 1, (
        "substantive cascade verdict still requires one LLM call for the summary"
    )
