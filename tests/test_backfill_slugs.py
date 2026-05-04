"""Tests for the one-shot ``lore curator backfill-slugs`` pass."""
from __future__ import annotations

from pathlib import Path

import pytest

from lore_curator.backfill_slugs import (
    apply_rename,
    backfill_wiki,
    plan_rename,
)


def _write_note(path: Path, fm_yaml: str, body: str = "body\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{fm_yaml}\n---\n\n{body}", encoding="utf-8")
    return path


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    sessions = tmp_path / "sessions" / "2026" / "05"
    sessions.mkdir(parents=True)
    return tmp_path


# ---------------------------------------------------------------------------
# plan_rename — the pure decision function
# ---------------------------------------------------------------------------


def test_plan_renames_when_title_yields_different_slug(wiki: Path):
    p = _write_note(
        wiki / "sessions" / "2026" / "05" / "04-1101-session-lore-1101.md",
        "type: session\ntitle: Storage Engine Disk Identifier Tests Located",
    )
    plan = plan_rename(p)
    assert plan is not None
    assert plan.new_path.name == "04-1101-storage-engine-disk-identifier-tests-located.md"


def test_plan_skips_stub_state(wiki: Path):
    p = _write_note(
        wiki / "sessions" / "2026" / "05" / "04-1318-attach.md",
        "type: session\nstate: stub\ntitle: lore session — 2026-05-04",
    )
    assert plan_rename(p) is None


def test_plan_skips_continuation_chain(wiki: Path):
    p = _write_note(
        wiki / "sessions" / "2026" / "05" / "04-1101-foo.md",
        "type: session\ntitle: A Real Title\npart: 2\ncontinues: '[[04-0900-prior]]'",
    )
    assert plan_rename(p) is None


def test_plan_skips_when_slug_already_matches(wiki: Path):
    # Title "Storage Engine" → slug "storage-engine"; filename matches.
    p = _write_note(
        wiki / "sessions" / "2026" / "05" / "04-1101-storage-engine.md",
        "type: session\ntitle: Storage Engine",
    )
    assert plan_rename(p) is None


def test_plan_skips_placeholder_title(wiki: Path):
    p = _write_note(
        wiki / "sessions" / "2026" / "05" / "04-1101-session-lore-1101.md",
        "type: session\ntitle: 'lore session — 2026-05-04'\ndescription: '_synthesis pending_'",
    )
    assert plan_rename(p) is None


def test_plan_skips_malformed_filename(wiki: Path):
    # Missing the ``<DD>-<HHMM>-`` prefix.
    p = _write_note(wiki / "sessions" / "2026" / "05" / "weird-name.md",
                    "type: session\ntitle: Real Title")
    assert plan_rename(p) is None


def test_plan_avoids_collision(wiki: Path):
    target = wiki / "sessions" / "2026" / "05" / "04-1101-target.md"
    pre_existing = wiki / "sessions" / "2026" / "05" / "04-1101-target.md"
    pre_existing.parent.mkdir(parents=True, exist_ok=True)
    pre_existing.write_text("---\ntype: session\n---\nother\n")
    p = _write_note(
        wiki / "sessions" / "2026" / "05" / "04-1101-source.md",
        "type: session\ntitle: target",
    )
    plan = plan_rename(p)
    assert plan is not None
    assert plan.new_path.name == "04-1101-target-2.md"


# ---------------------------------------------------------------------------
# apply_rename — disk effects
# ---------------------------------------------------------------------------


def test_apply_rename_writes_alias_and_renames(wiki: Path):
    p = _write_note(
        wiki / "sessions" / "2026" / "05" / "04-1101-session-lore-1101.md",
        "type: session\ntitle: Storage Engine Refactor",
    )
    plan = plan_rename(p)
    assert plan is not None
    apply_rename(plan)

    assert not p.exists()
    assert plan.new_path.exists()
    text = plan.new_path.read_text(encoding="utf-8")
    assert "aliases:" in text
    assert "04-1101-session-lore-1101" in text  # full old stem preserved


def test_apply_rename_appends_to_existing_aliases(wiki: Path):
    p = _write_note(
        wiki / "sessions" / "2026" / "05" / "04-1101-cryptic.md",
        "type: session\ntitle: Better Title\naliases:\n  - earlier-alias",
    )
    plan = plan_rename(p)
    assert plan is not None
    apply_rename(plan)

    from lore_core.schema import parse_frontmatter

    fm = parse_frontmatter(plan.new_path.read_text(encoding="utf-8"))
    assert "earlier-alias" in fm["aliases"]
    assert "04-1101-cryptic" in fm["aliases"]


# ---------------------------------------------------------------------------
# backfill_wiki — driver
# ---------------------------------------------------------------------------


def test_backfill_wiki_dry_run_does_not_touch_disk(wiki: Path):
    p = _write_note(
        wiki / "sessions" / "2026" / "05" / "04-1101-session-lore-1101.md",
        "type: session\ntitle: Storage Engine Refactor",
    )
    report = backfill_wiki(wiki, apply=False)
    assert len(report.planned) == 1
    assert not report.renamed  # nothing applied
    assert p.exists()  # still at original path


def test_backfill_wiki_apply_renames_only_eligible(wiki: Path):
    eligible = _write_note(
        wiki / "sessions" / "2026" / "05" / "04-1101-session-lore-1101.md",
        "type: session\ntitle: Storage Engine Refactor",
    )
    stub = _write_note(
        wiki / "sessions" / "2026" / "05" / "04-1318-attach.md",
        "type: session\nstate: stub\ntitle: 'lore session — 2026-05-04'",
    )
    chain = _write_note(
        wiki / "sessions" / "2026" / "05" / "04-1500-foo.md",
        "type: session\ntitle: A Real Title\npart: 2\ncontinues: '[[04-1400-prior]]'",
    )
    report = backfill_wiki(wiki, apply=True)
    assert len(report.renamed) == 1
    assert report.skipped_stub == 1
    assert report.skipped_chain == 1
    assert not eligible.exists()
    assert stub.exists()
    assert chain.exists()
