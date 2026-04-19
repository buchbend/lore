"""Tests for the ClaudeCodeAdapter (Task 6 — passive-capture MVP).

All SDK calls are mocked via sys.modules injection so the real
claude-agent-sdk package is NOT required at test time.
"""
from __future__ import annotations

import sys
import types
from collections import namedtuple
from datetime import datetime
from pathlib import Path

import pytest

from lore_core.types import ToolCall, ToolResult, TranscriptHandle

# ---------------------------------------------------------------------------
# Fake SDK helpers
# ---------------------------------------------------------------------------

_FakeSession = namedtuple("_FakeSession", ["id", "path", "mtime"])


class _FakeSDK:
    """Drop-in replacement for the claude_agent_sdk module."""

    def __init__(self, sessions=None, messages_by_id=None, raise_on_get=False):
        self._sessions = sessions or []
        self._messages = messages_by_id or {}
        self._raise_on_get = raise_on_get

    def list_sessions(self, directory):  # noqa: ARG002
        return iter(self._sessions)

    def get_session_messages(self, session_id):
        if self._raise_on_get:
            raise RuntimeError("SDK failure")
        return iter(self._messages.get(session_id, []))


def _make_fake_module(sdk_instance: _FakeSDK):
    """Wrap a _FakeSDK instance as a module-like object."""
    mod = types.ModuleType("claude_agent_sdk")
    mod.list_sessions = sdk_instance.list_sessions
    mod.get_session_messages = sdk_instance.get_session_messages
    return mod


def _make_handle(session_id: str = "s1", directory: Path | None = None) -> TranscriptHandle:
    directory = directory or Path("/tmp/sessions")
    return TranscriptHandle(
        host="claude-code",
        id=session_id,
        path=directory / session_id,
        cwd=directory,
        mtime=datetime(2024, 1, 1),
    )


# ---------------------------------------------------------------------------
# Fixture: inject fake SDK into sys.modules for most tests
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0)

_SESSIONS = [
    _FakeSession(id="abc", path="/tmp/sessions/abc", mtime=_NOW),
    _FakeSession(id="def", path="/tmp/sessions/def", mtime=_NOW),
]


@pytest.fixture()
def fake_sdk(monkeypatch):
    """Install a basic fake SDK into sys.modules and return it."""
    sdk_instance = _FakeSDK(sessions=_SESSIONS)
    mod = _make_fake_module(sdk_instance)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)
    return sdk_instance


# ---------------------------------------------------------------------------
# 1. host attribute
# ---------------------------------------------------------------------------


def test_host_attribute_is_claude_code():
    from lore_adapters.claude_code import ClaudeCodeAdapter

    assert ClaudeCodeAdapter.host == "claude-code"


# ---------------------------------------------------------------------------
# 2. list_transcripts returns handles
# ---------------------------------------------------------------------------


def test_list_transcripts_returns_handles(fake_sdk, monkeypatch):  # noqa: ARG001
    from lore_adapters.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    directory = Path("/tmp/sessions")
    handles = adapter.list_transcripts(directory)

    assert len(handles) == 2
    assert handles[0].host == "claude-code"
    assert handles[0].id == "abc"
    assert handles[0].path == Path("/tmp/sessions/abc")
    assert handles[0].cwd == directory
    assert handles[0].mtime == _NOW
    assert handles[1].id == "def"


# ---------------------------------------------------------------------------
# 3. ImportError when SDK missing
# ---------------------------------------------------------------------------


def test_list_transcripts_without_sdk_raises_importerror(monkeypatch):
    # Setting sys.modules["claude_agent_sdk"] = None causes Python to raise
    # ImportError when "import claude_agent_sdk" is attempted inside _require_sdk.
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)  # type: ignore[arg-type]

    from lore_adapters.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    with pytest.raises(ImportError, match="pip install lore\\[capture\\]"):
        adapter.list_transcripts(Path("/tmp"))


# ---------------------------------------------------------------------------
# 4–10. _iter_turns normalisation
# ---------------------------------------------------------------------------


def _make_adapter_with_messages(monkeypatch, session_id: str, messages: list[dict]):
    sdk_instance = _FakeSDK(messages_by_id={session_id: messages})
    mod = _make_fake_module(sdk_instance)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)

    from lore_adapters.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    handle = _make_handle(session_id)
    return adapter, handle


def test_iter_turns_normalises_text(monkeypatch):
    adapter, handle = _make_adapter_with_messages(
        monkeypatch,
        "s1",
        [{"role": "assistant", "content": [{"type": "text", "text": "hi"}]}],
    )
    turns = list(adapter._iter_turns(handle))
    assert len(turns) == 1
    assert turns[0].text == "hi"
    assert turns[0].role == "assistant"


def test_iter_turns_normalises_thinking_to_reasoning(monkeypatch):
    adapter, handle = _make_adapter_with_messages(
        monkeypatch,
        "s1",
        [{"role": "assistant", "content": [{"type": "thinking", "thinking": "deep thought"}]}],
    )
    turns = list(adapter._iter_turns(handle))
    assert len(turns) == 1
    assert turns[0].role == "assistant"
    assert turns[0].reasoning == "deep thought"
    assert turns[0].text is None


