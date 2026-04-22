"""Tests for AttachmentsFile: load/save roundtrip, longest-prefix match,
CRUD, decline tracking, fingerprinting."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_core.state.attachments import (
    Attachment,
    AttachmentsFile,
    fingerprint_of,
)


@pytest.fixture
def lore_root(tmp_path: Path) -> Path:
    (tmp_path / ".lore").mkdir()
    return tmp_path


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 4, 22, 9, 0, 0, tzinfo=UTC)


def _attach(path: Path, *, wiki: str = "w", scope: str = "w:s",
            source: str = "manual", now: datetime | None = None,
            fp: str | None = None) -> Attachment:
    return Attachment(
        path=path,
        wiki=wiki,
        scope=scope,
        attached_at=now or datetime(2026, 4, 22, 9, 0, 0, tzinfo=UTC),
        source=source,
        offer_fingerprint=fp,
    )


def test_empty_file_load_is_empty(lore_root: Path) -> None:
    af = AttachmentsFile(lore_root)
    af.load()
    assert af.all() == []


def test_add_save_reload_roundtrip(lore_root: Path, tmp_path: Path, now: datetime) -> None:
    af = AttachmentsFile(lore_root)
    af.load()
    repo = tmp_path / "repo"
    repo.mkdir()
    af.add(_attach(repo, wiki="ccat", scope="ccat:ds", now=now, fp="sha256:abc"))
    af.save()

    af2 = AttachmentsFile(lore_root)
    af2.load()
    got = af2.all()
    assert len(got) == 1
    assert got[0].path == repo.resolve()
    assert got[0].wiki == "ccat"
    assert got[0].scope == "ccat:ds"
    assert got[0].offer_fingerprint == "sha256:abc"
    assert got[0].attached_at == now


def test_add_is_upsert_by_path(lore_root: Path, tmp_path: Path) -> None:
    af = AttachmentsFile(lore_root)
    af.load()
    repo = tmp_path / "repo"
    repo.mkdir()
    af.add(_attach(repo, wiki="w1", scope="a:1"))
    af.add(_attach(repo, wiki="w2", scope="a:2"))
    assert len(af.all()) == 1
    assert af.all()[0].wiki == "w2"
    assert af.all()[0].scope == "a:2"


def test_remove_returns_true_when_present(lore_root: Path, tmp_path: Path) -> None:
    af = AttachmentsFile(lore_root)
    af.load()
    repo = tmp_path / "repo"
    repo.mkdir()
    af.add(_attach(repo))
    assert af.remove(repo) is True
    assert af.all() == []


def test_remove_returns_false_when_absent(lore_root: Path, tmp_path: Path) -> None:
    af = AttachmentsFile(lore_root)
    af.load()
    assert af.remove(tmp_path / "missing") is False


def test_longest_prefix_match_exact(lore_root: Path, tmp_path: Path) -> None:
    af = AttachmentsFile(lore_root)
    af.load()
    repo = tmp_path / "repo"
    repo.mkdir()
    af.add(_attach(repo, wiki="w", scope="a:b"))
    hit = af.longest_prefix_match(repo)
    assert hit is not None
    assert hit.scope == "a:b"


def test_longest_prefix_match_descendant(lore_root: Path, tmp_path: Path) -> None:
    af = AttachmentsFile(lore_root)
    af.load()
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    af.add(_attach(repo, scope="p:q"))
    hit = af.longest_prefix_match(repo / "src")
    assert hit is not None
    assert hit.scope == "p:q"


def test_longest_prefix_match_returns_most_specific(lore_root: Path, tmp_path: Path) -> None:
    """Two attachments; nested one must win when cwd is inside both."""
    af = AttachmentsFile(lore_root)
    af.load()
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    af.add(_attach(outer, wiki="wo", scope="o"))
    af.add(_attach(inner, wiki="wi", scope="o:i"))
    hit = af.longest_prefix_match(inner / "deep")
    assert hit is not None
    assert hit.scope == "o:i"


def test_longest_prefix_match_no_match(lore_root: Path, tmp_path: Path) -> None:
    af = AttachmentsFile(lore_root)
    af.load()
    other = tmp_path / "other"
    other.mkdir()
    af.add(_attach(other))
    stranger = tmp_path / "stranger"
    stranger.mkdir()
    assert af.longest_prefix_match(stranger) is None


def test_longest_prefix_match_on_nonexistent_cwd(lore_root: Path, tmp_path: Path) -> None:
    """cwd that doesn't exist still resolves to an absolute path and may match."""
    af = AttachmentsFile(lore_root)
    af.load()
    repo = tmp_path / "repo"
    repo.mkdir()
    af.add(_attach(repo))
    ghost = repo / "deleted" / "subdir"
    hit = af.longest_prefix_match(ghost)
    assert hit is not None


