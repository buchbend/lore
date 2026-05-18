"""Smoke tests for the P2 (outline → narrative) Curator A pipeline.

Targets the new two-call ``compose_session_note`` and the supporting
helpers added on ``pr/p2-style``:

* ``_p2_outline_tool_schema`` / ``_p2_compose_tool_schema``
* ``_p2_outline_prompt`` / ``_p2_compose_prompt``
* ``compose_session_note`` (two-call: outline → compose; returns
  ``{title, summary_lede, narrative, outline_items}``)

Replaces the pre-P2 coverage in ``tests/test_synthesis.py`` and
``tests/test_synthesis_narrative.py`` (skipped at the module level
on this branch).
"""
from __future__ import annotations

from typing import Any

import pytest

from lore_curator.synthesis import (
    OUTLINE_MAX_ITEMS,
    OUTLINE_MIN_ITEMS,
    SUMMARY_LEDE_MAX,
    _p2_compose_prompt,
    _p2_compose_tool_schema,
    _p2_outline_prompt,
    _p2_outline_tool_schema,
    compose_session_note,
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


def test_outline_schema_has_only_items_property():
    schema = _p2_outline_tool_schema()
    assert schema["name"] == "outline"
    props = schema["input_schema"]["properties"]
    assert list(props.keys()) == ["items"]
    items_prop = props["items"]
    assert items_prop["minItems"] == OUTLINE_MIN_ITEMS
    assert items_prop["maxItems"] == OUTLINE_MAX_ITEMS
    assert schema["input_schema"]["required"] == ["items"]
    assert schema["input_schema"]["additionalProperties"] is False


def test_compose_schema_has_only_three_top_level_fields():
    schema = _p2_compose_tool_schema()
    assert schema["name"] == "compose"
    props = schema["input_schema"]["properties"]
    assert set(props.keys()) == {"title", "summary_lede", "narrative"}
    assert props["summary_lede"]["maxLength"] == SUMMARY_LEDE_MAX
    assert set(schema["input_schema"]["required"]) == {
        "title", "summary_lede", "narrative",
    }
    assert schema["input_schema"]["additionalProperties"] is False


# ---------------------------------------------------------------------------
# Prompts — verbatim P2 phrasing checks (regression guard)
# ---------------------------------------------------------------------------


def test_outline_prompt_carries_p2_phrasing():
    p = _p2_outline_prompt(
        turns_text="[user@0] hi",
        activity_summary="",
        is_continuation=False,
        continues_wikilink=None,
    )
    assert "outline" in p.lower()
    assert "FIRST PASS" in p
    assert "<<<TRANSCRIPT BEGIN>>>" in p
    assert "[user@0] hi" in p
    assert "<<<TRANSCRIPT END>>>" in p


def test_outline_prompt_with_continuation_adds_rider():
    p = _p2_outline_prompt(
        turns_text="[user@0] hi",
        activity_summary="",
        is_continuation=True,
        continues_wikilink="[[prev-part]]",
    )
    assert "CONTINUES [[prev-part]]" in p


def test_outline_prompt_empty_slice_adds_guardrail():
    p = _p2_outline_prompt(
        turns_text="",
        activity_summary="- touched foo.py",
        is_continuation=False,
        continues_wikilink=None,
    )
    # The preamble line names the delimiters in passing; the guardrail
    # check is that the *delimited block* never appears (which would
    # require a non-empty ``turns_text``).
    assert "<<<TRANSCRIPT BEGIN>>>\n" not in p
    assert "No conversation slice" in p
    assert "do not invent topics" in p


def test_compose_prompt_renders_outline_block_and_p2_phrasing():
    p = _p2_compose_prompt(
        outline_items=["topic A", "topic B"],
        turns_text="[assistant@5] reply",
        activity_summary="",
        is_continuation=False,
        continues_wikilink=None,
    )
    assert "=== OUTLINE ===" in p
    assert "  - topic A" in p
    assert "  - topic B" in p
    assert "Bold-led" in p
    assert "@N" in p
    assert "narrative" in p
    assert "<<<TRANSCRIPT BEGIN>>>" in p


def test_compose_prompt_with_continuation_adds_rider():
    p = _p2_compose_prompt(
        outline_items=["a", "b"],
        turns_text="x",
        activity_summary="",
        is_continuation=True,
        continues_wikilink="[[prev]]",
    )
    assert "CONTINUES [[prev]]" in p


# ---------------------------------------------------------------------------
# Two-call dispatch — ``compose_session_note`` end-to-end
# ---------------------------------------------------------------------------


class _FakeBlock:
    """ToolUseBlock-shaped object the Anthropic SDK returns."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.input = payload


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], model: str = "test-model") -> None:
        self.content = [_FakeBlock(payload)]
        self.model = model


class _FakeMessages:
    """Routes outline vs compose by inspecting the requested tool name.

    Matches the contract ``compose_session_note`` calls into:
    ``llm_client.messages.create(... tool_choice={"type":"tool","name":<n>})``.
    """

    def __init__(
        self,
        *,
        outline_items: list[str] | None = None,
        compose_payload: dict[str, Any] | None = None,
    ) -> None:
        # Default only when caller didn't pass a value at all — an
        # explicit ``[]`` must round-trip so the empty-outline test
        # actually sees an empty items list on the wire.
        if outline_items is None:
            outline_items = [
                "Discussed the p2 redesign",
                "Drafted outline schema",
                "Drafted compose schema",
                "Wired two-call compose",
            ]
        self.outline_items = outline_items
        self.compose_payload = compose_payload or {
            "title": "P2 outline-then-narrative path",
            "summary_lede": "Two-call P2 compose path drives narrative.",
            "narrative": "- **Outcome:** narrative reaches phase-2-apply. @0",
        }
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        tool_name = (kwargs.get("tool_choice") or {}).get("name", "")
        if tool_name == "outline":
            return _FakeResponse({"items": self.outline_items})
        return _FakeResponse(self.compose_payload)


class _FakeClient:
    def __init__(self, messages: _FakeMessages) -> None:
        self.messages = messages


def test_compose_session_note_runs_outline_then_compose_and_returns_full_dict():
    msgs = _FakeMessages()
    client = _FakeClient(msgs)

    out = compose_session_note(
        turns_text="[user@0] hi\n[assistant@1] yo",
        activity_summary="- commit abcdef: noop",
        is_continuation=False,
        continues_wikilink=None,
        llm_client=client,
        model="test-model",
        logger=None,
        transcript_id="tx-1",
    )

    assert out is not None
    assert out["title"] == "P2 outline-then-narrative path"
    assert out["summary_lede"] == "Two-call P2 compose path drives narrative."
    assert "narrative reaches phase-2-apply" in out["narrative"]
    assert out["outline_items"] == [
        "Discussed the p2 redesign",
        "Drafted outline schema",
        "Drafted compose schema",
        "Wired two-call compose",
    ]

    # Two calls: outline → compose, in order.
    assert len(msgs.calls) == 2
    assert msgs.calls[0]["tool_choice"] == {"type": "tool", "name": "outline"}
    assert msgs.calls[1]["tool_choice"] == {"type": "tool", "name": "compose"}


def test_compose_session_note_returns_none_on_empty_outline():
    msgs = _FakeMessages(outline_items=[])  # outline returns no items
    client = _FakeClient(msgs)
    out = compose_session_note(
        turns_text="[user@0] hi",
        activity_summary="",
        is_continuation=False,
        continues_wikilink=None,
        llm_client=client,
        model="test-model",
        logger=None,
        transcript_id="tx-2",
    )
    assert out is None
    # Compose never gets called.
    assert len(msgs.calls) == 1


def test_compose_session_note_propagates_outline_telemetry():
    msgs = _FakeMessages()
    client = _FakeClient(msgs)
    events: list[tuple[str, dict[str, Any]]] = []

    class _Logger:
        run_id = "test"

        def emit(self, name: str, **kw: Any) -> None:
            events.append((name, kw))

    compose_session_note(
        turns_text="[user@0] hi",
        activity_summary="",
        is_continuation=False,
        continues_wikilink=None,
        llm_client=client,
        model="test-model",
        logger=_Logger(),
        transcript_id="tx-3",
    )

    calls = {kw.get("call") for _, kw in events}
    assert "p2-outline" in calls
    assert "p2-compose" in calls
    outline_responses = [
        kw for n, kw in events
        if n == "llm-response" and kw.get("call") == "p2-outline"
    ]
    assert len(outline_responses) == 1
    assert outline_responses[0]["outline_items_count"] == 4
