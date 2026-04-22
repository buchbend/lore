"""Tests for the new `lore attach {accept,decline,manual,offer}` subcommands."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lore_cli.__main__ import app
from lore_core.offer import FILENAME, offer_fingerprint, parse_lore_yml
from lore_core.state.attachments import Attachment, AttachmentsFile
from lore_core.state.scopes import ScopesFile

runner = CliRunner(mix_stderr=False)


@pytest.fixture
def lore_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".lore").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    return tmp_path


def _write_offer(dir_: Path, *, wiki: str = "team-alpha", scope: str = "ccat:ds",
                 wiki_source: str | None = None) -> None:
    lines = [f"wiki: {wiki}", f"scope: {scope}"]
    if wiki_source:
        lines.append(f"wiki_source: {wiki_source}")
    (dir_ / FILENAME).write_text("\n".join(lines) + "\n")


# ---- accept ----

def test_accept_happy_path(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo)
    result = runner.invoke(app, ["attach", "accept", "--cwd", str(repo)])
    assert result.exit_code == 0, result.stdout + result.stderr

    af = AttachmentsFile(lore_root); af.load()
    rows = af.all()
    assert len(rows) == 1
    assert rows[0].wiki == "team-alpha"
    assert rows[0].scope == "ccat:ds"
    assert rows[0].source == "accepted-offer"
    assert rows[0].offer_fingerprint is not None

    sf = ScopesFile(lore_root); sf.load()
    assert sf.get("ccat") is not None
    assert sf.get("ccat:ds") is not None
    assert sf.resolve_wiki("ccat:ds") == "team-alpha"


def test_accept_no_offer_fails(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(app, ["attach", "accept", "--cwd", str(repo)])
    assert result.exit_code == 1


def test_accept_after_decline_fails(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo)
    runner.invoke(app, ["attach", "decline", "--cwd", str(repo)])
    result = runner.invoke(app, ["attach", "accept", "--cwd", str(repo)])
    assert result.exit_code == 1


def test_accept_scope_conflict_surfaces_options(lore_root: Path, tmp_path: Path) -> None:
    """Root `ccat` already assigned to one wiki; new offer with `ccat:...`
    but different wiki should fail with a helpful conflict message."""
    sf = ScopesFile(lore_root); sf.load()
    sf.ingest_chain("ccat:existing", "wiki-a")
    sf.save()

    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo, wiki="wiki-b", scope="ccat:new")
    result = runner.invoke(app, ["attach", "accept", "--cwd", str(repo)])
    assert result.exit_code == 1
    assert "Scope conflict" in result.stderr or "conflict" in result.stderr.lower()


# ---- decline ----

def test_decline_records_fingerprint(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_offer(repo)
    offer = parse_lore_yml(repo / FILENAME)
    fp = offer_fingerprint(offer)

    result = runner.invoke(app, ["attach", "decline", "--cwd", str(repo)])
    assert result.exit_code == 0

    af = AttachmentsFile(lore_root); af.load()
    assert af.is_declined(repo, fp)


def test_decline_no_offer_fails(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(app, ["attach", "decline", "--cwd", str(repo)])
    assert result.exit_code == 1


# ---- manual ----

def test_manual_attach(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        app,
        ["attach", "manual", "--wiki", "w", "--scope", "a:b", "--cwd", str(repo)],
    )
    assert result.exit_code == 0

    af = AttachmentsFile(lore_root); af.load()
    rows = af.all()
    assert len(rows) == 1
    assert rows[0].source == "manual"
    assert rows[0].offer_fingerprint is None

    sf = ScopesFile(lore_root); sf.load()
    assert sf.get("a:b") is not None


def test_manual_scope_conflict(lore_root: Path, tmp_path: Path) -> None:
    sf = ScopesFile(lore_root); sf.load()
    sf.ingest_chain("ccat:x", "wiki-a")
    sf.save()

    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        app,
        ["attach", "manual", "--wiki", "wiki-b", "--scope", "ccat:y", "--cwd", str(repo)],
    )
    assert result.exit_code == 1


# ---- offer ----

def test_offer_writes_lore_yml(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        app,
        ["attach", "offer", "--wiki", "team-beta", "--scope", "proj:mod",
         "--cwd", str(repo), "--wiki-source", "git@github.com:team/beta-wiki.git"],
    )
    assert result.exit_code == 0

    offer_path = repo / FILENAME
    assert offer_path.exists()
    offer = parse_lore_yml(offer_path)
    assert offer is not None
    assert offer.wiki == "team-beta"
    assert offer.scope == "proj:mod"
    assert offer.wiki_source == "git@github.com:team/beta-wiki.git"


def test_offer_refuses_overwrite(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / FILENAME).write_text("wiki: existing\nscope: e:s\n")
    result = runner.invoke(
        app,
        ["attach", "offer", "--wiki", "new", "--scope", "n:s", "--cwd", str(repo)],
    )
    assert result.exit_code == 1
    # Original preserved
    assert (repo / FILENAME).read_text() == "wiki: existing\nscope: e:s\n"


def test_offer_overwrite_with_force(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / FILENAME).write_text("wiki: old\nscope: o:s\n")
    result = runner.invoke(
        app,
        ["attach", "offer", "--wiki", "new", "--scope", "n:s", "--cwd", str(repo), "--force"],
    )
    assert result.exit_code == 0
    offer = parse_lore_yml(repo / FILENAME)
    assert offer.wiki == "new"
