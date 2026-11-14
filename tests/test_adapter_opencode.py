"""OpenCode transcript adapter: reads the JSON lines the Lore plugin writes.

Session files are synthesized from the 2.x schema types (see opencode_session).
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import opencode_session as oc
import pytest
from lore_adapters import get_adapter
from lore_adapters.opencode import OpenCodeAdapter
from lore_adapters.protocol import Adapter
from lore_core.capture_routing import register_pending_transcripts
from lore_core.ledger import TranscriptLedger


@pytest.fixture()
def folder(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "oc-sessions"
    monkeypatch.setenv("LORE_OPENCODE_DIR", str(d))
    return d


def _messages() -> list[dict]:
    return [
        oc.user("fix the bug"),
        oc.assistant(
            "msg_a1",
            [
                {"type": "reasoning", "text": "think"},
                oc.text("looking"),
                oc.tool("read", "call_1", {"filePath": str(Path("/p/a.py"))}, "contents"),
                oc.tool("edit", "call_2", {"filePath": str(Path("/p/a.py"))}),
                oc.tool("bash", "call_3", {"command": "ls"}),
            ],
        ),
        oc.idle(),
    ]


def _handle(folder, cwd):
    handles = OpenCodeAdapter().list_transcripts(cwd)
    assert len(handles) == 1
    return handles[0]


def test_registered_under_the_host_name_and_satisfies_the_protocol():
    adapter = get_adapter("opencode")
    assert adapter.integration == "opencode"
    assert isinstance(adapter, Adapter)


def test_lists_only_main_sessions_of_the_directory(folder, tmp_path):
    cwd = tmp_path / "proj"
    oc.write_session(folder, "ses_main", cwd, _messages())
    oc.write_session(folder, "ses_child", cwd, _messages(), parent="ses_main")
    oc.write_session(folder, "ses_other", tmp_path / "elsewhere", _messages())
    h = _handle(folder, cwd)
    assert (h.integration, h.id, h.cwd) == ("opencode", "ses_main", cwd)
    assert isinstance(h.mtime, datetime)


def test_turns_follow_the_message_content(folder, tmp_path):
    cwd = tmp_path / "proj"
    oc.write_session(folder, "ses_main", cwd, _messages())
    turns = list(OpenCodeAdapter().read_slice(_handle(folder, cwd)))
    assert [t.index for t in turns] == list(range(len(turns)))
    assert [(t.role, t.text) for t in turns[:1]] == [("user", "fix the bug")]
    assert turns[1].role == "assistant" and turns[1].reasoning == "think"
    assert turns[2].text == "looking"
    read, read_result, edit, edit_result, bash, _ = turns[3:9]
    assert (read.tool_call.name, read.tool_call.category) == ("read", "file_read")
    assert read_result.role == "tool_result" and read_result.tool_result.output == "contents"
    assert read_result.tool_result.tool_call_id == "call_1"
    assert edit.tool_call.category == "file_edit"
    assert bash.tool_call.category == "shell_exec"
    assert read.timestamp is not None


def test_read_slice_from_index_and_after_hash(folder, tmp_path):
    cwd = tmp_path / "proj"
    oc.write_session(folder, "ses_main", cwd, _messages())
    a, h = OpenCodeAdapter(), _handle(folder, cwd)
    turns = list(a.read_slice(h))
    assert [t.index for t in a.read_slice(h, 3)] == [t.index for t in turns[3:]]
    after = list(a.read_slice_after_hash(h, turns[2].content_hash()))
    assert after == turns[3:]
    assert list(a.read_slice_after_hash(h, None)) == turns


def test_path_for_id_and_is_complete(folder, tmp_path):
    cwd = tmp_path / "proj"
    path = oc.write_session(folder, "ses_main", cwd, _messages())
    a = OpenCodeAdapter()
    assert a.transcript_path_for_id("ses_main", cwd) == path
    assert a.transcript_path_for_id("ses_nope", cwd) is None
    assert a.is_complete(_handle(folder, cwd))


def test_malformed_lines_are_skipped(folder, tmp_path):
    cwd = tmp_path / "proj"
    path = oc.write_session(folder, "ses_main", cwd, _messages())
    path.write_text(path.read_text() + "{not json\n")
    assert len(list(OpenCodeAdapter().read_slice(_handle(folder, cwd)))) == 9


def test_writing_the_file_twice_leaves_one_index_entry_with_linkage(folder, tmp_path):
    cwd = tmp_path / "proj"
    cwd.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(cwd)], check=True)
    lore_root = tmp_path / "vault"
    lore_root.mkdir()
    adapter = get_adapter("opencode")
    oc.write_session(folder, "ses_main", cwd, _messages())
    register_pending_transcripts(lore_root, cwd, adapter=adapter, deep=True)
    path = oc.write_session(folder, "ses_main", cwd, _messages() + [oc.user("more", mid="m9")])
    register_pending_transcripts(lore_root, cwd, adapter=adapter, deep=True)
    entries = TranscriptLedger(lore_root).all_entries()
    assert [(e.integration, e.transcript_id, e.path) for e in entries] == [
        ("opencode", "ses_main", path)
    ]
    assert {"repo", "branch", "commits", "files"} <= set(entries[0].linkage)
