"""Tests for the ManualSendAdapter (Task 7 — passive-capture MVP).

User-dumped JSONL transcripts from hosts without native adapters.
"""

from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path

import pytest

from lore_core.types import ToolCall, ToolResult, TranscriptHandle


# ---------------------------------------------------------------------------
# 1. host attribute
# ---------------------------------------------------------------------------


def test_host_attribute_is_manual_send():
    from lore_adapters.manual_send import ManualSendAdapter

    assert ManualSendAdapter.host == "manual-send"


# ---------------------------------------------------------------------------
# 2. list_transcripts returns empty
# ---------------------------------------------------------------------------


def test_list_transcripts_returns_empty():
    from lore_adapters.manual_send import ManualSendAdapter

    adapter = ManualSendAdapter()
    directory = Path("/tmp")
    handles = adapter.list_transcripts(directory)
    assert handles == []


# ---------------------------------------------------------------------------
# 3. read_from parses JSONL file path
# ---------------------------------------------------------------------------


def test_read_from_parses_jsonl_path(tmp_path):
    from lore_adapters.manual_send import ManualSendAdapter

    # Create a temp JSONL file
    jsonl_file = tmp_path / "transcript.jsonl"
    lines = [
        {"index": 0, "role": "user", "text": "hi"},
        {"index": 1, "role": "assistant", "text": "hello"},
    ]
    with jsonl_file.open("w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")

    adapter = ManualSendAdapter()
    turns = list(adapter.read_from(jsonl_file, tmp_path))

    assert len(turns) == 2
    assert turns[0].index == 0
    assert turns[0].role == "user"
    assert turns[0].text == "hi"
    assert turns[1].index == 1
    assert turns[1].role == "assistant"
    assert turns[1].text == "hello"


# ---------------------------------------------------------------------------
# 4. read_from parses JSONL stream (StringIO)
# ---------------------------------------------------------------------------


def test_read_from_parses_jsonl_stream():
    from lore_adapters.manual_send import ManualSendAdapter

    jsonl = io.StringIO(
        '{"index": 0, "role": "user", "text": "hi"}\n'
        '{"index": 1, "role": "assistant", "text": "hello"}\n'
    )
    adapter = ManualSendAdapter()
    turns = list(adapter.read_from(jsonl, Path("/tmp")))

    assert len(turns) == 2
    assert turns[0].text == "hi"
    assert turns[1].text == "hello"


# ---------------------------------------------------------------------------
# 5. read_from skips metadata preamble
# ---------------------------------------------------------------------------


def test_read_from_skips_meta_preamble(tmp_path):
    from lore_adapters.manual_send import ManualSendAdapter

    jsonl_file = tmp_path / "transcript.jsonl"
    with jsonl_file.open("w") as f:
        f.write('{"_meta": {"schema_version": 1, "source_host": "cursor"}}\n')
        f.write('{"index": 0, "role": "user", "text": "hi"}\n')

    adapter = ManualSendAdapter()
    turns = list(adapter.read_from(jsonl_file, tmp_path))

    # Should only emit one Turn, not the metadata line
    assert len(turns) == 1
    assert turns[0].text == "hi"


# ---------------------------------------------------------------------------
# 6. read_from skips blank lines
# ---------------------------------------------------------------------------


def test_read_from_skips_blank_lines(tmp_path):
    from lore_adapters.manual_send import ManualSendAdapter

    jsonl_file = tmp_path / "transcript.jsonl"
    with jsonl_file.open("w") as f:
        f.write('{"index": 0, "role": "user", "text": "hi"}\n')
        f.write("\n")
        f.write("   \n")
        f.write('{"index": 1, "role": "assistant", "text": "hello"}\n')

    adapter = ManualSendAdapter()
    turns = list(adapter.read_from(jsonl_file, tmp_path))

    assert len(turns) == 2
    assert turns[0].index == 0
    assert turns[1].index == 1


# ---------------------------------------------------------------------------
# 7. read_from preserves declared_host in host_extras
# ---------------------------------------------------------------------------


def test_read_from_preserves_declared_host_in_host_extras(tmp_path):
    from lore_adapters.manual_send import ManualSendAdapter

    jsonl_file = tmp_path / "transcript.jsonl"
    with jsonl_file.open("w") as f:
        f.write('{"index": 0, "role": "user", "text": "hi"}\n')

    adapter = ManualSendAdapter()
    turns = list(adapter.read_from(jsonl_file, tmp_path, declared_host="cursor"))

    assert len(turns) == 1
    assert turns[0].host_extras["manual_send.declared_host"] == "cursor"


# ---------------------------------------------------------------------------
# 8. read_from parses tool_call block
# ---------------------------------------------------------------------------


def test_read_from_parses_tool_call_block(tmp_path):
    from lore_adapters.manual_send import ManualSendAdapter

    jsonl_file = tmp_path / "transcript.jsonl"
    with jsonl_file.open("w") as f:
        f.write(
            '{"index": 0, "role": "assistant", "tool_call": '
            '{"name": "Read", "input": {"path": "a.py"}, "id": "t1"}}\n'
        )

    adapter = ManualSendAdapter()
    turns = list(adapter.read_from(jsonl_file, tmp_path))

    assert len(turns) == 1
    assert turns[0].tool_call == ToolCall(
        name="Read", input={"path": "a.py"}, id="t1"
    )
    assert turns[0].role == "assistant"


# ---------------------------------------------------------------------------
# 9. read_from parses tool_result block
# ---------------------------------------------------------------------------


