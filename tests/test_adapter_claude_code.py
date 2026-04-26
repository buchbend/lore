"""Tests for the ClaudeCodeAdapter.

The adapter reads Claude Code transcript JSONL files directly from disk
(``~/.claude/projects/<encoded-cwd>/<uuid>.jsonl``). Tests use real
fixture files under ``tmp_path`` and monkeypatch ``Path.home`` to point
the adapter at them.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from lore_core.types import ToolCall, ToolResult, TranscriptHandle


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_home(monkeypatch, tmp_path):
    """Point Path.home() at tmp_path so the adapter finds fixture JSONL files."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def _projects_dir(home: Path, cwd: Path) -> Path:
    encoded = str(Path(cwd).resolve()).replace("/", "-")
    d = home / ".claude" / "projects" / encoded
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_session(
    home: Path,
    cwd: Path,
    session_id: str,
    events: list[dict],
) -> Path:
    """Write a Claude Code-style JSONL file for ``session_id`` under ``cwd``."""
    path = _projects_dir(home, cwd) / f"{session_id}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return path


def _msg_event(role: str, content, *, session_id: str = "s1", ts: str | None = None) -> dict:
    """Build a user/assistant line in Claude Code's JSONL shape."""
    event_type = "user" if role == "user" else "assistant"
    return {
        "type": event_type,
        "timestamp": ts or "2026-04-22T12:00:00Z",
        "sessionId": session_id,
        "message": {"role": role, "content": content},
    }


def _make_handle(session_id: str, path: Path, cwd: Path) -> TranscriptHandle:
    return TranscriptHandle(
        integration="claude-code",
        id=session_id,
        path=path,
        cwd=cwd,
        mtime=datetime(2024, 1, 1),
    )


# ---------------------------------------------------------------------------
# host attribute
# ---------------------------------------------------------------------------


def test_host_attribute_is_claude_code():
    from lore_adapters.claude_code import ClaudeCodeAdapter

    assert ClaudeCodeAdapter.integration == "claude-code"


# ---------------------------------------------------------------------------
# list_transcripts
# ---------------------------------------------------------------------------


def test_list_transcripts_returns_handles_from_disk(fake_home, tmp_path):
    cwd = tmp_path / "proj"
    cwd.mkdir()
    _write_session(fake_home, cwd, "abc", [_msg_event("user", "hi", session_id="abc")])
    _write_session(fake_home, cwd, "def", [_msg_event("user", "yo", session_id="def")])

    from lore_adapters.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    handles = adapter.list_transcripts(cwd)

    ids = sorted(h.id for h in handles)
    assert ids == ["abc", "def"]
    for h in handles:
        assert h.integration == "claude-code"
        assert h.cwd == cwd
        assert h.path.exists()
        assert h.path.suffix == ".jsonl"
        assert h.mtime is not None


def test_list_transcripts_returns_empty_when_projects_dir_missing(fake_home, tmp_path):
    cwd = tmp_path / "never-used"
    cwd.mkdir()

    from lore_adapters.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    assert adapter.list_transcripts(cwd) == []


# ---------------------------------------------------------------------------
# _iter_turns normalisation
# ---------------------------------------------------------------------------


def _adapter_with_events(fake_home, cwd: Path, events: list[dict]):
    from lore_adapters.claude_code import ClaudeCodeAdapter

    path = _write_session(fake_home, cwd, "s1", events)
    adapter = ClaudeCodeAdapter()
    handle = _make_handle("s1", path, cwd)
    return adapter, handle


def test_iter_turns_normalises_text(fake_home, tmp_path):
    cwd = tmp_path / "p"
    cwd.mkdir()
    adapter, handle = _adapter_with_events(
        fake_home, cwd, [_msg_event("assistant", [{"type": "text", "text": "hi"}])]
    )
    turns = list(adapter._iter_turns(handle))
    assert len(turns) == 1
    assert turns[0].text == "hi"
    assert turns[0].role == "assistant"