def test_decline_and_is_declined(lore_root: Path, tmp_path: Path) -> None:
    af = AttachmentsFile(lore_root)
    af.load()
    p = tmp_path / "repo"
    p.mkdir()
    af.decline(p, "sha256:abc")
    assert af.is_declined(p, "sha256:abc") is True
    assert af.is_declined(p, "sha256:xyz") is False


def test_decline_is_upsert(lore_root: Path, tmp_path: Path) -> None:
    af = AttachmentsFile(lore_root)
    af.load()
    p = tmp_path / "repo"
    p.mkdir()
    af.decline(p, "sha256:abc")
    af.decline(p, "sha256:abc")  # same key — no duplicate
    af.save()

    af2 = AttachmentsFile(lore_root)
    af2.load()
    # Different fingerprint doesn't invalidate the first
    assert af2.is_declined(p, "sha256:abc") is True


def test_decline_survives_save_load(lore_root: Path, tmp_path: Path) -> None:
    af = AttachmentsFile(lore_root)
    af.load()
    p = tmp_path / "repo"
    p.mkdir()
    af.decline(p, "sha256:foo")
    af.save()

    af2 = AttachmentsFile(lore_root)
    af2.load()
    assert af2.is_declined(p, "sha256:foo") is True


def test_get_exact_match(lore_root: Path, tmp_path: Path) -> None:
    af = AttachmentsFile(lore_root)
    af.load()
    repo = tmp_path / "repo"
    repo.mkdir()
    af.add(_attach(repo, scope="x:y"))
    assert af.get(repo) is not None
    assert af.get(tmp_path / "other") is None


def test_get_descendant_is_none(lore_root: Path, tmp_path: Path) -> None:
    """get() is exact match — use longest_prefix_match for descendants."""
    af = AttachmentsFile(lore_root)
    af.load()
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    af.add(_attach(repo))
    assert af.get(repo / "sub") is None


def test_fingerprint_determinism() -> None:
    a = {"wiki": "x", "scope": "a:b", "wiki_source": "url"}
    b = {"scope": "a:b", "wiki_source": "url", "wiki": "x"}
    assert fingerprint_of(a) == fingerprint_of(b)


def test_fingerprint_differs_on_content() -> None:
    a = fingerprint_of({"wiki": "x", "scope": "a:b"})
    b = fingerprint_of({"wiki": "x", "scope": "a:c"})
    assert a != b


def test_load_tolerates_malformed_json(lore_root: Path) -> None:
    """Corrupt file → load as empty, do not raise."""
    (lore_root / ".lore" / "attachments.json").write_text("{not json")
    af = AttachmentsFile(lore_root)
    af.load()
    assert af.all() == []


def test_save_creates_parent_dir(tmp_path: Path) -> None:
    """Save works even when .lore/ doesn't yet exist (atomic_write_text
    creates it)."""
    af = AttachmentsFile(tmp_path)   # no .lore/ made by fixture
    af.load()
    repo = tmp_path / "repo"
    repo.mkdir()
    af.add(_attach(repo))
    af.save()
    assert (tmp_path / ".lore" / "attachments.json").exists()
