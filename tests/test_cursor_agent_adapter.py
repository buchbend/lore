"""Tests for the Cursor Agent Mode JSONL adapter.

Covers the ``~/.cursor/projects/<slug>/agent-transcripts/<uuid>/<uuid>.jsonl``
surface. Fixtures use the message shape confirmed by the storage research
(``{id, role, content: [{type, text|toolName|toolCallId}]}``). If a real
Cursor JSONL in the wild differs materially, adjust fixtures + parser
together — the adapter is intentionally tolerant.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_adapters.cursor_agent import (
    CursorAgentAdapter,
    _slug_for_cwd,
)
from lore_core.types import TranscriptHandle


def _seed_cursor_tree(
    tmp_home: Path, cwd: Path, agent_uuid: str, jsonl_lines: list[str]
) -> Path:
    """Stand up ~/.cursor/projects/<slug>/agent-transcripts/<agent>/<agent>.jsonl."""
    slug = _slug_for_cwd(cwd)
    transcript_dir = tmp_home / ".cursor" / "projects" / slug / "agent-transcripts" / agent_uuid
    transcript_dir.mkdir(parents=True)
    path = transcript_dir / f"{agent_uuid}.jsonl"
    path.write_text("\n".join(jsonl_lines) + "\n")
    return path


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Point HOME at a tmp dir so ~/.cursor resolves there."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Path.home() uses HOME on POSIX; no additional patching needed.
    return tmp_path


# ---------------------------------------------------------------------------
# Slug encoding
# ---------------------------------------------------------------------------


def test_slug_for_cwd_strips_leading_slash_and_replaces_separators(tmp_path: Path) -> None:
    p = tmp_path / "home" / "user" / "project"
    p.mkdir(parents=True)
    # Build an absolute path like /tmp/pytest-xyz/home/user/project and
    # compute its slug. Assertion is structural — no leading dash,
    # original dashes preserved.
    slug = _slug_for_cwd(p)
    assert not slug.startswith("-")
    assert "/" not in slug
    # Absolute path's first component (after /) should be the leading slug component
    resolved_parts = str(p.resolve())[1:].split("/")
    assert slug == "-".join(resolved_parts)


def test_slug_for_cwd_known_example() -> None:
    # The canonical user example: /home/buchbend/git/lore -> home-buchbend-git-lore
    # (absolute path only — we use an explicit string).
    p = Path("/home/buchbend/git/lore")
    # We can't actually create /home/buchbend/git/lore in a test, but we
    # can call _slug_for_cwd with an abs-path that might not exist.
    slug = _slug_for_cwd(p)
    assert slug == "home-buchbend-git-lore"


# ---------------------------------------------------------------------------
# list_transcripts
# ---------------------------------------------------------------------------


def test_list_transcripts_returns_empty_when_no_cursor_dir(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    adapter = CursorAgentAdapter()
    assert adapter.list_transcripts(project) == []


def test_list_transcripts_finds_jsonl(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    _seed_cursor_tree(tmp_home, project, uuid, [
        json.dumps({"id": "1", "role": "user", "content": [{"type": "text", "text": "hi"}]}),
    ])
    adapter = CursorAgentAdapter()
    handles = adapter.list_transcripts(project)
    assert len(handles) == 1
    h = handles[0]
    assert h.host == "cursor"
    assert h.id == uuid
    assert h.path.name == f"{uuid}.jsonl"


def test_list_transcripts_skips_non_jsonl(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    slug = _slug_for_cwd(project)
    agent_dir = (
        tmp_home / ".cursor" / "projects" / slug / "agent-transcripts" / "agent-xxx"
    )
    agent_dir.mkdir(parents=True)
    (agent_dir / "notes.txt").write_text("not a transcript")

    assert CursorAgentAdapter().list_transcripts(project) == []


def test_list_transcripts_multi_agent(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    for uuid in ("111", "222", "333"):
        _seed_cursor_tree(tmp_home, project, uuid, [
            json.dumps({"id": "1", "role": "user",
                        "content": [{"type": "text", "text": f"from {uuid}"}]}),
        ])
    handles = CursorAgentAdapter().list_transcripts(project)
    assert {h.id for h in handles} == {"111", "222", "333"}


# ---------------------------------------------------------------------------
# _iter_turns / read_slice
# ---------------------------------------------------------------------------


def test_read_slice_text_blocks(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    path = _seed_cursor_tree(tmp_home, project, "agent-001", [
        json.dumps({"id": "1", "role": "user",
                    "content": [{"type": "text", "text": "hello"}]}),
        json.dumps({"id": "2", "role": "assistant",
                    "content": [{"type": "text", "text": "hi there"}]}),
    ])
    adapter = CursorAgentAdapter()
    handle = adapter.list_transcripts(project)[0]
    turns = list(adapter.read_slice(handle))
    assert len(turns) == 2
    assert turns[0].role == "user" and turns[0].text == "hello"
    assert turns[1].role == "assistant" and turns[1].text == "hi there"
    assert turns[0].index == 0 and turns[1].index == 1


def test_read_slice_tool_call_block(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _seed_cursor_tree(tmp_home, project, "agent-001", [
        json.dumps({"id": "1", "role": "assistant", "content": [
            {"type": "text", "text": "Let me check."},
            {"type": "tool-call", "toolCallId": "tc_01",
             "toolName": "Read", "args": {"path": "/tmp/x"}},
        ]}),
    ])
    adapter = CursorAgentAdapter()
    handle = adapter.list_transcripts(project)[0]
    turns = list(adapter.read_slice(handle))
    # One message, two content blocks → two turns.
    assert len(turns) == 2
    assert turns[0].text == "Let me check."
    assert turns[1].tool_call is not None
    assert turns[1].tool_call.name == "Read"
    assert turns[1].tool_call.input == {"path": "/tmp/x"}
    assert turns[1].tool_call.id == "tc_01"


def test_read_slice_tool_result_block(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _seed_cursor_tree(tmp_home, project, "agent-001", [
        json.dumps({"id": "1", "role": "tool", "content": [
            {"type": "tool-result", "toolCallId": "tc_01", "result": "output"},
        ]}),
    ])
    adapter = CursorAgentAdapter()
    handle = adapter.list_transcripts(project)[0]
    turns = list(adapter.read_slice(handle))
    assert len(turns) == 1
    assert turns[0].role == "tool_result"
    assert turns[0].tool_result is not None
    assert turns[0].tool_result.tool_call_id == "tc_01"
    assert turns[0].tool_result.output == "output"


def test_read_slice_tolerates_malformed_json(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _seed_cursor_tree(tmp_home, project, "agent-001", [
        json.dumps({"id": "1", "role": "user",
                    "content": [{"type": "text", "text": "ok"}]}),
        "{not-valid-json",
        json.dumps({"id": "2", "role": "assistant",
                    "content": [{"type": "text", "text": "still here"}]}),
    ])
    adapter = CursorAgentAdapter()
    handle = adapter.list_transcripts(project)[0]
    turns = list(adapter.read_slice(handle))
    assert len(turns) == 2
    assert turns[0].text == "ok"
    assert turns[1].text == "still here"


def test_read_slice_handles_unknown_block_type(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _seed_cursor_tree(tmp_home, project, "agent-001", [
        json.dumps({"id": "1", "role": "assistant", "content": [
            {"type": "image", "url": "..."},
        ]}),
    ])
    adapter = CursorAgentAdapter()
    handle = adapter.list_transcripts(project)[0]
    turns = list(adapter.read_slice(handle))
    assert len(turns) == 1
    assert "cursor.unknown_block" in turns[0].host_extras


def test_read_slice_from_index(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _seed_cursor_tree(tmp_home, project, "agent-001", [
        json.dumps({"id": str(i), "role": "user",
                    "content": [{"type": "text", "text": f"m{i}"}]})
        for i in range(5)
    ])
    adapter = CursorAgentAdapter()
    handle = adapter.list_transcripts(project)[0]
    turns = list(adapter.read_slice(handle, from_index=3))
    assert len(turns) == 2
    assert turns[0].index == 3 and turns[1].index == 4


# ---------------------------------------------------------------------------
# read_slice_after_hash (ledger-watermark use case)
# ---------------------------------------------------------------------------


def test_read_slice_after_hash_yields_new_turns(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _seed_cursor_tree(tmp_home, project, "agent-001", [
        json.dumps({"id": str(i), "role": "user",
                    "content": [{"type": "text", "text": f"m{i}"}]})
        for i in range(5)
    ])
    adapter = CursorAgentAdapter()
    handle = adapter.list_transcripts(project)[0]
    all_turns = list(adapter.read_slice(handle))
    # Resume after the hash of turn 2.
    watermark = all_turns[2].content_hash()
    new_turns = list(adapter.read_slice_after_hash(handle, watermark))
    assert [t.index for t in new_turns] == [3, 4]


def test_read_slice_after_hash_none_yields_all(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _seed_cursor_tree(tmp_home, project, "agent-001", [
        json.dumps({"id": "1", "role": "user",
                    "content": [{"type": "text", "text": "hi"}]}),
    ])
    adapter = CursorAgentAdapter()
    handle = adapter.list_transcripts(project)[0]
    turns = list(adapter.read_slice_after_hash(handle, None))
    assert len(turns) == 1


# ---------------------------------------------------------------------------
# is_complete
# ---------------------------------------------------------------------------


def test_is_complete_true_on_readable(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _seed_cursor_tree(tmp_home, project, "agent-001", [
        json.dumps({"id": "1", "role": "user",
                    "content": [{"type": "text", "text": "hi"}]}),
    ])
    adapter = CursorAgentAdapter()
    handle = adapter.list_transcripts(project)[0]
    assert adapter.is_complete(handle) is True


def test_is_complete_false_on_empty(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _seed_cursor_tree(tmp_home, project, "agent-001", [])
    adapter = CursorAgentAdapter()
    handle = TranscriptHandle(
        host="cursor",
        id="agent-001",
        path=tmp_home / ".cursor" / "projects" / _slug_for_cwd(project)
             / "agent-transcripts" / "agent-001" / "agent-001.jsonl",
        cwd=project,
        mtime=datetime.now(UTC),
    )
    assert adapter.is_complete(handle) is False
