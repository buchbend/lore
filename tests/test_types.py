"""Tests for `lore_core.types` — core turn, transcript, scope types."""

from __future__ import annotations

import dataclasses
from datetime import datetime
from pathlib import Path

import pytest
from lore_core.types import (
    BlastRadius,
    Scope,
    ToolCall,
    ToolResult,
    TranscriptHandle,
    Turn,
)


def test_turn_content_hash_is_deterministic():
    """Same Turn fields produce the same content hash."""
    dt = datetime(2026, 4, 18, 12, 0, 0)
    t1 = Turn(
        index=0,
        timestamp=dt,
        role="user",
        text="Hello world",
    )
    t2 = Turn(
        index=0,
        timestamp=dt,
        role="user",
        text="Hello world",
    )
    assert t1.content_hash() == t2.content_hash()


def test_turn_content_hash_differs_on_text_change():
    """Turns differing only in text have different hashes."""
    dt = datetime(2026, 4, 18, 12, 0, 0)
    t1 = Turn(index=0, timestamp=dt, role="user", text="Hello")
    t2 = Turn(index=0, timestamp=dt, role="user", text="World")
    assert t1.content_hash() != t2.content_hash()


def test_turn_content_hash_differs_on_tool_call_input():
    """Turns with different tool_call.input have different hashes."""
    dt = datetime(2026, 4, 18, 12, 0, 0)
    tc1 = ToolCall(name="foo", input={"a": 1})
    tc2 = ToolCall(name="foo", input={"a": 2})
    t1 = Turn(index=0, timestamp=dt, role="assistant", tool_call=tc1)
    t2 = Turn(index=0, timestamp=dt, role="assistant", tool_call=tc2)
    assert t1.content_hash() != t2.content_hash()


def test_turn_content_hash_stable_on_tool_call_input_key_order():
    """Turns with tool_call inputs in different key order have the same hash."""
    dt = datetime(2026, 4, 18, 12, 0, 0)
    tc1 = ToolCall(name="foo", input={"a": 1, "b": 2})
    tc2 = ToolCall(name="foo", input={"b": 2, "a": 1})
    t1 = Turn(index=0, timestamp=dt, role="assistant", tool_call=tc1)
    t2 = Turn(index=0, timestamp=dt, role="assistant", tool_call=tc2)
    assert t1.content_hash() == t2.content_hash()


def test_turn_content_hash_has_sha256_prefix():
    """content_hash() starts with 'sha256:' prefix."""
    dt = datetime(2026, 4, 18, 12, 0, 0)
    t = Turn(index=0, timestamp=dt, role="user", text="test")
    assert t.content_hash().startswith("sha256:")


def test_types_are_frozen():
    """All core types are frozen dataclasses that reject mutation."""
    # ToolCall
    tc = ToolCall(name="test", input={})
    with pytest.raises(dataclasses.FrozenInstanceError):
        tc.name = "modified"

    # ToolResult
    tr = ToolResult(tool_call_id="123", output="result")
    with pytest.raises(dataclasses.FrozenInstanceError):
        tr.output = "modified"

    # Turn
    t = Turn(index=0, timestamp=None, role="user", text="test")
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.index = 1

    # TranscriptHandle
    th = TranscriptHandle(
        integration="localhost",
        id="1",
        path=Path("/tmp"),
        cwd=Path("/tmp"),
        mtime=datetime.now(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        th.integration = "modified"

    # Scope
    s = Scope(
        wiki="test",
        scope="a:b",
        backend="github",
        claude_md_path=Path("/tmp/CLAUDE.md"),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.wiki = "modified"


def test_blast_radius_enum_values():
    """BlastRadius enum has all expected values."""
    assert BlastRadius.CREATE.value == "create"
    assert BlastRadius.EDIT_FRONTMATTER.value == "edit-fm"
    assert BlastRadius.EDIT_BODY.value == "edit-body"
    assert BlastRadius.SUPERSEDE.value == "supersede"
