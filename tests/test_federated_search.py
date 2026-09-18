"""Federated search — wiki index hits beside a live GitHub search.

No network: every test puts a stub `gh` on PATH (the binary itself is
faked, so the real subprocess path in ``lore_core.gh`` runs) and points
the cwd at a throwaway directory so no real remote is resolved.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from lore_core.context_pack import gather
from lore_mcp.server import handle_search

ISSUE_HIT = {
    "number": 42,
    "title": "alpha regresses on reindex",
    "state": "open",
    "url": "https://github.com/acme/widgets/issues/42",
    "updatedAt": "2026-09-16T10:00:00Z",
}


def _wiki(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A one-note wiki the FTS index can answer 'alpha' from."""
    wiki = tmp_path / "wiki" / "demo" / "concepts"
    wiki.mkdir(parents=True)
    (wiki / "note.md").write_text("---\ntype: concept\n---\nalpha fox trots\n")
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return wiki


def _stub_gh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload: object = None,
    returncode: int = 0,
) -> Path:
    """Put a fake `gh` on PATH; returns the file its argv is logged to."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    out = tmp_path / "gh-stdout.json"
    out.write_text(json.dumps(payload if payload is not None else []))
    argv_log = tmp_path / "gh-argv.txt"
    stub = bindir / "gh"
    stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {argv_log}\ncat {out}\nexit {returncode}\n')
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return argv_log


def test_search_returns_wiki_hits_then_artifact_hits(tmp_path, monkeypatch):
    _wiki(tmp_path, monkeypatch)
    _stub_gh(tmp_path, monkeypatch, payload=[ISSUE_HIT])

    result = handle_search("alpha", wiki="demo", for_repo="acme/widgets", k=5)

    assert [h["path"] for h in result["wiki"]] == ["concepts/note.md"]
    assert result["artifacts"] == [
        {
            "ref": "acme/widgets#42",
            "title": "alpha regresses on reindex",
            "state": "open",
            "url": "https://github.com/acme/widgets/issues/42",
            "updated_at": "2026-09-16T10:00:00Z",
        }
    ]
    assert "note" not in result


def test_gh_failure_keeps_wiki_hits_and_names_the_omission(tmp_path, monkeypatch):
    _wiki(tmp_path, monkeypatch)
    _stub_gh(tmp_path, monkeypatch, payload=[], returncode=1)

    result = handle_search("alpha", wiki="demo", for_repo="acme/widgets", k=5)

    assert [h["path"] for h in result["wiki"]] == ["concepts/note.md"]
    assert result["artifacts"] == []
    assert "omitted" in result["note"]
    assert "acme/widgets" in result["note"]


def test_offline_machine_without_gh_degrades_to_wiki_hits(tmp_path, monkeypatch):
    _wiki(tmp_path, monkeypatch)
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    result = handle_search("alpha", wiki="demo", for_repo="acme/widgets", k=5)

    assert [h["path"] for h in result["wiki"]] == ["concepts/note.md"]
    assert result["artifacts"] == []
    assert "omitted" in result["note"]


def test_one_gh_call_carries_the_pinned_fields_and_limit(tmp_path, monkeypatch):
    _wiki(tmp_path, monkeypatch)
    argv_log = _stub_gh(tmp_path, monkeypatch, payload=[ISSUE_HIT])

    handle_search("alpha", wiki="demo", for_repo="acme/widgets", k=5)

    calls = argv_log.read_text().splitlines()
    assert calls == [
        "search issues alpha --repo acme/widgets --json number,title,state,url,updatedAt --limit 5"
    ]


def test_tool_description_sends_state_reads_to_gh():
    from lore_mcp.server import _tool_schema

    entry = next(t for t in _tool_schema() if t["name"] == "lore_search")
    text = entry["description"].lower()

    assert "finding" in text
    assert "gh" in text
    for verb in ("comment", "close", "merge", "poll"):
        assert verb in text


# ---------------------------------------------------------------------------
# focus-issue bodies — the pack answers the common case without a search
# ---------------------------------------------------------------------------


def _init_repo(repo_root: Path, *, branch: str, remote_url: str) -> None:
    run = lambda *a: subprocess.run(list(a), cwd=repo_root, check=True)  # noqa: E731
    repo_root.mkdir(parents=True, exist_ok=True)
    run("git", "init", "-q", "-b", branch)
    run("git", "config", "user.email", "test@example.com")
    run("git", "config", "user.name", "Test")
    run("git", "config", "commit.gpgsign", "false")
    run("git", "remote", "add", "origin", remote_url)
    (repo_root / "f.txt").write_text("x")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "init", "--no-verify")


def test_context_pack_focus_issue_carries_the_body(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo, branch="epic/162", remote_url="git@github.com:acme/widgets.git")
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "no-such-vault"))
    argv_log = _stub_gh(
        tmp_path,
        monkeypatch,
        payload={"number": 162, "title": "Epic 162", "state": "OPEN", "body": "the ask"},
    )

    result = gather(cwd=repo, repo_path=str(repo))

    assert argv_log.read_text().splitlines() == [
        "issue view 162 --repo acme/widgets --json number,title,state,body"
    ]
    assert result["epic_state"] == [
        {"number": 162, "title": "Epic 162", "state": "OPEN", "body": "the ask"}
    ]
