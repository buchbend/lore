"""Tests for the VSCode Copilot Chat JSONL adapter.

Covers ``<vscode-user>/workspaceStorage/<hash>/chatSessions/<id>.jsonl``
across Linux / macOS / (partially) Windows, and the Cursor variant
(same layout under ``~/.config/Cursor/User/``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from lore_adapters.vscode_copilot import (
    VSCodeCopilotAdapter,
    _apply_patch,
    _extract_text,
    _replay_jsonl,
)


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Point HOME + XDG_CONFIG_HOME at tmp; cover Linux probe path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    return tmp_path


def _seed_workspace(
    tmp_home: Path,
    cwd: Path,
    *,
    editor: str = "Code",
    ws_hash: str = "abc123",
    jsonl_lines: list[str] | None = None,
    session_id: str = "session-001",
) -> Path:
    """Set up <vscode-user>/workspaceStorage/<hash>/{workspace.json, chatSessions/<session>.jsonl}."""
    if sys.platform.startswith("linux"):
        base = tmp_home / ".config"
    elif sys.platform == "darwin":
        base = tmp_home / "Library" / "Application Support"
    else:  # win — untested, but the adapter should at least not crash
        base = tmp_home / "AppData" / "Roaming"
    user_dir = base / editor / "User"
    ws_dir = user_dir / "workspaceStorage" / ws_hash
    ws_dir.mkdir(parents=True)
    (ws_dir / "workspace.json").write_text(
        json.dumps({"folder": f"file://{cwd.resolve()}"})
    )
    sessions_dir = ws_dir / "chatSessions"
    sessions_dir.mkdir()
    jsonl = sessions_dir / f"{session_id}.jsonl"
    jsonl.write_text("\n".join(jsonl_lines or []) + ("\n" if jsonl_lines else ""))
    return jsonl


# ---------------------------------------------------------------------------
# _apply_patch
# ---------------------------------------------------------------------------


def test_apply_patch_top_level_key() -> None:
    state = {"a": 1}
    _apply_patch(state, ["a"], 2)
    assert state == {"a": 2}


def test_apply_patch_nested() -> None:
    state = {"a": {"b": {"c": 1}}}
    _apply_patch(state, ["a", "b", "c"], 99)
    assert state == {"a": {"b": {"c": 99}}}


def test_apply_patch_list_index() -> None:
    state = {"items": [10, 20, 30]}
    _apply_patch(state, ["items", "1"], 99)
    assert state == {"items": [10, 99, 30]}


def test_apply_patch_creates_missing_intermediate() -> None:
    state = {}
    _apply_patch(state, ["a", "b", "c"], 1)
    assert state == {"a": {"b": {"c": 1}}}


# ---------------------------------------------------------------------------
# _extract_text
# ---------------------------------------------------------------------------


def test_extract_text_bare_string() -> None:
    assert _extract_text("hello") == "hello"


def test_extract_text_parts() -> None:
    node = {"parts": [{"kind": "text", "text": "line 1"},
                      {"kind": "text", "text": "line 2"}]}
    assert _extract_text(node) == "line 1\nline 2"


def test_extract_text_markdown_content() -> None:
    node = {"parts": [{"kind": "markdownContent", "text": "# hello"}]}
    assert _extract_text(node) == "# hello"


def test_extract_text_text_key() -> None:
    assert _extract_text({"text": "hi"}) == "hi"


def test_extract_text_none_for_empty() -> None:
    assert _extract_text(None) is None
    assert _extract_text({}) is None
    assert _extract_text("") is None


# ---------------------------------------------------------------------------
# _replay_jsonl
# ---------------------------------------------------------------------------


def test_replay_snapshot_only(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({
        "kind": 0,
        "v": {"version": 3, "sessionId": "s1", "requests": [
            {"requestId": "r1", "message": "hi", "response": "hello"}
        ]},
    }) + "\n")
    state = _replay_jsonl(p)
    assert state["version"] == 3
    assert state["requests"][0]["message"] == "hi"


def test_replay_applies_patches(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join([
        json.dumps({"kind": 0, "v": {"version": 3, "customTitle": "Untitled",
                                     "requests": []}}),
        json.dumps({"kind": 1, "k": ["customTitle"], "v": "New Chat"}),
    ]) + "\n")
    state = _replay_jsonl(p)
    assert state["customTitle"] == "New Chat"


def test_replay_ignores_patches_before_snapshot(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join([
        json.dumps({"kind": 1, "k": ["customTitle"], "v": "early"}),
        json.dumps({"kind": 0, "v": {"version": 3, "customTitle": "base", "requests": []}}),
    ]) + "\n")
    assert _replay_jsonl(p)["customTitle"] == "base"


def test_replay_tolerates_malformed_lines(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join([
        json.dumps({"kind": 0, "v": {"version": 3, "requests": []}}),
        "not-json-{",
        json.dumps({"kind": 1, "k": ["customTitle"], "v": "ok"}),
    ]) + "\n")
    state = _replay_jsonl(p)
    assert state["customTitle"] == "ok"


# ---------------------------------------------------------------------------
# list_transcripts
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not sys.platform.startswith("linux") and sys.platform != "darwin",
    reason="probe paths only exercised on linux/darwin",
)
def test_list_transcripts_finds_session(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _seed_workspace(tmp_home, project, jsonl_lines=[json.dumps({
        "kind": 0, "v": {"version": 3, "requests": [
            {"requestId": "r1", "message": "hi", "response": "hello"}
        ]},
    })])
    handles = VSCodeCopilotAdapter().list_transcripts(project)
    assert len(handles) == 1
    assert handles[0].integration == "copilot"
    assert handles[0].path.name == "session-001.jsonl"


@pytest.mark.skipif(
    not sys.platform.startswith("linux") and sys.platform != "darwin",
    reason="probe paths only exercised on linux/darwin",
)
def test_list_transcripts_empty_when_no_workspace_json(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    # No seeding → empty result.
    assert VSCodeCopilotAdapter().list_transcripts(project) == []


@pytest.mark.skipif(
    not sys.platform.startswith("linux") and sys.platform != "darwin",
    reason="probe paths only exercised on linux/darwin",
)
def test_list_transcripts_covers_cursor_variant(tmp_home, tmp_path) -> None:
    """Copilot Chat installed inside Cursor writes to Cursor's user-data dir."""
    project = tmp_path / "proj"
    project.mkdir()
    _seed_workspace(tmp_home, project, editor="Cursor",
                    jsonl_lines=[json.dumps({
                        "kind": 0, "v": {"version": 3, "requests": []},
                    })])
    handles = VSCodeCopilotAdapter().list_transcripts(project)
    assert len(handles) == 1


