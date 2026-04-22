"""Tests for SessionStart offer-notice emission (`_offer_notice_line`).

Covers the five gate conditions (flag, LORE_ROOT, OFFERED, DRIFT,
other states) and the hook-event logging side-effect.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_cli.hooks import _offer_notice_line
from lore_core.offer import FILENAME, offer_fingerprint, parse_lore_yml
from lore_core.state.attachments import Attachment, AttachmentsFile


@pytest.fixture
def lore_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".lore").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    monkeypatch.setenv("LORE_NEW_STATE", "1")
    return tmp_path


def _write_offer(dir_: Path, *, wiki: str = "team-alpha", scope: str = "ccat:ds") -> None:
    (dir_ / FILENAME).write_text(f"wiki: {wiki}\nscope: {scope}\n")


def _attach(path: Path, *, fp: str | None = None, source: str = "accepted-offer") -> Attachment:
    return Attachment(
        path=path,
        wiki="team-alpha",
        scope="ccat:ds",
        attached_at=datetime(2026, 4, 22, 9, 0, tzinfo=UTC),
        source=source,
        offer_fingerprint=fp,
    )


# ---- gate conditions ----

def test_returns_none_when_flag_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LORE_NEW_STATE", raising=False)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    (tmp_path / ".lore").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo)
    assert _offer_notice_line(repo) is None


def test_returns_none_when_lore_root_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LORE_NEW_STATE", "1")
    monkeypatch.delenv("LORE_ROOT", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo)
    assert _offer_notice_line(repo) is None


def test_returns_none_untracked(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _offer_notice_line(repo) is None


def test_returns_none_attached(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo)
    offer = parse_lore_yml(repo / FILENAME)
    fp = offer_fingerprint(offer)

    af = AttachmentsFile(lore_root)
    af.load()
    af.add(_attach(repo, fp=fp))
    af.save()

    assert _offer_notice_line(repo) is None


def test_returns_none_dormant(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo)
    offer = parse_lore_yml(repo / FILENAME)
    fp = offer_fingerprint(offer)

    af = AttachmentsFile(lore_root)
    af.load()
    af.decline(repo, fp)
    af.save()

    assert _offer_notice_line(repo) is None


def test_returns_none_manual_attachment(lore_root: Path, tmp_path: Path) -> None:
    """Manual attachments (no offer) also have no notice — nothing to offer."""
    repo = tmp_path / "repo"
    repo.mkdir()
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(_attach(repo, fp=None, source="manual"))
    af.save()
    # No .lore.yml written
    assert _offer_notice_line(repo) is None


# ---- OFFERED / DRIFT emit notice ----

def test_emits_notice_on_offered(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo, wiki="team-alpha", scope="ccat:ds")

    notice = _offer_notice_line(repo)
    assert notice is not None
    assert "team-alpha" in notice
    assert "ccat:ds" in notice
    assert "/lore:attach" in notice


def test_emits_notice_on_drift(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(_attach(repo, fp="sha256:stale"))
    af.save()
    _write_offer(repo, wiki="different", scope="new:scope")

    notice = _offer_notice_line(repo)
    assert notice is not None
    # DRIFT message has distinct wording
    assert "changed" in notice
    assert "different" in notice


def test_emits_notice_from_descendant_cwd(lore_root: Path, tmp_path: Path) -> None:
    """cwd inside the repo (not at the root) still triggers the notice."""
    repo = tmp_path / "repo"
    (repo / "src" / "nested").mkdir(parents=True)
    _write_offer(repo)
    notice = _offer_notice_line(repo / "src" / "nested")
    assert notice is not None


# ---- hook-event logging ----

def test_logs_offered_hook_event(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo, wiki="team-alpha", scope="ccat:ds")
    _offer_notice_line(repo)

    events_path = lore_root / ".lore" / "hook-events.jsonl"
    assert events_path.exists()
    records = [json.loads(line) for line in events_path.read_text().splitlines() if line]
    matching = [r for r in records if r.get("event") == "lore-yml-offered"]
    assert len(matching) == 1
    assert matching[0]["outcome"] == "offered"
    assert matching[0]["detail"]["wiki"] == "team-alpha"
    assert matching[0]["detail"]["scope"] == "ccat:ds"


def test_logs_drift_hook_event(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(_attach(repo, fp="sha256:stale"))
    af.save()
    _write_offer(repo, wiki="new-wiki", scope="new:s")
    _offer_notice_line(repo)

    events_path = lore_root / ".lore" / "hook-events.jsonl"
    records = [json.loads(line) for line in events_path.read_text().splitlines() if line]
    matching = [r for r in records if r.get("event") == "lore-yml-offered"]
    assert len(matching) == 1
    assert matching[0]["outcome"] == "drift"
