"""Tests for `lore_core.context_pack` — the deterministic `lore_context_pack`
join (PRD 0004).

Relevance is a join on linkage keys (repo, scope, issue/epic) between
session-note frontmatter and a connected repo's ADR/PRD homes — never an
LLM call, never an FTS ranking dressed up as a join. Cold-start (no repo,
no vault, no scope) degrades to a well-formed empty pack, never an error.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml
from lore_core.context_pack import gather
from lore_core.state.attachments import Attachment, AttachmentsFile

_TODAY = date.today().isoformat()


def _init_repo(repo_root: Path, *, branch: str = "main", remote_url: str | None = None) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo_root, check=True)
    if remote_url:
        subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=repo_root, check=True)
    (repo_root / "f.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init", "--no-verify"], cwd=repo_root, check=True)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _session_note(
    *,
    repo: str = "",
    epics: list[int] | None = None,
    issues: list[int] | None = None,
    scope: str = "w:s",
    description: str = "a session",
) -> str:
    fm = {
        "schema_version": 2,
        "type": "session",
        "created": _TODAY,
        "last_reviewed": _TODAY,
        "description": description,
        "scope": scope,
        "linkage": {
            "schema_version": 1,
            "repo": repo,
            "branch": "main",
            "issues": issues or [],
            "prs": [],
            "epics": epics or [],
            "author": "",
        },
    }
    return f"---\n{yaml.safe_dump(fm, sort_keys=False)}---\n\n# s\n"


def _no_vault(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "no-such-vault"))


# ---------------------------------------------------------------------------
# repo-linkage join
# ---------------------------------------------------------------------------


def test_gather_joins_session_notes_by_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, remote_url="git@github.com:acme/widget.git")

    vault = tmp_path / "vault"
    wiki = vault / "wiki" / "w"
    _write(wiki / "sessions" / f"{_TODAY}-matching.md", _session_note(repo="acme/widget"))
    _write(wiki / "sessions" / f"{_TODAY}-unrelated.md", _session_note(repo="acme/other"))
    monkeypatch.setenv("LORE_ROOT", str(vault))

    result = gather(cwd=repo, repo_path=str(repo))
    assert "error" not in result
    assert result["repo"] == "acme/widget"
    paths = {s["path"] for s in result["sessions"]}
    assert any("matching" in p for p in paths)
    assert not any("unrelated" in p for p in paths)


# ---------------------------------------------------------------------------
# epic / issue join
# ---------------------------------------------------------------------------


def test_gather_joins_by_epic_from_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, branch="epic/162")

    vault = tmp_path / "vault"
    wiki = vault / "wiki" / "w"
    _write(
        wiki / "sessions" / f"{_TODAY}-on-epic.md",
        _session_note(repo="other/repo", epics=[162]),
    )
    _write(
        wiki / "sessions" / f"{_TODAY}-off-epic.md",
        _session_note(repo="other/repo", epics=[99]),
    )
    monkeypatch.setenv("LORE_ROOT", str(vault))

    result = gather(cwd=repo, repo_path=str(repo))
    assert result["focus_issues"] == [162]
    paths = {s["path"] for s in result["sessions"]}
    assert any("on-epic" in p for p in paths)
    assert not any("off-epic" in p for p in paths)


def test_gather_issue_param_widens_focus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)

    vault = tmp_path / "vault"
    wiki = vault / "wiki" / "w"
    _write(
        wiki / "sessions" / f"{_TODAY}-issue-180.md",
        _session_note(repo="other/repo", issues=[180]),
    )
    monkeypatch.setenv("LORE_ROOT", str(vault))

    result = gather(cwd=repo, repo_path=str(repo), issue=180)
    assert 180 in result["focus_issues"]
    paths = {s["path"] for s in result["sessions"]}
    assert any("issue-180" in p for p in paths)


def test_gather_epic_state_calls_gh_issue_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, branch="epic/162", remote_url="git@github.com:acme/widget.git")
    _no_vault(monkeypatch, tmp_path)

    calls = []

    def fake_gh_issue_view(repo_slug: str, number: int):
        calls.append((repo_slug, number))
        return {"number": number, "title": "Epic 162", "state": "OPEN"}

    monkeypatch.setattr("lore_core.context_pack.gh_issue_view", fake_gh_issue_view)

    result = gather(cwd=repo, repo_path=str(repo))
    assert calls == [("acme/widget", 162)]
    assert result["epic_state"] == [{"number": 162, "title": "Epic 162", "state": "OPEN"}]


def test_gather_epic_state_silent_when_gh_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, branch="epic/162", remote_url="git@github.com:acme/widget.git")
    _no_vault(monkeypatch, tmp_path)

    monkeypatch.setattr("lore_core.context_pack.gh_issue_view", lambda *a: None)

    result = gather(cwd=repo, repo_path=str(repo))
    assert "error" not in result
    assert result["epic_state"] == []


# ---------------------------------------------------------------------------
# ADR/PRD join (repo_docs pull + linkage classification of doc content)
# ---------------------------------------------------------------------------


def test_gather_filters_adr_prd_by_epic_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, branch="epic/162")
    _write(repo / "docs/adr/0001-x.md", "# ADR\n\nContext: epic #162.\n")
    _write(repo / "docs/adr/0002-y.md", "# ADR unrelated\n\nNo epic ref here.\n")
    _write(repo / "docs/prd/0001-p.md", "---\ntitle: P\n---\nepic #162 tracked here.\n")
    _no_vault(monkeypatch, tmp_path)

    result = gather(cwd=repo, repo_path=str(repo))
    adr_paths = {e["path"] for e in result["adr"]}
    assert "docs/adr/0001-x.md" in adr_paths
    assert "docs/adr/0002-y.md" not in adr_paths
    assert {e["path"] for e in result["prd"]} == {"docs/prd/0001-p.md"}


def test_gather_returns_all_adr_prd_when_no_focus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)  # branch "main" — no epic/issue signal
    _write(repo / "docs/adr/0001-x.md", "# ADR X\n")
    _write(repo / "docs/adr/0002-y.md", "# ADR Y\n")
    _no_vault(monkeypatch, tmp_path)

    result = gather(cwd=repo, repo_path=str(repo))
    assert result["focus_issues"] == []
    assert {e["path"] for e in result["adr"]} == {"docs/adr/0001-x.md", "docs/adr/0002-y.md"}


# ---------------------------------------------------------------------------
# scope join (via attachments-backed scope_resolver)
# ---------------------------------------------------------------------------


def test_gather_joins_by_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)  # no remote — repo string is empty

    vault = tmp_path / "vault"
    (vault / ".lore").mkdir(parents=True)
    af = AttachmentsFile(vault)
    af.load()
    af.add(
        Attachment(path=repo, wiki="w", scope="w:s", attached_at=datetime(2026, 1, 1, tzinfo=UTC))
    )
    af.save()

    wiki = vault / "wiki" / "w"
    _write(
        wiki / "sessions" / f"{_TODAY}-scoped.md", _session_note(repo="unrelated/repo", scope="w:s")
    )
    _write(
        wiki / "sessions" / f"{_TODAY}-other-scope.md",
        _session_note(repo="unrelated/repo", scope="w:other"),
    )
    monkeypatch.setenv("LORE_ROOT", str(vault))

    result = gather(cwd=repo, repo_path=str(repo))
    assert result["scope"] == "w:s"
    assert result["wiki"] == "w"
    paths = {s["path"] for s in result["sessions"]}
    assert any("scoped.md" in p and "other-scope" not in p for p in paths)
    assert not any("other-scope" in p for p in paths)


# ---------------------------------------------------------------------------
# cold start — well-formed empty packs, never errors
# ---------------------------------------------------------------------------


def test_gather_cold_start_no_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, remote_url="git@github.com:acme/widget.git")
    _no_vault(monkeypatch, tmp_path)

    result = gather(cwd=repo, repo_path=str(repo))
    assert "error" not in result
    assert result["sessions"] == []
    assert result["repo"] == "acme/widget"


def test_gather_cold_start_outside_git_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stranger = tmp_path / "stranger"
    stranger.mkdir()
    _no_vault(monkeypatch, tmp_path)

    result = gather(cwd=stranger)
    assert "error" not in result
    assert result["repo"] == ""
    assert result["sessions"] == []
    assert result["adr"] == []
    assert result["prd"] == []
    assert result["epic_state"] == []


# ---------------------------------------------------------------------------
# bounded pack
# ---------------------------------------------------------------------------


def test_gather_bounds_session_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lore_core.context_pack import MAX_SESSIONS

    repo = tmp_path / "repo"
    _init_repo(repo, remote_url="git@github.com:acme/widget.git")

    vault = tmp_path / "vault"
    wiki = vault / "wiki" / "w"
    for i in range(MAX_SESSIONS + 5):
        d = date.today().isoformat()
        _write(wiki / "sessions" / f"{d}-note-{i:02d}.md", _session_note(repo="acme/widget"))
    monkeypatch.setenv("LORE_ROOT", str(vault))

    result = gather(cwd=repo, repo_path=str(repo))
    assert len(result["sessions"]) == MAX_SESSIONS


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


def test_context_pack_tool_registered_in_schema() -> None:
    from lore_mcp.server import _tool_schema

    names = {t["name"] for t in _tool_schema()}
    assert "lore_context_pack" in names


def test_context_pack_dispatch_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lore_mcp.server import _dispatch

    repo = tmp_path / "repo"
    _init_repo(repo, remote_url="git@github.com:acme/widget.git")
    _no_vault(monkeypatch, tmp_path)

    result = _dispatch("lore_context_pack", {"cwd": str(repo), "repo_path": str(repo)})
    assert result["schema"] == "lore.context_pack/1"
    assert result["repo"] == "acme/widget"
