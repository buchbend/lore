"""Tests for scope_resolver.

Covers both the legacy CLAUDE.md walk-up path and the new registry-backed
``resolve_scope_via_registry`` path. Legacy tests remain until Phase 6
retires the walk-up.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from lore_core.scope_resolver import resolve_scope, resolve_scope_via_registry
from lore_core.state.attachments import Attachment, AttachmentsFile
from lore_core.types import Scope


def make_claude_md(path: Path, content: str) -> None:
    """Helper to write a CLAUDE.md file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / "CLAUDE.md").write_text(content)


def test_resolve_finds_direct_parent_claude_md(tmp_path: Path) -> None:
    """tmp_path/CLAUDE.md with valid ## Lore block; cwd=tmp_path."""
    lore_block = """# Some header

## Lore

<!-- managed by /lore:attach -->

- wiki: private
- scope: test
- backend: github
"""
    make_claude_md(tmp_path / "CLAUDE.md", lore_block)

    result = resolve_scope(tmp_path)
    assert result is not None
    assert isinstance(result, Scope)
    assert result.wiki == "private"
    assert result.scope == "test"
    assert result.backend == "github"
    assert result.claude_md_path == tmp_path / "CLAUDE.md"


def test_resolve_walks_multiple_levels(tmp_path: Path) -> None:
    """tmp_path/CLAUDE.md exists, cwd is tmp_path/a/b/c/."""
    lore_block = """# Root header

## Lore

<!-- managed by /lore:attach -->

- wiki: shared
- scope: lore:inbox
- backend: none
"""
    make_claude_md(tmp_path / "CLAUDE.md", lore_block)

    deep_cwd = tmp_path / "a" / "b" / "c"
    deep_cwd.mkdir(parents=True)

    result = resolve_scope(deep_cwd)
    assert result is not None
    assert result.wiki == "shared"
    assert result.scope == "lore:inbox"
    assert result.backend == "none"
    assert result.claude_md_path == tmp_path / "CLAUDE.md"


def test_resolve_returns_none_when_no_attach(tmp_path: Path) -> None:
    """tmp_path/CLAUDE.md exists but has no ## Lore block; returns None."""
    no_lore_block = """# Some header

## Other section

This is not a Lore block.
"""
    make_claude_md(tmp_path / "CLAUDE.md", no_lore_block)

    result = resolve_scope(tmp_path)
    assert result is None


def test_resolve_nearest_attach_wins(tmp_path: Path) -> None:
    """Both tmp_path and tmp_path/sub have ## Lore blocks; nearest wins."""
    outer_block = """# Outer

## Lore

<!-- managed by /lore:attach -->

- wiki: outer_wiki
- scope: outer
"""
    inner_block = """# Inner

## Lore

<!-- managed by /lore:attach -->

- wiki: inner_wiki
- scope: inner
"""
    make_claude_md(tmp_path / "CLAUDE.md", outer_block)

    sub_dir = tmp_path / "sub"
    sub_dir.mkdir()
    make_claude_md(sub_dir / "CLAUDE.md", inner_block)

    result = resolve_scope(sub_dir)
    assert result is not None
    assert result.wiki == "inner_wiki"
    assert result.scope == "inner"
    assert result.claude_md_path == sub_dir / "CLAUDE.md"


def test_resolve_respects_max_depth(tmp_path: Path) -> None:
    """CLAUDE.md many levels up; max_depth=2 can't reach it; returns None."""
    lore_block = """# Top level

## Lore

<!-- managed by /lore:attach -->

- wiki: deep
- scope: far
"""
    make_claude_md(tmp_path / "CLAUDE.md", lore_block)

    # Create a deep directory: a/b/c/d = 4 levels down
    deep_cwd = tmp_path / "a" / "b" / "c" / "d"
    deep_cwd.mkdir(parents=True)

    # max_depth=2 should only check up 2 levels, not enough to reach tmp_path
    result = resolve_scope(deep_cwd, max_depth=2)
    assert result is None


def test_resolve_handles_cwd_at_fs_root(tmp_path: Path) -> None:
    """Pass a root-like path; must not loop forever; returns None cleanly."""
    # Use the root directory directly; it won't have a Lore block
    # We just ensure it doesn't hang or error
    result = resolve_scope(Path("/"), max_depth=2)
    assert result is None


# ---- Registry-backed path (new in Phase 1) ----

def _attach(path: Path, *, wiki: str = "w", scope: str = "w:s") -> Attachment:
    return Attachment(
        path=path,
        wiki=wiki,
        scope=scope,
        attached_at=datetime(2026, 4, 22, 9, 0, 0, tzinfo=UTC),
    )


def test_registry_resolves_exact_path(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    af = AttachmentsFile(tmp_path)
    af.load()
    af.add(_attach(repo, wiki="ccat", scope="ccat:ds"))

    result = resolve_scope_via_registry(repo, af)
    assert result is not None
    assert result.wiki == "ccat"
    assert result.scope == "ccat:ds"
    # claude_md_path is a synthetic sentinel under the attached path
    assert result.claude_md_path == repo / "CLAUDE.md"


def test_registry_resolves_descendant(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    af = AttachmentsFile(tmp_path)
    af.load()
    af.add(_attach(repo, scope="x:y"))

    result = resolve_scope_via_registry(repo / "src", af)
    assert result is not None
    assert result.scope == "x:y"


def test_registry_returns_none_on_unattached(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    stranger = tmp_path / "stranger"
    stranger.mkdir()
    af = AttachmentsFile(tmp_path)
    af.load()
    assert resolve_scope_via_registry(stranger, af) is None


def test_registry_most_specific_wins(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    af = AttachmentsFile(tmp_path)
    af.load()
    af.add(_attach(outer, wiki="wo", scope="o"))
    af.add(_attach(inner, wiki="wi", scope="o:i"))

    result = resolve_scope_via_registry(inner / "deep", af)
    assert result is not None
    assert result.scope == "o:i"


def test_resolve_scope_dispatches_to_registry_when_attachments_provided(
    tmp_path: Path,
) -> None:
    """resolve_scope(cwd, attachments=af) uses the registry, not walk-up."""
    (tmp_path / ".lore").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    # Deliberately DO NOT write a CLAUDE.md; only the registry knows about this repo.
    af = AttachmentsFile(tmp_path)
    af.load()
    af.add(_attach(repo, wiki="registry-wiki", scope="r:a"))

    result = resolve_scope(repo, attachments=af)
    assert result is not None
    assert result.wiki == "registry-wiki"


def test_resolve_scope_falls_back_to_walkup_when_no_attachments(tmp_path: Path) -> None:
    """resolve_scope(cwd) without attachments still walks up for CLAUDE.md."""
    block = """## Lore

- wiki: legacy
- scope: a:b
"""
    (tmp_path / "CLAUDE.md").write_text(block)
    result = resolve_scope(tmp_path)
    assert result is not None
    assert result.wiki == "legacy"
