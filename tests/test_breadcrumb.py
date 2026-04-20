"""Tests for lore_cli.breadcrumb — session-start banner rendering."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lore_cli.breadcrumb import BannerContext, render_banner
from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry, WikiLedger, WikiLedgerEntry
from lore_core.types import Scope
from lore_core.wiki_config import BreadcrumbConfig, WikiConfig


@pytest.fixture
def lore_root(tmp_path: Path) -> Path:
    """Create a minimal lore_root with .lore/ directory."""
    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def wiki_config_normal() -> WikiConfig:
    """Wiki config with normal breadcrumb mode."""
    return WikiConfig(breadcrumb=BreadcrumbConfig(mode="normal"))


@pytest.fixture
def wiki_config_quiet() -> WikiConfig:
    """Wiki config with quiet breadcrumb mode."""
    return WikiConfig(breadcrumb=BreadcrumbConfig(mode="quiet"))


@pytest.fixture
def wiki_config_verbose() -> WikiConfig:
    """Wiki config with verbose breadcrumb mode."""
    return WikiConfig(breadcrumb=BreadcrumbConfig(mode="verbose"))


@pytest.fixture
def scope(tmp_path: Path) -> Scope:
    """Create a test scope."""
    return Scope(
        wiki="private",
        scope="private:root",
        backend="none",
        claude_md_path=tmp_path / "CLAUDE.md",
    )


def _make_transcript_entry(
    lore_root: Path,
    transcript_id: str = "t1",
    digested_hash: str | None = None,
) -> TranscriptLedgerEntry:
    """Create a transcript ledger entry."""
    return TranscriptLedgerEntry(
        host="claude-code",
        transcript_id=transcript_id,
        path=lore_root / "transcripts" / f"{transcript_id}.json",
        directory=lore_root / "transcripts",
        digested_hash=digested_hash,
        digested_index_hint=None,
        synthesised_hash=None,
        last_mtime=datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC),
        curator_a_run=None,
        noteworthy=None,
        session_note=None,
    )


# ---------------------------------------------------------------------------
# 1. Up-to-date: no pending, no errors
# ---------------------------------------------------------------------------


def test_banner_up_to_date_when_no_pending(
    lore_root: Path, wiki_config_normal: WikiConfig, scope: Scope
) -> None:
    """Empty ledger with no pending → 'up to date' message."""
    now = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)
    ctx = BannerContext(
        lore_root=lore_root,
        scope=scope,
        wiki_config=wiki_config_normal,
        now=now,
        note_count=0,
    )
    result = render_banner(ctx)
    assert result is not None
    assert result.startswith("lore: ")
    assert "up to date" in result
    assert "0 notes" in result


# ---------------------------------------------------------------------------
# 2. Pending: show count + timing
# ---------------------------------------------------------------------------


def test_banner_pending_format(
    lore_root: Path, wiki_config_normal: WikiConfig, scope: Scope
) -> None:
    """Seed 3 pending entries in ledger."""
    now = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)
    tledger = TranscriptLedger(lore_root)

    # Add 3 pending entries (no digested_hash)
    for i in range(3):
        entry = _make_transcript_entry(lore_root, transcript_id=f"t{i+1}", digested_hash=None)
        tledger.upsert(entry)

    ctx = BannerContext(
        lore_root=lore_root,
        scope=scope,
        wiki_config=wiki_config_normal,
        now=now,
        note_count=10,
    )
    result = render_banner(ctx)
    assert result is not None
    assert result.startswith("lore: ")
    assert "3 pending" in result


# ---------------------------------------------------------------------------
# 3. Curator running: lockfile check
# ---------------------------------------------------------------------------


def test_banner_curator_running_when_lock_exists(
    lore_root: Path, wiki_config_normal: WikiConfig, scope: Scope
) -> None:
    """Pre-create .lore/curator.lock directory."""
    lock_dir = lore_root / ".lore" / "curator.lock"
    lock_dir.mkdir(parents=True, exist_ok=True)

    now = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)
    ctx = BannerContext(
        lore_root=lore_root,
        scope=scope,
        wiki_config=wiki_config_normal,
        now=now,
    )
    result = render_banner(ctx)
    assert result == "lore: curator A running in background"


# ---------------------------------------------------------------------------
# 4. Quiet mode: suppress non-errors
# ---------------------------------------------------------------------------


def test_banner_quiet_mode_suppresses_non_errors(
    lore_root: Path, wiki_config_quiet: WikiConfig, scope: Scope
) -> None:
    """Quiet mode with no errors → None."""
    now = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)
    ctx = BannerContext(
        lore_root=lore_root,
        scope=scope,
        wiki_config=wiki_config_quiet,
        now=now,
    )
    result = render_banner(ctx)
    assert result is None


# ---------------------------------------------------------------------------
# 5. Quiet mode with errors: still show errors
# ---------------------------------------------------------------------------


def test_banner_quiet_mode_still_shows_errors(
    lore_root: Path, wiki_config_quiet: WikiConfig, scope: Scope
) -> None:
    """Quiet mode with errors → shows errors."""
    now = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)
    ctx = BannerContext(
        lore_root=lore_root,
        scope=scope,
        wiki_config=wiki_config_quiet,
        now=now,
    )
    errors = ["SURFACES.md invalid"]
    result = render_banner(ctx, errors=errors)
    assert result is not None
    assert result.startswith("lore!: ")
    assert "SURFACES.md invalid" in result


# ---------------------------------------------------------------------------
# 6. Errors use lore!: prefix
# ---------------------------------------------------------------------------


def test_banner_lore_bang_prefix_on_errors(
    lore_root: Path, wiki_config_normal: WikiConfig, scope: Scope
) -> None:
    """Error list → prefix is 'lore!:', not 'lore:'."""
    now = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)
    ctx = BannerContext(
        lore_root=lore_root,
        scope=scope,
        wiki_config=wiki_config_normal,
        now=now,
    )
    errors = ["config error 1", "config error 2"]
    result = render_banner(ctx, errors=errors)
    assert result is not None
    assert result.startswith("lore!: ")
    assert "config error 1 · config error 2" in result


# ---------------------------------------------------------------------------
# 7. Relative time: minutes
# ---------------------------------------------------------------------------


def test_banner_relative_time_minutes(
    lore_root: Path, wiki_config_normal: WikiConfig, scope: Scope
) -> None:
    """Last curator run 5 minutes ago → '5m ago'."""
    now = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)
    curator_time = now - timedelta(minutes=5)

    # Add 1 pending entry
    tledger = TranscriptLedger(lore_root)
    entry = _make_transcript_entry(lore_root, transcript_id="t1", digested_hash=None)
    tledger.upsert(entry)

    # Set last_curator_a in wiki ledger
    wledger = WikiLedger(lore_root, "private")
    wiki_entry = WikiLedgerEntry(wiki="private", last_curator_a=curator_time)
    wledger.write(wiki_entry)

    ctx = BannerContext(
        lore_root=lore_root,
        scope=scope,
        wiki_config=wiki_config_normal,
        now=now,
        note_count=10,
    )
    result = render_banner(ctx)
    assert result is not None
    assert "5m ago" in result


# ---------------------------------------------------------------------------
# 8. Relative time: hours
# ---------------------------------------------------------------------------


def test_banner_relative_time_hours(
    lore_root: Path, wiki_config_normal: WikiConfig, scope: Scope
) -> None:
    """Last curator run 3 hours ago → '3h ago'."""
    now = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)
    curator_time = now - timedelta(hours=3)

    # Add 1 pending entry
    tledger = TranscriptLedger(lore_root)
    entry = _make_transcript_entry(lore_root, transcript_id="t1", digested_hash=None)
    tledger.upsert(entry)

    # Set last_curator_a in wiki ledger
    wledger = WikiLedger(lore_root, "private")
    wiki_entry = WikiLedgerEntry(wiki="private", last_curator_a=curator_time)
    wledger.write(wiki_entry)

    ctx = BannerContext(
        lore_root=lore_root,
        scope=scope,
        wiki_config=wiki_config_normal,
        now=now,
        note_count=10,
    )
    result = render_banner(ctx)
    assert result is not None
    assert "3h ago" in result


# ---------------------------------------------------------------------------
# 9. Relative time: yesterday
# ---------------------------------------------------------------------------


def test_banner_relative_time_yesterday(
    lore_root: Path, wiki_config_normal: WikiConfig, scope: Scope
) -> None:
    """Last curator run 1 day + 1 second ago → 'yesterday'."""
    now = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)
    curator_time = now - timedelta(days=1, seconds=1)

    # Add 1 pending entry
    tledger = TranscriptLedger(lore_root)
    entry = _make_transcript_entry(lore_root, transcript_id="t1", digested_hash=None)
    tledger.upsert(entry)

    # Set last_curator_a in wiki ledger
    wledger = WikiLedger(lore_root, "private")
    wiki_entry = WikiLedgerEntry(wiki="private", last_curator_a=curator_time)
    wledger.write(wiki_entry)

    ctx = BannerContext(
        lore_root=lore_root,
        scope=scope,
        wiki_config=wiki_config_normal,
        now=now,
        note_count=10,
    )
    result = render_banner(ctx)
    assert result is not None
    assert "yesterday" in result


# ---------------------------------------------------------------------------
# 10. Verbose mode (optional for v1, stub)
# ---------------------------------------------------------------------------


def test_banner_verbose_vs_normal(
    lore_root: Path, wiki_config_verbose: WikiConfig, scope: Scope
) -> None:
    """Verbose mode returns same string as normal for v1."""
    now = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)

    # Add 1 pending entry
    tledger = TranscriptLedger(lore_root)
    entry = _make_transcript_entry(lore_root, transcript_id="t1", digested_hash=None)
    tledger.upsert(entry)

    ctx = BannerContext(
        lore_root=lore_root,
        scope=scope,
        wiki_config=wiki_config_verbose,
        now=now,
        note_count=10,
    )
    result = render_banner(ctx)
    assert result is not None
    # For v1, verbose == normal (same string returned)
    # Just verify it produces a valid banner
    assert result.startswith("lore: ")


# ---------------------------------------------------------------------------
# 11. All-skips hint
# ---------------------------------------------------------------------------


def test_banner_all_skips_hint(tmp_path: Path) -> None:
    """Most recent run: errors=0, filed=0, skipped>0, no pending → hint appears."""
    import json

    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir(parents=True)
    runs = lore_dir / "runs"
    runs.mkdir(parents=True)
    (runs / "2026-04-20T14-32-05-skipsr.jsonl").write_text(
        json.dumps({"type": "run-start", "ts": "2026-04-20T14:32:05Z", "trigger": "hook"}) + "\n"
        + json.dumps({"type": "run-end", "ts": "2026-04-20T14:32:09Z",
                      "duration_ms": 4000, "notes_new": 0, "notes_merged": 0,
                      "skipped": 3, "errors": 0}) + "\n"
    )
    from lore_cli.breadcrumb import render_banner
    now = datetime(2026, 4, 20, 15, 0, 0, tzinfo=UTC)
    scope = Scope(
        wiki="private",
        scope="private:root",
        backend="none",
        claude_md_path=tmp_path / "CLAUDE.md",
    )
    ctx = BannerContext(
        lore_root=tmp_path,
        scope=scope,
        wiki_config=WikiConfig(breadcrumb=BreadcrumbConfig(mode="normal")),
        now=now,
        note_count=5,
    )
    banner = render_banner(ctx)
    assert banner is not None
    assert "0 notes" in banner
    assert "3 skipped" in banner
    assert "lore runs show latest" in banner


# ---------------------------------------------------------------------------
# 12. Last-run error prefix
# ---------------------------------------------------------------------------


def test_banner_last_run_error_prefix(tmp_path: Path) -> None:
    """Most recent run ended with errors > 0 → lore!: prefix."""
    import json

    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir(parents=True)
    runs = lore_dir / "runs"
    runs.mkdir(parents=True)
    (runs / "2026-04-20T14-32-05-errrun.jsonl").write_text(
        json.dumps({"type": "run-start", "ts": "2026-04-20T14:32:05Z", "trigger": "hook"}) + "\n"
        + json.dumps({"type": "run-end", "ts": "2026-04-20T14:32:09Z",
                      "duration_ms": 4000, "notes_new": 0, "notes_merged": 0,
                      "skipped": 0, "errors": 2}) + "\n"
    )
    from lore_cli.breadcrumb import render_banner
    now = datetime(2026, 4, 20, 15, 0, 0, tzinfo=UTC)
    scope = Scope(
        wiki="private",
        scope="private:root",
        backend="none",
        claude_md_path=tmp_path / "CLAUDE.md",
    )
    ctx = BannerContext(
        lore_root=tmp_path,
        scope=scope,
        wiki_config=WikiConfig(breadcrumb=BreadcrumbConfig(mode="normal")),
        now=now,
        note_count=5,
    )
    banner = render_banner(ctx)
    assert banner is not None
    assert banner.startswith("lore!:")
    assert "2 errors" in banner
    assert "errrun" in banner


# ---------------------------------------------------------------------------
# 13. Hook-error trailing segment
# ---------------------------------------------------------------------------


def test_banner_hook_error_trailing_segment(tmp_path: Path) -> None:
    """A recent hook-events record with outcome=error appends trailing segment."""
    import json
    from datetime import UTC, datetime

    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir(parents=True)
    events = lore_dir / "hook-events.jsonl"
    recent = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    events.write_text(
        json.dumps({"schema_version": 1, "ts": recent, "event": "session-end",
                    "outcome": "error"}) + "\n"
    )
    from lore_cli.breadcrumb import render_banner
    now = datetime.now(UTC)
    scope = Scope(
        wiki="private",
        scope="private:root",
        backend="none",
        claude_md_path=tmp_path / "CLAUDE.md",
    )
    ctx = BannerContext(
        lore_root=tmp_path,
        scope=scope,
        wiki_config=WikiConfig(breadcrumb=BreadcrumbConfig(mode="normal")),
        now=now,
        note_count=5,
    )
    banner = render_banner(ctx)
    assert banner is not None
    assert "hook error" in banner
