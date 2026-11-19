"""Tests for `lore_core.context_pack` — the deterministic `lore_context_pack`
join (PRD 0004).

Relevance is a join on linkage keys (repo, scope, issue/epic) drawn from the
branch name and a connected repo's ADR/PRD homes — never an LLM call, never
an FTS ranking dressed up as a join. Cold-start (no repo, no vault, no scope)
degrades to a well-formed empty pack, never an error.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from lore_core.context_pack import gather


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


def _no_vault(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "no-such-vault"))


# ---------------------------------------------------------------------------
# epic / issue join
# ---------------------------------------------------------------------------


def test_gather_joins_by_epic_from_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, branch="epic/162")
    _no_vault(monkeypatch, tmp_path)

    result = gather(cwd=repo, repo_path=str(repo))
    assert result["focus_issues"] == [162]


def test_gather_issue_param_widens_focus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _no_vault(monkeypatch, tmp_path)

    result = gather(cwd=repo, repo_path=str(repo), issue=180)
    assert 180 in result["focus_issues"]


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
# scope join (via the route: a scope in the person config)
# ---------------------------------------------------------------------------


def test_gather_joins_by_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)  # no remote — repo string is empty

    from conftest import seed_scope

    vault = tmp_path / "vault"
    (vault / ".lore").mkdir(parents=True)
    seed_scope(repo, "w", name="w:s")
    monkeypatch.setenv("LORE_ROOT", str(vault))

    result = gather(cwd=repo, repo_path=str(repo))
    assert result["scope"] == "w:s"
    assert result["wiki"] == "w"


# ---------------------------------------------------------------------------
# cold start — well-formed empty packs, never errors
# ---------------------------------------------------------------------------


def test_gather_cold_start_no_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, remote_url="git@github.com:acme/widget.git")
    _no_vault(monkeypatch, tmp_path)

    result = gather(cwd=repo, repo_path=str(repo))
    assert "error" not in result
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
    assert result["adr"] == []
    assert result["prd"] == []
    assert result["epic_state"] == []


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
    assert "sessions" not in result


def test_gather_returns_no_sessions_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, remote_url="git@github.com:acme/widget.git")
    _no_vault(monkeypatch, tmp_path)

    result = gather(cwd=repo, repo_path=str(repo))
    assert "sessions" not in result


# ---------------------------------------------------------------------------
# intent — lexical rank over title + body, capped (PRD 0024 code mode)
# ---------------------------------------------------------------------------


def test_gather_intent_ranks_doc_naming_the_words_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write(repo / "docs/adr/0001-a.md", "# Session notes\n\nAbout notes.\n")
    _write(repo / "docs/adr/0002-b.md", "# Board ledger\n\nThe board ledger tracks merges.\n")
    _write(repo / "docs/prd/0001-p.md", "# Search\n\nFederated search.\n")
    _no_vault(monkeypatch, tmp_path)

    result = gather(cwd=repo, repo_path=str(repo), intent="board ledger merges")
    assert result["ranked"][0] == "docs/adr/0002-b.md"


def test_gather_intent_caps_ranked_at_eight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    for n in range(12):
        _write(repo / f"docs/adr/{n:04d}-x.md", f"# Widget {n}\n\nwidget body\n")
    _no_vault(monkeypatch, tmp_path)

    result = gather(cwd=repo, repo_path=str(repo), intent="widget")
    assert len(result["ranked"]) == 8
    assert len(result["adr"]) + len(result["prd"]) == 8


def test_gather_without_intent_or_focus_returns_current_adrs_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write(repo / "docs/adr/0001-old.md", "# Old\n\n- Status: Superseded by ADR 0002\n")
    _write(repo / "docs/adr/0002-new.md", "# New\n\n- **Status:** Accepted\n")
    _write(repo / "docs/prd/0001-p.md", "---\nstatus: shipped\n---\n# P\n")
    _write(repo / "docs/prd/0002-q.md", "# Q\n")
    _no_vault(monkeypatch, tmp_path)

    result = gather(cwd=repo, repo_path=str(repo))
    assert result["ranked"] == ["docs/adr/0002-new.md"]
    assert result["prd"] == []


def test_context_pack_tool_takes_intent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lore_mcp.server import _dispatch, _tool_schema

    schema = next(t for t in _tool_schema() if t["name"] == "lore_context_pack")
    assert "intent" in schema["inputSchema"]["properties"]

    repo = tmp_path / "repo"
    _init_repo(repo)
    _write(repo / "docs/adr/0001-a.md", "# Alpha\n")
    _write(repo / "docs/adr/0002-b.md", "# Beta gamma\n")
    _no_vault(monkeypatch, tmp_path)

    result = _dispatch(
        "lore_context_pack", {"cwd": str(repo), "repo_path": str(repo), "intent": "gamma"}
    )
    assert result["ranked"] == ["docs/adr/0002-b.md"]


def test_gather_intent_ranks_over_all_docs_on_a_focus_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, branch="feat/646-x")
    _write(repo / "docs/adr/0001-a.md", "# Board ledger\n\nNo issue reference.\n")
    _no_vault(monkeypatch, tmp_path)

    result = gather(cwd=repo, repo_path=str(repo), intent="ledger")
    assert result["ranked"] == ["docs/adr/0001-a.md"]


def test_gather_intent_ignores_stop_words(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write(repo / "docs/adr/0001-a.md", "# A\n\n" + "the and for with " * 20 + "\n")
    _write(repo / "docs/adr/0002-b.md", "# B\n\nThe board.\n")
    _no_vault(monkeypatch, tmp_path)

    result = gather(cwd=repo, repo_path=str(repo), intent="fix the board for the team")
    assert result["ranked"] == ["docs/adr/0002-b.md"]


def test_adr_body_line_starting_status_deprecated_stays_current(tmp_path):
    from lore_core.context_pack import _is_current

    (tmp_path / "a.md").write_text(
        "---\nstatus: accepted\n---\n# A\n\n- Date: x\n\n## Context\n\nstatus: deprecated here\n"
    )
    assert _is_current(tmp_path, {"path": "a.md", "status": "accepted"})
    (tmp_path / "b.md").write_text("---\nstatus: superseded\n---\n# B\n")
    assert not _is_current(tmp_path, {"path": "b.md", "status": "superseded"})


def test_adr_status_section_decides_currency(tmp_path):
    from lore_core.context_pack import _is_current

    (tmp_path / "a.md").write_text("# A\n\n## Status\n\nSuperseded by ADR 0012.\n\n## Context\n")
    assert not _is_current(tmp_path, {"path": "a.md"})
    (tmp_path / "b.md").write_text(
        "# B\n\n## Status\n\nAccepted.\n\n## Context\n\nstatus: deprecated\n"
    )
    assert _is_current(tmp_path, {"path": "b.md"})


def test_gather_intent_keeps_focus_prd_without_intent_word(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, branch="epic/162")
    _write(repo / "docs/prd/0001-p.md", "# Plan\n\nepic #162 tracked here.\n")
    _write(repo / "docs/adr/0001-a.md", "# Board ledger\n\nNo ref.\n")
    _no_vault(monkeypatch, tmp_path)

    result = gather(cwd=repo, repo_path=str(repo), intent="ledger")
    assert result["ranked"] == ["docs/prd/0001-p.md", "docs/adr/0001-a.md"]


def test_gather_intent_with_focus_caps_ranked_at_eight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, branch="epic/162")
    _write(repo / "docs/prd/0001-p.md", "# Plan\n\nepic #162 tracked here.\n")
    for n in range(12):
        _write(repo / f"docs/adr/{n:04d}-x.md", f"# Widget {n}\n\nwidget body\n")
    _no_vault(monkeypatch, tmp_path)

    result = gather(cwd=repo, repo_path=str(repo), intent="widget")
    assert len(result["ranked"]) == 8
    assert result["ranked"][0] == "docs/prd/0001-p.md"
    assert len(result["adr"]) + len(result["prd"]) == 8


def test_gather_intent_ranks_current_adr_above_superseded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write(repo / "docs/adr/0001-old.md", "# Ledger\n\n- Status: Superseded by ADR 0002\n")
    _write(repo / "docs/adr/0002-new.md", "# Ledger\n\n- Status: Accepted\n")
    _no_vault(monkeypatch, tmp_path)

    result = gather(cwd=repo, repo_path=str(repo), intent="ledger")
    assert result["ranked"][0] == "docs/adr/0002-new.md"


def test_gather_intent_ranks_current_prd_above_shipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write(repo / "docs/prd/0001-a.md", "---\nstatus: shipped\n---\n# Ledger\n")
    _write(repo / "docs/prd/0002-b.md", "# Ledger\n")
    _no_vault(monkeypatch, tmp_path)

    result = gather(cwd=repo, repo_path=str(repo), intent="ledger")
    assert result["ranked"][0] == "docs/prd/0002-b.md"


def test_gather_intent_keeps_focus_prd_when_focus_docs_fill_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, branch="epic/162")
    for n in range(10):
        _write(repo / f"docs/adr/{n:04d}-x.md", f"# X {n}\n\nepic #162 widget.\n")
    _write(repo / "docs/prd/0001-p.md", "# Plan\n\nepic #162 tracked here.\n")
    _no_vault(monkeypatch, tmp_path)

    result = gather(cwd=repo, repo_path=str(repo), intent="widget")
    assert "docs/prd/0001-p.md" in result["ranked"]
    assert len(result["ranked"]) == 8


def test_gather_intent_ranks_current_focus_docs_above_superseded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, branch="epic/162")
    for n in range(5):
        _write(
            repo / f"docs/adr/{n:04d}-old.md",
            f"# Old {n}\n\n## Status\n\nSuperseded by ADR 0009.\n\nepic #162.\n",
        )
    for n in range(5, 10):
        _write(repo / f"docs/adr/{n:04d}-new.md", f"# New {n}\n\nepic #162.\n")
    _no_vault(monkeypatch, tmp_path)

    result = gather(cwd=repo, repo_path=str(repo), intent="widget")
    assert result["ranked"][:5] == [f"docs/adr/{n:04d}-new.md" for n in range(5, 10)]


@pytest.mark.parametrize("number", ["0007", "0008", "0011"])
def test_adrs_that_adr_0012_supersedes_are_not_current(number: str) -> None:
    from lore_core.context_pack import _is_current

    repo = Path(__file__).resolve().parent.parent
    path = next((repo / "docs/adr").glob(f"{number}-*.md")).relative_to(repo)
    assert not _is_current(repo, {"path": str(path)})
