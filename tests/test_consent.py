"""Tests for classify_state — the 6-state consent machine."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_core.consent import ConsentResult, ConsentState, classify_state
from lore_core.offer import FILENAME, offer_fingerprint, parse_lore_yml
from lore_core.state.attachments import Attachment, AttachmentsFile


@pytest.fixture
def lore_root(tmp_path: Path) -> Path:
    (tmp_path / ".lore").mkdir()
    return tmp_path


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 4, 22, 9, 0, 0, tzinfo=UTC)


def _write_offer(dir_: Path, *, wiki: str = "w", scope: str = "a:b",
                 wiki_source: str | None = None) -> None:
    lines = [f"wiki: {wiki}", f"scope: {scope}"]
    if wiki_source:
        lines.append(f"wiki_source: {wiki_source}")
    (dir_ / FILENAME).write_text("\n".join(lines) + "\n")


def _attach(path: Path, *, wiki: str = "w", scope: str = "a:b",
            now: datetime, fp: str | None = None,
            source: str = "accepted-offer") -> Attachment:
    return Attachment(
        path=path,
        wiki=wiki,
        scope=scope,
        attached_at=now,
        source=source,
        offer_fingerprint=fp,
    )


# ---- UNTRACKED ----

def test_untracked(lore_root: Path, tmp_path: Path) -> None:
    """No offer, no attachment → UNTRACKED."""
    repo = tmp_path / "repo"
    repo.mkdir()
    af = AttachmentsFile(lore_root)
    af.load()
    r = classify_state(repo, af)
    assert r.state is ConsentState.UNTRACKED
    assert r.offer is None
    assert r.repo_root is None


# ---- OFFERED ----

def test_offered(lore_root: Path, tmp_path: Path) -> None:
    """Offer present, no attachment, not declined → OFFERED."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo, wiki="team-alpha", scope="ccat:ds")
    af = AttachmentsFile(lore_root)
    af.load()
    r = classify_state(repo, af)
    assert r.state is ConsentState.OFFERED
    assert r.offer is not None
    assert r.offer.wiki == "team-alpha"
    assert r.repo_root == repo
    assert r.offer_fingerprint is not None


def test_descendant_of_offer_without_inherit_is_untracked(
    lore_root: Path, tmp_path: Path,
) -> None:
    """Issue #24: walk-up no longer auto-attaches descendants. A
    `.lore.yml` without `inherit: true` is inert below its own dir."""
    repo = tmp_path / "repo"
    (repo / "src" / "nested").mkdir(parents=True)
    _write_offer(repo)  # no inherit
    af = AttachmentsFile(lore_root)
    af.load()
    r = classify_state(repo / "src" / "nested", af)
    assert r.state is ConsentState.UNTRACKED


def test_descendant_of_offer_with_inherit_is_offered(
    lore_root: Path, tmp_path: Path,
) -> None:
    """`inherit: true` opts back in to subtree attachment offers."""
    repo = tmp_path / "repo"
    (repo / "src" / "nested").mkdir(parents=True)
    (repo / FILENAME).write_text("wiki: w\nscope: a:b\ninherit: true\n")
    af = AttachmentsFile(lore_root)
    af.load()
    r = classify_state(repo / "src" / "nested", af)
    assert r.state is ConsentState.OFFERED
    assert r.repo_root == repo


# ---- ATTACHED ----

def test_attached(lore_root: Path, tmp_path: Path, now: datetime) -> None:
    """Offer + attachment with matching fingerprint → ATTACHED."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo, wiki="w", scope="a:b")
    offer = parse_lore_yml(repo / FILENAME)
    fp = offer_fingerprint(offer)

    af = AttachmentsFile(lore_root)
    af.load()
    af.add(_attach(repo, wiki="w", scope="a:b", now=now, fp=fp))

    r = classify_state(repo, af)
    assert r.state is ConsentState.ATTACHED


# ---- DORMANT ----

def test_dormant(lore_root: Path, tmp_path: Path) -> None:
    """Offer + decline entry with matching scope → DORMANT."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo, scope="a:b")

    af = AttachmentsFile(lore_root)
    af.load()
    af.decline(repo, "a:b")

    r = classify_state(repo, af)
    assert r.state is ConsentState.DORMANT


