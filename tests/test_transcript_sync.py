"""Tests for lore_core.transcript_sync — mirror transcripts into wikis."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry
from lore_core.state.attachments import Attachment, AttachmentsFile
from lore_core.transcript_sync import (
    GitignoreNegationError,
    SyncResult,
    _copy_transcript_atomically,
    _ensure_transcripts_gitignored,
    sync_transcripts,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _attach(lore_root: Path, cwd: Path, wiki: str = "mywiki", scope: str = "mywiki:lore"):
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(
        Attachment(
            path=cwd, wiki=wiki, scope=scope,
            attached_at=datetime.now(UTC), source="manual",
        )
    )
    af.save()


def _seed_ledger_entry(
    lore_root: Path,
    *,
    transcript_id: str,
    src_path: Path,
    directory: Path,
    integration: str = "claude-code",
    orphan: bool = False,
) -> None:
    ledger = TranscriptLedger(lore_root)
    ledger.upsert(
        TranscriptLedgerEntry(
            integration=integration,
            transcript_id=transcript_id,
            path=src_path,
            directory=directory,
            digested_hash=None,
            digested_index_hint=None,
            synthesised_hash=None,
            last_mtime=datetime.now(UTC),
            curator_a_run=None,
            noteworthy=None,
            session_note=None,
            orphan=orphan,
        )
    )


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(ln) + "\n" for ln in lines))


# ---------------------------------------------------------------------------
# _ensure_transcripts_gitignored
# ---------------------------------------------------------------------------


def test_ensure_gitignored_creates_file_when_absent(tmp_path):
    wiki = tmp_path / "wiki" / "w"
    _ensure_transcripts_gitignored(wiki)
    gi = (wiki / ".gitignore").read_text()
    assert ".transcripts/" in gi.splitlines()


def test_ensure_gitignored_appends_when_pattern_missing(tmp_path):
    wiki = tmp_path / "wiki" / "w"
    wiki.mkdir(parents=True)
    (wiki / ".gitignore").write_text("*.pyc\nnode_modules/\n")
    _ensure_transcripts_gitignored(wiki)
    gi = (wiki / ".gitignore").read_text()
    lines = gi.splitlines()
    assert "*.pyc" in lines
    assert "node_modules/" in lines
    assert ".transcripts/" in lines


def test_ensure_gitignored_idempotent_when_already_present(tmp_path):
    wiki = tmp_path / "wiki" / "w"
    wiki.mkdir(parents=True)
    (wiki / ".gitignore").write_text("*.pyc\n.transcripts/\n")
    before = (wiki / ".gitignore").read_text()
    _ensure_transcripts_gitignored(wiki)
    assert (wiki / ".gitignore").read_text() == before


def test_ensure_gitignored_matches_line_equality_not_substring(tmp_path):
    """`.transcripts_backup` must NOT count as covering `.transcripts/`."""
    wiki = tmp_path / "wiki" / "w"
    wiki.mkdir(parents=True)
    (wiki / ".gitignore").write_text(".transcripts_backup\n")
    _ensure_transcripts_gitignored(wiki)
    lines = (wiki / ".gitignore").read_text().splitlines()
    assert ".transcripts_backup" in lines
    assert ".transcripts/" in lines  # our line still appended


def test_ensure_gitignored_aborts_on_negation_pattern(tmp_path):
    wiki = tmp_path / "wiki" / "w"
    wiki.mkdir(parents=True)
    (wiki / ".gitignore").write_text(".transcripts/\n!.transcripts/keepme.jsonl\n")
    with pytest.raises(GitignoreNegationError):
        _ensure_transcripts_gitignored(wiki)


def test_ensure_gitignored_adds_missing_trailing_newline(tmp_path):
    wiki = tmp_path / "wiki" / "w"
    wiki.mkdir(parents=True)
    (wiki / ".gitignore").write_text("*.pyc")  # no trailing newline
    _ensure_transcripts_gitignored(wiki)
    text = (wiki / ".gitignore").read_text()
    assert text == "*.pyc\n.transcripts/\n"


def test_ensure_gitignored_accepts_alternative_spellings(tmp_path):
    wiki = tmp_path / "wiki" / "w"
    wiki.mkdir(parents=True)
    (wiki / ".gitignore").write_text(".transcripts\n")  # no trailing slash
    before = (wiki / ".gitignore").read_text()
    _ensure_transcripts_gitignored(wiki)
    # Recognized as already covering — no duplicate line added.
    assert (wiki / ".gitignore").read_text() == before


# ---------------------------------------------------------------------------
# _copy_transcript_atomically
# ---------------------------------------------------------------------------


def test_copy_transcript_copies_full_file(tmp_path):
    src = tmp_path / "src.jsonl"
    dst = tmp_path / "dst.jsonl"
    _write_jsonl(src, [{"a": 1}, {"b": 2}])
    _copy_transcript_atomically(src, dst)
    assert dst.read_text() == src.read_text()


def test_copy_transcript_truncates_partial_final_line(tmp_path):
    """A file ending mid-line (host was mid-write) gets truncated to last good newline."""
    src = tmp_path / "src.jsonl"
    src.write_text('{"a": 1}\n{"b": 2}\n{"c": incomplete')
    dst = tmp_path / "dst.jsonl"
    _copy_transcript_atomically(src, dst)
    lines = dst.read_text().splitlines()
    assert lines == ['{"a": 1}', '{"b": 2}']


def test_copy_transcript_leaves_no_tmp_on_success(tmp_path):
    src = tmp_path / "src.jsonl"
    dst = tmp_path / "dst.jsonl"
    _write_jsonl(src, [{"a": 1}])
    _copy_transcript_atomically(src, dst)
    assert not dst.with_suffix(dst.suffix + ".tmp").exists()


def test_copy_transcript_empty_file_is_valid(tmp_path):
    src = tmp_path / "src.jsonl"
    src.write_text("")
    dst = tmp_path / "dst.jsonl"
    _copy_transcript_atomically(src, dst)
    assert dst.exists()
    assert dst.read_text() == ""


def test_copy_transcript_preserves_valid_trailing_newline(tmp_path):
    src = tmp_path / "src.jsonl"
    src.write_text('{"a": 1}\n{"b": 2}\n')
    dst = tmp_path / "dst.jsonl"
    _copy_transcript_atomically(src, dst)
    assert dst.read_text() == '{"a": 1}\n{"b": 2}\n'


# ---------------------------------------------------------------------------
# sync_transcripts end-to-end
# ---------------------------------------------------------------------------


def _setup_vault(tmp_path: Path) -> tuple[Path, Path]:
    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    (lore_root / "wiki" / "mywiki").mkdir(parents=True)
    cwd = tmp_path / "proj"
    cwd.mkdir()
    _attach(lore_root, cwd)
    return lore_root, cwd


def test_sync_copies_transcript_into_wiki_transcripts_dir(tmp_path):
    lore_root, cwd = _setup_vault(tmp_path)

    src = tmp_path / "projects" / "uuid-1.jsonl"
    _write_jsonl(src, [{"type": "user", "message": {"role": "user", "content": "hi"}}])
    _seed_ledger_entry(lore_root, transcript_id="uuid-1", src_path=src, directory=cwd)

    result = sync_transcripts(lore_root)
    assert isinstance(result, SyncResult)
    assert result.copied == 1
    assert result.errors == []

    mirror = lore_root / "wiki" / "mywiki" / ".transcripts" / "uuid-1.jsonl"
    assert mirror.exists()
    assert mirror.read_text() == src.read_text()


def test_sync_adds_gitignore_entry_before_any_copy(tmp_path):
    lore_root, cwd = _setup_vault(tmp_path)
    src = tmp_path / "projects" / "u.jsonl"
    _write_jsonl(src, [{"type": "user", "message": {"role": "user", "content": "x"}}])
    _seed_ledger_entry(lore_root, transcript_id="u", src_path=src, directory=cwd)

    sync_transcripts(lore_root)

    gi = (lore_root / "wiki" / "mywiki" / ".gitignore").read_text()
    assert ".transcripts/" in gi.splitlines()


def test_sync_skips_up_to_date_destinations(tmp_path):
    lore_root, cwd = _setup_vault(tmp_path)
    src = tmp_path / "projects" / "a.jsonl"
    _write_jsonl(src, [{"x": 1}])
    _seed_ledger_entry(lore_root, transcript_id="a", src_path=src, directory=cwd)

    first = sync_transcripts(lore_root)
    assert first.copied == 1

    second = sync_transcripts(lore_root)
    assert second.copied == 0
    assert second.skipped >= 1


def test_sync_recopies_when_source_newer(tmp_path):
    lore_root, cwd = _setup_vault(tmp_path)
    src = tmp_path / "projects" / "a.jsonl"
    _write_jsonl(src, [{"x": 1}])
    _seed_ledger_entry(lore_root, transcript_id="a", src_path=src, directory=cwd)

    sync_transcripts(lore_root)

    # Bump source
    import time
    time.sleep(0.02)
    _write_jsonl(src, [{"x": 1}, {"x": 2}])

    second = sync_transcripts(lore_root)
    assert second.copied == 1
    mirror = lore_root / "wiki" / "mywiki" / ".transcripts" / "a.jsonl"
    assert "x\": 2" in mirror.read_text()


def test_sync_filters_by_wiki_name(tmp_path):
    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    (lore_root / "wiki" / "alpha").mkdir(parents=True)
    (lore_root / "wiki" / "beta").mkdir(parents=True)
    cwd_a = tmp_path / "proj-a"
    cwd_a.mkdir()
    cwd_b = tmp_path / "proj-b"
    cwd_b.mkdir()
    _attach(lore_root, cwd_a, wiki="alpha")
    _attach(lore_root, cwd_b, wiki="beta")

    src_a = tmp_path / "projects" / "a.jsonl"
    src_b = tmp_path / "projects" / "b.jsonl"
    _write_jsonl(src_a, [{"x": 1}])
    _write_jsonl(src_b, [{"x": 2}])
    _seed_ledger_entry(lore_root, transcript_id="a", src_path=src_a, directory=cwd_a)
    _seed_ledger_entry(lore_root, transcript_id="b", src_path=src_b, directory=cwd_b)

    result = sync_transcripts(lore_root, wiki="alpha")
    assert result.copied == 1

    assert (lore_root / "wiki" / "alpha" / ".transcripts" / "a.jsonl").exists()
    assert not (lore_root / "wiki" / "beta" / ".transcripts" / "b.jsonl").exists()


def test_sync_skips_orphan_entries(tmp_path):
    lore_root, cwd = _setup_vault(tmp_path)
    src = tmp_path / "projects" / "orph.jsonl"
    _write_jsonl(src, [{"x": 1}])
    _seed_ledger_entry(
        lore_root, transcript_id="orph",
        src_path=src, directory=cwd, orphan=True,
    )

    result = sync_transcripts(lore_root)
    assert result.copied == 0
    assert result.skipped >= 1


def test_sync_skips_unattached_entries(tmp_path):
    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    (lore_root / "wiki" / "mywiki").mkdir(parents=True)
    # Note: no `_attach` call — the cwd is not covered by any attachment.
    cwd = tmp_path / "unattached"
    cwd.mkdir()

    src = tmp_path / "projects" / "una.jsonl"
    _write_jsonl(src, [{"x": 1}])
    _seed_ledger_entry(lore_root, transcript_id="una", src_path=src, directory=cwd)

    result = sync_transcripts(lore_root)
    assert result.copied == 0
    assert result.skipped >= 1


def test_sync_collects_errors_instead_of_aborting(tmp_path):
    """One bad source doesn't stop the rest from syncing."""
    lore_root, cwd = _setup_vault(tmp_path)
    good_src = tmp_path / "projects" / "good.jsonl"
    _write_jsonl(good_src, [{"x": 1}])
    # Pre-existing .gitignore with a negation → forces error for this wiki
    (lore_root / "wiki" / "mywiki" / ".gitignore").write_text("!.transcripts/keep.jsonl\n")
    _seed_ledger_entry(lore_root, transcript_id="good", src_path=good_src, directory=cwd)

    result = sync_transcripts(lore_root)
    assert result.copied == 0
    assert len(result.errors) >= 1
    assert "negation" in result.errors[0].lower() or "transcripts" in result.errors[0].lower()