# ---------------------------------------------------------------------------
# Turn emission
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not sys.platform.startswith("linux") and sys.platform != "darwin",
    reason="probe paths only exercised on linux/darwin",
)
def test_read_slice_emits_user_and_assistant_turns(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _seed_workspace(tmp_home, project, jsonl_lines=[json.dumps({
        "kind": 0, "v": {"version": 3, "requests": [
            {"requestId": "r1", "timestamp": 1700000000000,
             "message": "first q",
             "response": {"parts": [{"kind": "text", "text": "first a"}]}},
            {"requestId": "r2", "timestamp": 1700000100000,
             "message": "second q",
             "response": "second a"},
        ]},
    })])
    adapter = VSCodeCopilotAdapter()
    handle = adapter.list_transcripts(project)[0]
    turns = list(adapter.read_slice(handle))
    assert [t.role for t in turns] == ["user", "assistant", "user", "assistant"]
    assert turns[0].text == "first q"
    assert turns[1].text == "first a"
    assert turns[2].text == "second q"
    assert turns[3].text == "second a"
    assert [t.index for t in turns] == [0, 1, 2, 3]


@pytest.mark.skipif(
    not sys.platform.startswith("linux") and sys.platform != "darwin",
    reason="probe paths only exercised on linux/darwin",
)
def test_unsupported_version_carries_in_host_extras(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _seed_workspace(tmp_home, project, jsonl_lines=[json.dumps({
        "kind": 0, "v": {"version": 99, "requests": [
            {"requestId": "r1", "message": "q", "response": "a"},
        ]},
    })])
    adapter = VSCodeCopilotAdapter()
    handle = adapter.list_transcripts(project)[0]
    turns = list(adapter.read_slice(handle))
    assert turns
    assert turns[0].integration_extras.get("copilot.unsupported_version") == 99


@pytest.mark.skipif(
    not sys.platform.startswith("linux") and sys.platform != "darwin",
    reason="probe paths only exercised on linux/darwin",
)
def test_read_slice_after_hash_resumes(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _seed_workspace(tmp_home, project, jsonl_lines=[json.dumps({
        "kind": 0, "v": {"version": 3, "requests": [
            {"requestId": f"r{i}", "message": f"q{i}", "response": f"a{i}"}
            for i in range(3)
        ]},
    })])
    adapter = VSCodeCopilotAdapter()
    handle = adapter.list_transcripts(project)[0]
    all_turns = list(adapter.read_slice(handle))
    watermark = all_turns[2].content_hash()  # after q1's response
    resumed = list(adapter.read_slice_after_hash(handle, watermark))
    assert [t.index for t in resumed] == [3, 4, 5]


@pytest.mark.skipif(
    not sys.platform.startswith("linux") and sys.platform != "darwin",
    reason="probe paths only exercised on linux/darwin",
)
def test_is_complete_for_populated_session(tmp_home, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _seed_workspace(tmp_home, project, jsonl_lines=[json.dumps({
        "kind": 0, "v": {"version": 3, "requests": [
            {"requestId": "r1", "message": "q", "response": "a"},
        ]},
    })])
    adapter = VSCodeCopilotAdapter()
    handle = adapter.list_transcripts(project)[0]
    assert adapter.is_complete(handle) is True