def test_dormant_decline_for_different_scope_does_not_suppress(
    lore_root: Path, tmp_path: Path,
) -> None:
    """An old decline for a different scope doesn't suppress a new offer."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo, wiki="new-wiki", scope="new:scope")
    af = AttachmentsFile(lore_root)
    af.load()
    af.decline(repo, "old:scope")     # stale decline, different offer
    r = classify_state(repo, af)
    assert r.state is ConsentState.OFFERED


def test_dormant_survives_content_edit_that_changes_fingerprint(
    lore_root: Path, tmp_path: Path,
) -> None:
    """Editing an offer's `wiki_source` (a fingerprint field) after a
    decline must NOT re-prompt — decline is keyed on (path, scope), not
    on the content fingerprint that changed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo, wiki="w", scope="a:b")
    fp_before = offer_fingerprint(parse_lore_yml(repo / FILENAME))

    af = AttachmentsFile(lore_root)
    af.load()
    af.decline(repo, "a:b")
    r = classify_state(repo, af)
    assert r.state is ConsentState.DORMANT

    # Edit content only — scope unchanged, but wiki_source is a
    # fingerprint field, so the fingerprint DOES change here.
    _write_offer(repo, wiki="w", scope="a:b", wiki_source="git@example.com:x.git")
    fp_after = offer_fingerprint(parse_lore_yml(repo / FILENAME))
    assert fp_before != fp_after  # sanity: the edit is not a no-op

    r2 = classify_state(repo, af)
    assert r2.state is ConsentState.DORMANT


def test_dormant_reprompts_on_scope_change(lore_root: Path, tmp_path: Path) -> None:
    """A changed scope IS a materially different offer — re-asks even
    though the decline for the old scope is still on file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo, wiki="w", scope="a:b")

    af = AttachmentsFile(lore_root)
    af.load()
    af.decline(repo, "a:b")
    assert classify_state(repo, af).state is ConsentState.DORMANT

    _write_offer(repo, wiki="w", scope="a:c")  # scope changed
    r = classify_state(repo, af)
    assert r.state is ConsentState.OFFERED


# ---- MANUAL ----

def test_manual(lore_root: Path, tmp_path: Path, now: datetime) -> None:
    """No offer, but attachment exists → MANUAL."""
    repo = tmp_path / "repo"
    repo.mkdir()
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(_attach(repo, wiki="w", scope="a:b", now=now, source="manual"))
    r = classify_state(repo, af)
    assert r.state is ConsentState.MANUAL
    assert r.offer is None


# ---- DRIFT ----

def test_drift_offer_changed(lore_root: Path, tmp_path: Path, now: datetime) -> None:
    """Attachment fingerprint mismatches current offer → DRIFT."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # Old attachment with stale fingerprint
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(_attach(repo, now=now, fp="sha256:stale"))
    # New offer with a different fingerprint
    _write_offer(repo, wiki="new-wiki", scope="new:scope")

    r = classify_state(repo, af)
    assert r.state is ConsentState.DRIFT


def test_drift_manual_attachment_with_offer(lore_root: Path, tmp_path: Path, now: datetime) -> None:
    """Manual attachment (no fp) + subsequent offer → DRIFT — user should
    be prompted to reconcile the manual choice with the repo's offer."""
    repo = tmp_path / "repo"
    repo.mkdir()
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(_attach(repo, now=now, fp=None, source="manual"))
    _write_offer(repo)
    r = classify_state(repo, af)
    assert r.state is ConsentState.DRIFT


# ---- malformed offer ----

def test_malformed_offer_treated_as_absent(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / FILENAME).write_text("wiki: [bad-yaml\n")
    af = AttachmentsFile(lore_root)
    af.load()
    r = classify_state(repo, af)
    assert r.state is ConsentState.UNTRACKED


# ---- descendants ----

def test_attached_state_from_deep_descendant(
    lore_root: Path, tmp_path: Path, now: datetime,
) -> None:
    repo = tmp_path / "repo"
    (repo / "a" / "b").mkdir(parents=True)
    _write_offer(repo)
    offer = parse_lore_yml(repo / FILENAME)
    fp = offer_fingerprint(offer)
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(_attach(repo, now=now, fp=fp))
    r = classify_state(repo / "a" / "b", af)
    assert r.state is ConsentState.ATTACHED
