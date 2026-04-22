"""Tests for scope_resolver — registry-backed longest-prefix match.

Post-Phase-6, ``resolve_scope`` has no walk-up fallback. It either finds
a matching attachment in ``attachments.json`` or returns ``None``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_core.scope_resolver import resolve_scope
from lore_core.state.attachments import Attachment, AttachmentsFile


def _attach(path: Path, *, wiki: str = "w", scope: str = "w:s") -> Attachment:
    return Attachment(
        path=path,
        wiki=wiki,
        scope=scope,
        attached_at=datetime(2026, 4, 22, 9, 0, 0, tzinfo=UTC),
    )


# ---- explicit-attachments path ----

def test_registry_resolves_exact_path(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    af = AttachmentsFile(tmp_path)
    af.load()
    af.add(_attach(repo, wiki="ccat", scope="ccat:ds"))

    result = resolve_scope(repo, af)
    assert result is not None
    assert result.wiki == "ccat"
    assert result.scope == "ccat:ds"
    # Synthetic sentinel so legacy callers of scope.claude_md_path still work
    assert result.claude_md_path == repo / "CLAUDE.md"


def test_registry_resolves_descendant(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    af = AttachmentsFile(tmp_path)
    af.load()
    af.add(_attach(repo, scope="x:y"))

    result = resolve_scope(repo / "src", af)
    assert result is not None
    assert result.scope == "x:y"


def test_registry_returns_none_on_unattached(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    stranger = tmp_path / "stranger"
    stranger.mkdir()
    af = AttachmentsFile(tmp_path)
    af.load()
    assert resolve_scope(stranger, af) is None


def test_registry_most_specific_wins(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    af = AttachmentsFile(tmp_path)
    af.load()
    af.add(_attach(outer, wiki="wo", scope="o"))
    af.add(_attach(inner, wiki="wi", scope="o:i"))

    result = resolve_scope(inner / "deep", af)
    assert result is not None
    assert result.scope == "o:i"


# ---- default (auto-loaded) attachments path ----

def test_resolve_scope_auto_loads_from_lore_root_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".lore").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    af = AttachmentsFile(tmp_path)
    af.load()
    af.add(_attach(repo, wiki="auto-wiki", scope="a:b"))
    af.save()

    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    result = resolve_scope(repo)
    assert result is not None
    assert result.wiki == "auto-wiki"


def test_resolve_scope_returns_none_when_lore_root_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LORE_ROOT", raising=False)
    assert resolve_scope(tmp_path) is None


def test_resolve_scope_returns_none_when_lore_root_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "nonexistent"))
    assert resolve_scope(tmp_path) is None


def test_resolve_scope_dispatches_to_registry_when_attachments_provided(
    tmp_path: Path,
) -> None:
    (tmp_path / ".lore").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    af = AttachmentsFile(tmp_path)
    af.load()
    af.add(_attach(repo, wiki="registry-wiki", scope="r:a"))

    result = resolve_scope(repo, attachments=af)
    assert result is not None
    assert result.wiki == "registry-wiki"