def test_iter_turns_normalises_tool_use(monkeypatch):
    adapter, handle = _make_adapter_with_messages(
        monkeypatch,
        "s1",
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {"x": 1}}
                ],
            }
        ],
    )
    turns = list(adapter._iter_turns(handle))
    assert len(turns) == 1
    assert turns[0].role == "assistant"
    assert turns[0].tool_call == ToolCall(name="Read", input={"x": 1}, id="t1")


def test_iter_turns_normalises_tool_result_list_content(monkeypatch):
    adapter, handle = _make_adapter_with_messages(
        monkeypatch,
        "s1",
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [{"type": "text", "text": "ok"}],
                        "is_error": False,
                    }
                ],
            }
        ],
    )
    turns = list(adapter._iter_turns(handle))
    assert len(turns) == 1
    assert turns[0].role == "tool_result"
    assert turns[0].tool_result == ToolResult(tool_call_id="t1", output="ok", is_error=False)


def test_iter_turns_normalises_tool_result_str_content(monkeypatch):
    adapter, handle = _make_adapter_with_messages(
        monkeypatch,
        "s1",
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": "ok",
                        "is_error": False,
                    }
                ],
            }
        ],
    )
    turns = list(adapter._iter_turns(handle))
    assert len(turns) == 1
    assert turns[0].role == "tool_result"
    assert turns[0].tool_result == ToolResult(tool_call_id="t1", output="ok", is_error=False)


def test_iter_turns_multi_block_message_emits_multiple_turns(monkeypatch):
    adapter, handle = _make_adapter_with_messages(
        monkeypatch,
        "s1",
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "About to read"},
                    {"type": "tool_use", "id": "t2", "name": "Read", "input": {"path": "/f"}},
                ],
            }
        ],
    )
    turns = list(adapter._iter_turns(handle))
    assert len(turns) == 2
    assert turns[0].index == 0
    assert turns[0].text == "About to read"
    assert turns[1].index == 1
    assert turns[1].tool_call is not None
    assert turns[1].tool_call.name == "Read"


def test_iter_turns_unknown_block_goes_to_host_extras(monkeypatch):
    block = {"type": "surprise", "data": "boom"}
    adapter, handle = _make_adapter_with_messages(
        monkeypatch,
        "s1",
        [{"role": "assistant", "content": [block]}],
    )
    turns = list(adapter._iter_turns(handle))
    assert len(turns) == 1
    assert turns[0].host_extras.get("claude_code.unknown_block") == block


# ---------------------------------------------------------------------------
# 11. read_slice from_index filter
# ---------------------------------------------------------------------------


def _five_turn_messages():
    """5 consecutive text messages (one block each)."""
    return [
        {"role": "user", "content": [{"type": "text", "text": f"msg{i}"}]}
        for i in range(5)
    ]


def test_read_slice_from_index_filters(monkeypatch):
    adapter, handle = _make_adapter_with_messages(monkeypatch, "s1", _five_turn_messages())
    turns = list(adapter.read_slice(handle, from_index=3))
    assert len(turns) == 2
    assert turns[0].index == 3
    assert turns[1].index == 4


# ---------------------------------------------------------------------------
# 12–15. read_slice_after_hash
# ---------------------------------------------------------------------------


def test_read_slice_after_hash_uses_hint_when_valid(monkeypatch):
    adapter, handle = _make_adapter_with_messages(monkeypatch, "s1", _five_turn_messages())
    all_turns = list(adapter._iter_turns(handle))
    target_hash = all_turns[2].content_hash()
    result = list(adapter.read_slice_after_hash(handle, after_hash=target_hash, index_hint=2))
    assert len(result) == 2
    assert result[0].index == 3
    assert result[1].index == 4


def test_read_slice_after_hash_falls_back_on_hint_mismatch(monkeypatch):
    adapter, handle = _make_adapter_with_messages(monkeypatch, "s1", _five_turn_messages())
    all_turns = list(adapter._iter_turns(handle))
    target_hash = all_turns[2].content_hash()
    # Pass wrong hint (index 4 doesn't match hash of index 2)
    result = list(adapter.read_slice_after_hash(handle, after_hash=target_hash, index_hint=4))
    assert len(result) == 2
    assert result[0].index == 3


def test_read_slice_after_hash_unknown_hash_yields_all(monkeypatch):
    adapter, handle = _make_adapter_with_messages(monkeypatch, "s1", _five_turn_messages())
    result = list(adapter.read_slice_after_hash(handle, after_hash="sha256:deadbeef"))
    assert len(result) == 5


def test_read_slice_after_hash_none_hash_yields_all(monkeypatch):
    adapter, handle = _make_adapter_with_messages(monkeypatch, "s1", _five_turn_messages())
    result = list(adapter.read_slice_after_hash(handle, after_hash=None))
    assert len(result) == 5


# ---------------------------------------------------------------------------
# 16–17. is_complete
# ---------------------------------------------------------------------------


def test_is_complete_true_when_turns_exist(monkeypatch):
    adapter, handle = _make_adapter_with_messages(
        monkeypatch,
        "s1",
        [{"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
    )
    assert adapter.is_complete(handle) is True


def test_is_complete_false_on_exception(monkeypatch):
    sdk_instance = _FakeSDK(raise_on_get=True)
    mod = _make_fake_module(sdk_instance)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)

    from lore_adapters.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    handle = _make_handle("s1")
    assert adapter.is_complete(handle) is False