def test_iter_turns_normalises_thinking_to_reasoning(fake_home, tmp_path):
    cwd = tmp_path / "p"
    cwd.mkdir()
    adapter, handle = _adapter_with_events(
        fake_home,
        cwd,
        [_msg_event("assistant", [{"type": "thinking", "thinking": "deep thought"}])],
    )
    turns = list(adapter._iter_turns(handle))
    assert len(turns) == 1
    assert turns[0].role == "assistant"
    assert turns[0].reasoning == "deep thought"
    assert turns[0].text is None


def test_iter_turns_normalises_tool_use(fake_home, tmp_path):
    cwd = tmp_path / "p"
    cwd.mkdir()
    adapter, handle = _adapter_with_events(
        fake_home,
        cwd,
        [
            _msg_event(
                "assistant",
                [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"x": 1}}],
            )
        ],
    )
    turns = list(adapter._iter_turns(handle))
    assert len(turns) == 1
    assert turns[0].role == "assistant"
    assert turns[0].tool_call == ToolCall(
        name="Read", input={"x": 1}, id="t1", category="file_read",
    )


def test_iter_turns_normalises_tool_result_list_content(fake_home, tmp_path):
    cwd = tmp_path / "p"
    cwd.mkdir()
    adapter, handle = _adapter_with_events(
        fake_home,
        cwd,
        [
            _msg_event(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [{"type": "text", "text": "ok"}],
                        "is_error": False,
                    }
                ],
            )
        ],
    )
    turns = list(adapter._iter_turns(handle))
    assert len(turns) == 1
    assert turns[0].role == "tool_result"
    assert turns[0].tool_result == ToolResult(tool_call_id="t1", output="ok", is_error=False)


def test_iter_turns_normalises_tool_result_str_content(fake_home, tmp_path):
    cwd = tmp_path / "p"
    cwd.mkdir()
    adapter, handle = _adapter_with_events(
        fake_home,
        cwd,
        [
            _msg_event(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": "ok",
                        "is_error": False,
                    }
                ],
            )
        ],
    )
    turns = list(adapter._iter_turns(handle))
    assert len(turns) == 1
    assert turns[0].role == "tool_result"
    assert turns[0].tool_result == ToolResult(tool_call_id="t1", output="ok", is_error=False)


def test_iter_turns_multi_block_message_emits_multiple_turns(fake_home, tmp_path):
    cwd = tmp_path / "p"
    cwd.mkdir()
    adapter, handle = _adapter_with_events(
        fake_home,
        cwd,
        [
            _msg_event(
                "assistant",
                [
                    {"type": "text", "text": "About to read"},
                    {"type": "tool_use", "id": "t2", "name": "Read", "input": {"path": "/f"}},
                ],
            )
        ],
    )
    turns = list(adapter._iter_turns(handle))
    assert len(turns) == 2
    assert turns[0].index == 0
    assert turns[0].text == "About to read"
    assert turns[1].index == 1
    assert turns[1].tool_call is not None
    assert turns[1].tool_call.name == "Read"


def test_iter_turns_unknown_block_goes_to_host_extras(fake_home, tmp_path):
    cwd = tmp_path / "p"
    cwd.mkdir()
    block = {"type": "surprise", "data": "boom"}
    adapter, handle = _adapter_with_events(
        fake_home, cwd, [_msg_event("assistant", [block])]
    )
    turns = list(adapter._iter_turns(handle))
    assert len(turns) == 1
    assert turns[0].integration_extras.get("claude_code.unknown_block") == block


def test_iter_turns_skips_non_user_assistant_event_types(fake_home, tmp_path):
    cwd = tmp_path / "p"
    cwd.mkdir()
    from lore_adapters.claude_code import ClaudeCodeAdapter

    events = [
        {"type": "queue-operation", "operation": "enqueue", "timestamp": "t0", "sessionId": "s1"},
        {"type": "attachment", "attachment": "f", "timestamp": "t1", "sessionId": "s1"},
        _msg_event("user", "hello"),
        {"type": "last-prompt", "timestamp": "t2", "sessionId": "s1"},
    ]
    path = _write_session(fake_home, cwd, "s1", events)
    adapter = ClaudeCodeAdapter()
    handle = _make_handle("s1", path, cwd)
    turns = list(adapter._iter_turns(handle))
    assert len(turns) == 1
    assert turns[0].text == "hello"


