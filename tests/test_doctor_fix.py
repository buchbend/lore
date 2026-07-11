"""Tests for `lore doctor --fix` — state repair, path migration.

CRITICAL invariant under test throughout: attachments.json is the
non-regenerable consent record (docs/architecture/state.md) — no repair
here may ever drop an attachment row, only rebuild scopes.json or update
fields in place after explicit consent.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lore_cli.__main__ import app
from lore_core.state.attachments import Attachment, AttachmentsFile
from lore_core.state.scopes import ScopesFile
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def lore_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".lore").mkdir()
    (tmp_path / "wiki" / "private").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    return tmp_path


def _attach(
    path: Path, *, wiki: str = "private", scope: str = "lore:a", fp: str | None = None
) -> Attachment:
    return Attachment(
        path=path,
        wiki=wiki,
        scope=scope,
        attached_at=datetime(2026, 4, 22, 9, 0, tzinfo=UTC),
        source="manual",
        offer_fingerprint=fp,
    )


def _checks(result) -> dict[str, dict]:
    payload = json.loads(result.stdout)
    return {c["check"]: c for c in payload["data"]["checks"]}


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    """Strip ANSI escapes — Rich's auto-highlighter splits printed text
    (e.g. a scope ID) across multiple style spans, so a raw substring
    check against styled output can miss even though the text is there."""
    return _ANSI.sub("", text)


# ---------------------------------------------------------------------------
# AC1 — rebuild scopes.json from accepted attachments
# ---------------------------------------------------------------------------


def test_fix_rebuilds_scopes_from_corrupt_json(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(_attach(repo, wiki="private", scope="lore:a"))
    af.save()

    (lore_root / ".lore" / "scopes.json").write_text("{not valid json")

    # Red: attachments check fails because the scope can't be found in a
    # corrupt (empty-after-parse) scopes.json.
    before = runner.invoke(app, ["doctor", "--json"])
    assert _checks(before)["attachments"]["ok"] is False

    result = runner.invoke(app, ["doctor", "--fix", "--yes", "--json"])
    assert result.exit_code == 0, result.stdout

    # Revalidate with a plain (non-fix) run.
    after = runner.invoke(app, ["doctor", "--json"])
    assert after.exit_code == 0, after.stdout
    assert _checks(after)["attachments"]["ok"] is True

    sf = ScopesFile(lore_root)
    sf.load()
    assert sf.get("lore:a") is not None
    assert sf.resolve_wiki("lore:a") == "private"


def test_fix_rebuild_scopes_is_declinable(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(_attach(repo, wiki="private", scope="lore:a"))
    af.save()

    (lore_root / ".lore" / "scopes.json").write_text("{not valid json")

    result = runner.invoke(app, ["doctor", "--fix", "--json"], input="n\n")
    # The underlying corruption is declined, not fixed — the run still
    # legitimately fails on the pre-existing "not in scopes.json" issue.
    assert result.exit_code == 1, result.stdout

    # Declined — scopes.json must be untouched (still the original corrupt bytes).
    assert (lore_root / ".lore" / "scopes.json").read_text() == "{not valid json"


def test_fix_rebuild_scopes_prints_diff_before_consent(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(_attach(repo, wiki="private", scope="lore:a:b"))
    af.save()

    result = runner.invoke(app, ["doctor", "--fix"], input="n\n")
    plain = _plain(result.stdout)
    assert "lore:a:b" in plain
    assert "Apply?" in plain


# ---------------------------------------------------------------------------
# AC2 — fingerprint re-stamp for drifted offers
# ---------------------------------------------------------------------------


def _seed_drifted_offer(lore_root: Path, repo: Path) -> str:
    """Attachment accepted under one offer; `.lore.yml` since edited to a
    new scope. Returns the stale fingerprint stored on the attachment."""
    from lore_core.offer import Offer, offer_fingerprint

    repo.mkdir()
    old_offer = Offer(wiki="private", scope="lore:old")
    old_fp = offer_fingerprint(old_offer)

    af = AttachmentsFile(lore_root)
    af.load()
    af.add(_attach(repo, wiki="private", scope="lore:old", fp=old_fp))
    af.save()

    sf = ScopesFile(lore_root)
    sf.load()
    sf.ingest_chain("lore:old", "private")
    sf.save()

    (repo / ".lore.yml").write_text("wiki: private\nscope: lore:new\n")
    return old_fp


def test_fix_restamps_drifted_fingerprint_and_updates_scope(
    lore_root: Path, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    old_fp = _seed_drifted_offer(lore_root, repo)

    before = runner.invoke(app, ["doctor", "--json"])
    assert "drift" in _checks(before)["attachments"]["message"]

    result = runner.invoke(app, ["doctor", "--fix", "--yes"])
    assert result.exit_code == 0, result.stdout
    assert "lore:old" in result.stdout and "lore:new" in result.stdout

    af = AttachmentsFile(lore_root)
    af.load()
    entries = af.all()
    assert len(entries) == 1, "re-stamp must not drop or duplicate the consent record"
    assert entries[0].scope == "lore:new"
    assert entries[0].offer_fingerprint != old_fp

    after = runner.invoke(app, ["doctor", "--json"])
    assert _checks(after)["attachments"]["ok"] is True


def test_fix_restamp_is_declinable(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    old_fp = _seed_drifted_offer(lore_root, repo)

    runner.invoke(app, ["doctor", "--fix"], input="n\n")

    af = AttachmentsFile(lore_root)
    af.load()
    entries = af.all()
    assert len(entries) == 1, "decline must not drop the consent record"
    assert entries[0].offer_fingerprint == old_fp
    assert entries[0].scope == "lore:old"


# ---------------------------------------------------------------------------
# AC3 — vault/repo path migration
# ---------------------------------------------------------------------------


def test_fix_migrates_attachment_path_prefix(lore_root: Path, tmp_path: Path) -> None:
    old_home = tmp_path / "old_home"
    new_home = tmp_path / "new_home"
    (new_home / "repo").mkdir(parents=True)  # the repo now lives here

    af = AttachmentsFile(lore_root)
    af.load()
    af.add(_attach(old_home / "repo", wiki="private", scope="lore:a"))
    af.save()
    sf = ScopesFile(lore_root)
    sf.load()
    sf.ingest_chain("lore:a", "private")
    sf.save()

    before = runner.invoke(app, ["doctor", "--json"])
    assert "missing on disk" in _checks(before)["attachments"]["message"]

    result = runner.invoke(
        app,
        [
            "doctor",
            "--fix",
            "--yes",
            "--migrate-path-from",
            str(old_home),
            "--migrate-path-to",
            str(new_home),
        ],
    )
    assert result.exit_code == 0, result.stdout

    af = AttachmentsFile(lore_root)
    af.load()
    entries = af.all()
    assert len(entries) == 1, "migration must not drop the consent record"
    assert entries[0].path == (new_home / "repo").resolve()

    after = runner.invoke(app, ["doctor", "--json"])
    assert _checks(after)["attachments"]["ok"] is True


def test_fix_migrate_path_no_match_is_noop(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(_attach(repo, wiki="private", scope="lore:a"))
    af.save()
    sf = ScopesFile(lore_root)
    sf.load()
    sf.ingest_chain("lore:a", "private")
    sf.save()

    result = runner.invoke(
        app,
        [
            "doctor",
            "--fix",
            "--yes",
            "--migrate-path-from",
            str(tmp_path / "unrelated"),
            "--migrate-path-to",
            str(tmp_path / "also-unrelated"),
        ],
    )
    assert result.exit_code == 0, result.stdout

    af = AttachmentsFile(lore_root)
    af.load()
    assert af.all()[0].path == repo.resolve()


# ---------------------------------------------------------------------------
# AC5 — plain `doctor` (no --fix) never writes
# ---------------------------------------------------------------------------


def test_doctor_without_fix_writes_nothing_even_with_repairable_issues(
    lore_root: Path, tmp_path: Path
) -> None:
    """Same broken-state fixtures as the --fix tests above, but run
    without --fix: attachments.json and scopes.json — the two files repair
    touches — must be byte-for-byte unchanged.

    Scoped to those two files rather than all of `.lore/`: hook-events.jsonl
    / `.lore/drain` are telemetry the SessionStart hook probe writes on
    every invocation regardless of doctor — a pre-existing, intentional
    always-append behaviour (docs/architecture/state.md), not part of the
    no-write guarantee this AC covers.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(_attach(repo, wiki="private", scope="lore:a"))
    af.save()
    (lore_root / ".lore" / "scopes.json").write_text("{not valid json")

    attachments_path = lore_root / ".lore" / "attachments.json"
    scopes_path = lore_root / ".lore" / "scopes.json"
    before = (attachments_path.read_bytes(), scopes_path.read_bytes())

    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 1  # the corrupt-registry issue still fails the run

    after = (attachments_path.read_bytes(), scopes_path.read_bytes())
    assert before == after, (
        "plain `doctor` (no --fix) must never write attachments.json/scopes.json"
    )


# ---------------------------------------------------------------------------
# AC4b — attachments integrity flags a wiki/scope-registry mismatch
# ---------------------------------------------------------------------------


def test_attachments_check_flags_wiki_scope_registry_mismatch(
    lore_root: Path, tmp_path: Path
) -> None:
    (lore_root / "wiki" / "other").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()

    af = AttachmentsFile(lore_root)
    af.load()
    # Attachment claims wiki=other, but the scope registry says lore:a -> private.
    af.add(_attach(repo, wiki="other", scope="lore:a"))
    af.save()
    sf = ScopesFile(lore_root)
    sf.load()
    sf.ingest_chain("lore:a", "private")
    sf.save()

    result = runner.invoke(app, ["doctor", "--json"])
    checks = _checks(result)
    assert checks["attachments"]["ok"] is False
    assert "other" in checks["attachments"]["message"]
    assert "private" in checks["attachments"]["message"]