def test_read_from_parses_tool_result_block(tmp_path):
    from lore_adapters.manual_send import ManualSendAdapter

    jsonl_file = tmp_path / "transcript.jsonl"
    with jsonl_file.open("w") as f:
        f.write(
            '{"index": 0, "role": "tool_result", "tool_result": '
            '{"tool_call_id": "t1", "output": "content", "is_error": false}}\n'
        )

    adapter = ManualSendAdapter()
    turns = list(adapter.read_from(jsonl_file, tmp_path))

    assert len(turns) == 1
    assert turns[0].tool_result == ToolResult(
        tool_call_id="t1", output="content", is_error=False
    )
    assert turns[0].role == "tool_result"


# ---------------------------------------------------------------------------
# 10. read_from parses reasoning block
# ---------------------------------------------------------------------------


def test_read_from_parses_reasoning_block(tmp_path):
    from lore_adapters.manual_send import ManualSendAdapter

    jsonl_file = tmp_path / "transcript.jsonl"
    with jsonl_file.open("w") as f:
        f.write(
            '{"index": 0, "role": "assistant", "reasoning": "think about X"}\n'
        )

    adapter = ManualSendAdapter()
    turns = list(adapter.read_from(jsonl_file, tmp_path))

    assert len(turns) == 1
    assert turns[0].reasoning == "think about X"
    assert turns[0].role == "assistant"


# ---------------------------------------------------------------------------
# 11. read_from rejects missing index
# ---------------------------------------------------------------------------


def test_read_from_rejects_missing_index(tmp_path):
    from lore_adapters.manual_send import ManualSendAdapter

    jsonl_file = tmp_path / "transcript.jsonl"
    with jsonl_file.open("w") as f:
        f.write('{"role": "user", "text": "hi"}\n')

    adapter = ManualSendAdapter()
    with pytest.raises(ValueError, match="missing required field.*index"):
        list(adapter.read_from(jsonl_file, tmp_path))


# ---------------------------------------------------------------------------
# 12. read_from rejects missing role
# ---------------------------------------------------------------------------


def test_read_from_rejects_missing_role(tmp_path):
    from lore_adapters.manual_send import ManualSendAdapter

    jsonl_file = tmp_path / "transcript.jsonl"
    with jsonl_file.open("w") as f:
        f.write('{"index": 0, "text": "hi"}\n')

    adapter = ManualSendAdapter()
    with pytest.raises(ValueError, match="missing required field.*role"):
        list(adapter.read_from(jsonl_file, tmp_path))


# ---------------------------------------------------------------------------
# 13. read_from rejects malformed JSON
# ---------------------------------------------------------------------------


def test_read_from_rejects_malformed_json(tmp_path):
    from lore_adapters.manual_send import ManualSendAdapter

    jsonl_file = tmp_path / "transcript.jsonl"
    with jsonl_file.open("w") as f:
        f.write('{"index": 0, "role": "user", "text": "hi"\n')  # missing closing }

    adapter = ManualSendAdapter()
    with pytest.raises(ValueError, match="malformed JSON on line"):
        list(adapter.read_from(jsonl_file, tmp_path))


# ---------------------------------------------------------------------------
# 14. read_slice filters from_index
# ---------------------------------------------------------------------------


def test_read_slice_from_index_filters(tmp_path):
    from lore_adapters.manual_send import ManualSendAdapter

    jsonl_file = tmp_path / "transcript.jsonl"
    with jsonl_file.open("w") as f:
        for i in range(5):
            f.write(f'{{"index": {i}, "role": "user", "text": "msg{i}"}}\n')

    adapter = ManualSendAdapter()
    handle = TranscriptHandle(
        host="manual-send",
        id="test",
        path=jsonl_file,
        cwd=tmp_path,
        mtime=datetime.now(),
    )
    turns = list(adapter.read_slice(handle, from_index=2))

    assert len(turns) == 3
    assert turns[0].index == 2
    assert turns[1].index == 3
    assert turns[2].index == 4


# ---------------------------------------------------------------------------
# 15. read_slice_after_hash roundtrip
# ---------------------------------------------------------------------------


def test_read_slice_after_hash_roundtrip(tmp_path):
    from lore_adapters.manual_send import ManualSendAdapter

    jsonl_file = tmp_path / "transcript.jsonl"
    with jsonl_file.open("w") as f:
        for i in range(5):
            f.write(f'{{"index": {i}, "role": "user", "text": "msg{i}"}}\n')

    adapter = ManualSendAdapter()
    handle = TranscriptHandle(
        host="manual-send",
        id="test",
        path=jsonl_file,
        cwd=tmp_path,
        mtime=datetime.now(),
    )

    # Get all turns to find the hash of turn at index 2
    all_turns = list(adapter.read_slice(handle, from_index=0))
    target_hash = all_turns[2].content_hash()

    # Read after that hash
    result = list(adapter.read_slice_after_hash(handle, after_hash=target_hash))

    assert len(result) == 2
    assert result[0].index == 3
    assert result[1].index == 4


# ---------------------------------------------------------------------------
# 16. is_complete always true
# ---------------------------------------------------------------------------


def test_is_complete_always_true(tmp_path):
    from lore_adapters.manual_send import ManualSendAdapter

    jsonl_file = tmp_path / "transcript.jsonl"
    with jsonl_file.open("w") as f:
        f.write('{"index": 0, "role": "user", "text": "hi"}\n')

    adapter = ManualSendAdapter()
    handle = TranscriptHandle(
        host="manual-send",
        id="test",
        path=jsonl_file,
        cwd=tmp_path,
        mtime=datetime.now(),
    )

    assert adapter.is_complete(handle) is True