def test_iter_turns_tolerates_malformed_jsonl_lines(fake_home, tmp_path):
    cwd = tmp_path / "p"
    cwd.mkdir()
    from lore_adapters.claude_code import ClaudeCodeAdapter

    path = _projects_dir(fake_home, cwd) / "s1.jsonl"
    with path.open("w", encoding="utf-8") as f:
        f.write("not json at all\n")
        f.write(json.dumps(_msg_event("user", "recovered")) + "\n")
        f.write("{broken\n")
    adapter = ClaudeCodeAdapter()
    handle = _make_handle("s1", path, cwd)
    turns = list(adapter._iter_turns(handle))
    assert len(turns) == 1
    assert turns[0].text == "recovered"


def test_iter_turns_missing_file_yields_nothing(tmp_path):
    from lore_adapters.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    handle = _make_handle("nope", tmp_path / "never.jsonl", tmp_path)
    assert list(adapter._iter_turns(handle)) == []


# ---------------------------------------------------------------------------
# read_slice
# ---------------------------------------------------------------------------


def _five_turn_events():
    return [_msg_event("user", [{"type": "text", "text": f"msg{i}"}]) for i in range(5)]


def test_read_slice_from_index_filters(fake_home, tmp_path):
    cwd = tmp_path / "p"
    cwd.mkdir()
    adapter, handle = _adapter_with_events(fake_home, cwd, _five_turn_events())
    turns = list(adapter.read_slice(handle, from_index=3))
    assert len(turns) == 2
    assert turns[0].index == 3
    assert turns[1].index == 4


def test_read_slice_after_hash_uses_hint_when_valid(fake_home, tmp_path):
    cwd = tmp_path / "p"
    cwd.mkdir()
    adapter, handle = _adapter_with_events(fake_home, cwd, _five_turn_events())
    all_turns = list(adapter._iter_turns(handle))
    target_hash = all_turns[2].content_hash()
    result = list(adapter.read_slice_after_hash(handle, after_hash=target_hash, index_hint=2))
    assert len(result) == 2
    assert result[0].index == 3
    assert result[1].index == 4


def test_read_slice_after_hash_falls_back_on_hint_mismatch(fake_home, tmp_path):
    cwd = tmp_path / "p"
    cwd.mkdir()
    adapter, handle = _adapter_with_events(fake_home, cwd, _five_turn_events())
    all_turns = list(adapter._iter_turns(handle))
    target_hash = all_turns[2].content_hash()
    result = list(adapter.read_slice_after_hash(handle, after_hash=target_hash, index_hint=4))
    assert len(result) == 2
    assert result[0].index == 3


def test_read_slice_after_hash_unknown_hash_yields_all(fake_home, tmp_path):
    cwd = tmp_path / "p"
    cwd.mkdir()
    adapter, handle = _adapter_with_events(fake_home, cwd, _five_turn_events())
    result = list(adapter.read_slice_after_hash(handle, after_hash="sha256:deadbeef"))
    assert len(result) == 5


def test_read_slice_after_hash_none_hash_yields_all(fake_home, tmp_path):
    cwd = tmp_path / "p"
    cwd.mkdir()
    adapter, handle = _adapter_with_events(fake_home, cwd, _five_turn_events())
    result = list(adapter.read_slice_after_hash(handle, after_hash=None))
    assert len(result) == 5


# ---------------------------------------------------------------------------
# is_complete
# ---------------------------------------------------------------------------


def test_is_complete_true_when_turns_exist(fake_home, tmp_path):
    cwd = tmp_path / "p"
    cwd.mkdir()
    adapter, handle = _adapter_with_events(
        fake_home, cwd, [_msg_event("assistant", [{"type": "text", "text": "done"}])]
    )
    assert adapter.is_complete(handle) is True


def test_is_complete_false_for_missing_file(tmp_path):
    from lore_adapters.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    handle = _make_handle("s1", tmp_path / "does-not-exist.jsonl", tmp_path)
    assert adapter.is_complete(handle) is False
