"""``resolve_scope`` is a shim over ``resolve_route`` (PRD 0016)."""

from __future__ import annotations

from pathlib import Path

from conftest import seed_scope
from lore_core.scope_resolver import resolve_scope


def test_shim_returns_the_route_as_a_scope(tmp_path: Path) -> None:
    repo = tmp_path / "git" / "dc"
    repo.mkdir(parents=True)
    seed_scope(tmp_path / "git", "ccat", name="ccatobs")

    scope = resolve_scope(repo)

    assert scope is not None
    assert (scope.wiki, scope.scope) == ("ccat", "ccatobs")
    assert scope.claude_md_path == repo / "CLAUDE.md"


def test_shim_returns_none_when_no_wiki_routes_the_cwd(tmp_path: Path) -> None:
    assert resolve_scope(tmp_path) is None
