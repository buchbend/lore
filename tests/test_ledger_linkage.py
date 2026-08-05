"""Transcript-ledger linkage block — round-trip and back-compat.

The ledger is the personal layer's linkage store: every entry carries
where the session worked (repo, branch), what it referenced (PRs,
issues), and what it produced (commits, files).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry


def _entry(lore_root: Path, **kw) -> TranscriptLedgerEntry:
    defaults = {
        "integration": "claude-code",
        "transcript_id": "t1",
        "path": lore_root / "t1.jsonl",
        "directory": lore_root / "proj",
        "digested_hash": None,
        "digested_index_hint": None,
        "synthesised_hash": None,
        "last_mtime": datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
        "curator_a_run": None,
        "noteworthy": None,
        "session_note": None,
    }
    defaults.update(kw)
    return TranscriptLedgerEntry(**defaults)


def test_linkage_block_round_trips_through_the_ledger_file(tmp_path: Path) -> None:
    ledger = TranscriptLedger(tmp_path)
    ledger.upsert(
        _entry(
            tmp_path,
            linkage={
                "repo": "buchbend/lore",
                "branch": "feat/358-ledger-expansion",
                "prs": [364],
                "issues": [358],
                "commits": ["abc1234"],
                "files": ["lib/lore_core/ledger.py"],
            },
        )
    )

    read_back = TranscriptLedger(tmp_path).get("claude-code", "t1")

    assert read_back is not None
    assert read_back.linkage == {
        "repo": "buchbend/lore",
        "branch": "feat/358-ledger-expansion",
        "prs": [364],
        "issues": [358],
        "commits": ["abc1234"],
        "files": ["lib/lore_core/ledger.py"],
    }


def test_entry_written_before_this_change_loads_with_an_empty_linkage(tmp_path: Path) -> None:
    """AC2: pre-linkage ledgers stay readable. Same one-release
    back-compat contract as the ``host`` → ``integration`` key."""
    legacy = {
        "claude-code::old": {
            "host": "claude-code",
            "transcript_id": "old",
            "path": str(tmp_path / "old.jsonl"),
            "directory": str(tmp_path / "proj"),
            "digested_hash": None,
            "digested_index_hint": None,
            "synthesised_hash": None,
            "last_mtime": "2026-04-18T10:00:00+00:00",
            "curator_a_run": None,
            "noteworthy": None,
            "session_note": None,
        }
    }
    (tmp_path / ".lore").mkdir(parents=True)
    (tmp_path / ".lore" / "transcript-ledger.json").write_text(json.dumps(legacy))

    entry = TranscriptLedger(tmp_path).get("claude-code", "old")

    assert entry is not None
    assert entry.linkage == {}


# ---------------------------------------------------------------------------
# AC1 — capture populates the block
# ---------------------------------------------------------------------------


def _init_repo(repo_root: Path, *, branch: str, remote_url: str) -> None:
    import subprocess

    repo_root.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(a, cwd=repo_root, check=True)  # noqa: E731
    run("git", "init", "-q", "-b", branch)
    run("git", "config", "user.email", "test@example.com")
    run("git", "config", "user.name", "Test")
    run("git", "config", "commit.gpgsign", "false")
    run("git", "remote", "add", "origin", remote_url)
    (repo_root / "f.txt").write_text("x")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "init", "--no-verify")


class _FakeAdapter:
    integration = "fake"

    def __init__(self, handles, turns=()):
        self._handles = handles
        self._turns = list(turns)

    def list_transcripts(self, directory):
        return self._handles

    def read_slice(self, handle, from_index=0):
        yield from self._turns

    def read_slice_after_hash(self, *a, **kw):
        yield from ()

    def is_complete(self, handle):
        return True


def test_capture_stamps_repo_branch_and_branch_issue_on_a_new_entry(tmp_path: Path) -> None:
    """AC1: the entry capture writes carries the linkage block."""
    from lore_core.types import TranscriptHandle
    from lore_curator.capture_routing import register_pending_transcripts

    repo = tmp_path / "proj"
    _init_repo(
        repo,
        branch="feat/358-ledger-expansion",
        remote_url="git@github.com:buchbend/lore.git",
    )
    lore_root = tmp_path / "vault"
    handle = TranscriptHandle(
        integration="fake",
        id="t1",
        path=repo / "t1.jsonl",
        cwd=repo,
        mtime=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )

    register_pending_transcripts(lore_root, repo, adapter=_FakeAdapter([handle]))

    entry = TranscriptLedger(lore_root).get("fake", "t1")
    assert entry is not None
    assert entry.linkage["repo"] == "buchbend/lore"
    assert entry.linkage["branch"] == "feat/358-ledger-expansion"
    assert entry.linkage["issues"] == [358]


def _edit_turn(index: int, file_path: str):
    from lore_core.types import ToolCall, Turn

    return Turn(
        index=index,
        timestamp=None,
        role="assistant",
        tool_call=ToolCall(
            name="Edit",
            input={"file_path": file_path},
            id=f"c{index}",
            category="file_edit",
        ),
    )


def _commit_turns(index: int, sha: str):
    from lore_core.types import ToolCall, ToolResult, Turn

    call = Turn(
        index=index,
        timestamp=None,
        role="assistant",
        tool_call=ToolCall(
            name="Bash",
            input={"command": 'git commit -m "closes #99"'},
            id=f"b{index}",
            category="shell_exec",
        ),
    )
    result = Turn(
        index=index + 1,
        timestamp=None,
        role="tool_result",
        tool_result=ToolResult(
            tool_call_id=f"b{index}",
            output=f"[feat/358 {sha}] closes #99\n 1 file changed",
        ),
    )
    return [call, result]


def test_deep_capture_reads_files_and_commits_out_of_the_transcript(tmp_path: Path) -> None:
    """AC1: at a session boundary the block also carries what the
    session produced — edited files (repo-relative) and commit SHAs."""
    from lore_core.types import TranscriptHandle
    from lore_curator.capture_routing import register_pending_transcripts

    repo = tmp_path / "proj"
    _init_repo(
        repo,
        branch="feat/358-ledger-expansion",
        remote_url="git@github.com:buchbend/lore.git",
    )
    lore_root = tmp_path / "vault"
    handle = TranscriptHandle(
        integration="fake",
        id="t1",
        path=repo / "t1.jsonl",
        cwd=repo,
        mtime=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    turns = [_edit_turn(0, str(repo / "lib" / "a.py")), *_commit_turns(1, "abc1234")]

    register_pending_transcripts(lore_root, repo, adapter=_FakeAdapter([handle], turns), deep=True)

    linkage = TranscriptLedger(lore_root).get("fake", "t1").linkage
    assert linkage["files"] == ["lib/a.py"]
    assert linkage["commits"] == ["abc1234"]


def test_a_later_shallow_capture_keeps_the_deep_results(tmp_path: Path) -> None:
    """Shallow passes run on every prompt; they must not wipe the
    files/commits a boundary pass already derived."""
    from lore_core.types import TranscriptHandle
    from lore_curator.capture_routing import register_pending_transcripts

    repo = tmp_path / "proj"
    _init_repo(repo, branch="feat/358-x", remote_url="git@github.com:buchbend/lore.git")
    lore_root = tmp_path / "vault"
    handle = TranscriptHandle(
        integration="fake",
        id="t1",
        path=repo / "t1.jsonl",
        cwd=repo,
        mtime=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    adapter = _FakeAdapter([handle], [_edit_turn(0, str(repo / "lib" / "a.py"))])
    register_pending_transcripts(lore_root, repo, adapter=adapter, deep=True)

    grown = TranscriptHandle(
        integration="fake",
        id="t1",
        path=repo / "t1.jsonl",
        cwd=repo,
        mtime=datetime(2026, 8, 1, 11, 0, tzinfo=UTC),
    )
    register_pending_transcripts(lore_root, repo, adapter=_FakeAdapter([grown]), deep=False)

    assert TranscriptLedger(lore_root).get("fake", "t1").linkage["files"] == ["lib/a.py"]


def test_session_end_capture_takes_the_deep_pass(tmp_path: Path, monkeypatch) -> None:
    """AC1 routing: the boundary event is what promotes capture to deep."""
    from lore_curator import capture_routing

    seen: list[bool] = []
    monkeypatch.setattr(
        capture_routing,
        "register_pending_transcripts",
        lambda *a, **kw: seen.append(kw.get("deep", False)),
    )
    monkeypatch.setattr(capture_routing, "load_wiki_config", None, raising=False)
    _run_route(tmp_path, capture_routing, event="session-start")
    _run_route(tmp_path, capture_routing, event="session-end")
    _run_route(tmp_path, capture_routing, event="pre-compact")

    assert seen == [False, True, True]


def _run_route(tmp_path: Path, capture_routing, *, event: str) -> None:
    from lore_core.types import Scope

    scope = Scope(
        wiki="demo",
        scope="demo:proj",
        backend="none",
        claude_md_path=tmp_path / "CLAUDE.md",
    )
    (tmp_path / "wiki" / "demo").mkdir(parents=True, exist_ok=True)
    capture_routing.route_capture(
        tmp_path,
        tmp_path / "proj",
        scope,
        event=event,
        adapter=_FakeAdapter([]),
        transcript=None,
        spawn_curator_a=lambda *a, **kw: False,
    )
