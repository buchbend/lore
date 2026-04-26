"""Tests for lore_cli.breadcrumb — session-start banner rendering."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lore_cli.breadcrumb import (
    BannerContext,
    consume_pending_breadcrumb,
    render_banner,
    render_session_end_breadcrumb,
    write_pending_breadcrumb,
)
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
        integration="claude-code",
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


def test_banner_no_pending_no_errors_is_silent(
    lore_root: Path, wiki_config_normal: WikiConfig, scope: Scope
) -> None:
    """Empty ledger with no pending, no errors → None."""
    now = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)
    ctx = BannerContext(
        lore_root=lore_root,
        scope=scope,
        wiki_config=wiki_config_normal,
        now=now,
        note_count=0,
    )
    result = render_banner(ctx)
    assert result is None


# ---------------------------------------------------------------------------
# 2. Pending: show count + timing
# ---------------------------------------------------------------------------


def test_banner_pending_is_silent(
    lore_root: Path, wiki_config_normal: WikiConfig, scope: Scope
) -> None:
    """Pending entries without errors → None (pipeline state is internal)."""
    now = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)
    tledger = TranscriptLedger(lore_root)

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
    assert result is None


# ---------------------------------------------------------------------------
# 3. Curator running: lockfile check
# ---------------------------------------------------------------------------


def test_banner_curator_running_is_silent(
    lore_root: Path, wiki_config_normal: WikiConfig, scope: Scope
) -> None:
    """Curator running without errors → None (pipeline state is internal)."""
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
    assert result is None


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


def test_banner_pending_with_curator_history_is_silent(
    lore_root: Path, wiki_config_normal: WikiConfig, scope: Scope
) -> None:
    """Pending entries with curator history → None (pipeline state is internal)."""
    now = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)
    curator_time = now - timedelta(minutes=5)

    tledger = TranscriptLedger(lore_root)
    entry = _make_transcript_entry(lore_root, transcript_id="t1", digested_hash=None)
    tledger.upsert(entry)

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
    assert result is None


# ---------------------------------------------------------------------------
# 10. Verbose mode: same as normal (no pipeline jargon)
# ---------------------------------------------------------------------------


def test_banner_verbose_vs_normal(
    lore_root: Path, wiki_config_verbose: WikiConfig, scope: Scope
) -> None:
    """Verbose mode without errors → None."""
    now = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)
    ctx = BannerContext(
        lore_root=lore_root,
        scope=scope,
        wiki_config=wiki_config_verbose,
        now=now,
        note_count=10,
    )
    result = render_banner(ctx)
    assert result is None


# ---------------------------------------------------------------------------
# 11. All-skips: no longer surfaced (pipeline state is internal)
# ---------------------------------------------------------------------------


def test_banner_all_skips_is_silent(tmp_path: Path) -> None:
    """Most recent run: errors=0, filed=0, skipped>0, no pending → None."""
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
    assert banner is None


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


# ---------------------------------------------------------------------------
# 14. render_session_end_breadcrumb — pure function tests
# ---------------------------------------------------------------------------


def test_session_end_breadcrumb_spawned_curator_is_silent() -> None:
    result = render_session_end_breadcrumb("spawned-curator", pending_after=3, threshold=3)
    assert result is None


def test_session_end_breadcrumb_below_threshold_is_silent() -> None:
    result = render_session_end_breadcrumb("below-threshold", pending_after=2, threshold=3)
    assert result is None


def test_session_end_breadcrumb_no_new_turns_is_none() -> None:
    result = render_session_end_breadcrumb("no-new-turns", pending_after=0, threshold=3)
    assert result is None


def test_session_end_breadcrumb_error() -> None:
    result = render_session_end_breadcrumb("error", pending_after=0, error_message="disk full")
    assert result is not None
    assert result.startswith("lore!:")
    assert "disk full" in result


def test_session_end_breadcrumb_error_default_message() -> None:
    result = render_session_end_breadcrumb("error", pending_after=0)
    assert result is not None
    assert result.startswith("lore!:")
    assert "unknown error" in result


def test_session_end_breadcrumb_unattached_is_none() -> None:
    result = render_session_end_breadcrumb("unattached", pending_after=0)
    assert result is None


# ---------------------------------------------------------------------------
# 15. write_pending_breadcrumb / consume_pending_breadcrumb round-trip
# ---------------------------------------------------------------------------


def test_pending_breadcrumb_roundtrip(tmp_path: Path) -> None:
    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir()
    write_pending_breadcrumb(tmp_path, "lore!: capture error — disk full")
    result = consume_pending_breadcrumb(tmp_path)
    assert result == "lore!: capture error — disk full"
    # Second consume returns None — already consumed
    assert consume_pending_breadcrumb(tmp_path) is None


def test_pending_breadcrumb_absent_returns_none(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    assert consume_pending_breadcrumb(tmp_path) is None


def test_pending_breadcrumb_stale_returns_none(tmp_path: Path) -> None:
    import time as _time

    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir()
    dest = lore_dir / "pending-breadcrumb.txt"
    dest.write_text("old line")
    # Back-date mtime by 2 hours
    old_time = _time.time() - 7200
    import os
    os.utime(dest, (old_time, old_time))
    assert consume_pending_breadcrumb(tmp_path) is None


# ---------------------------------------------------------------------------
# 16. render_banner with pending breadcrumb prepended
# ---------------------------------------------------------------------------


def test_render_banner_prepends_session_end_error(tmp_path: Path) -> None:
    """A pending error breadcrumb is prepended to the SessionStart banner."""
    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir()
    write_pending_breadcrumb(tmp_path, "lore!: capture error — disk full")

    now = datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC)
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
    assert "capture error" in banner


def test_render_banner_quiet_with_session_end_error(tmp_path: Path) -> None:
    """Quiet mode still surfaces error breadcrumbs."""
    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir()
    write_pending_breadcrumb(tmp_path, "lore!: capture error — timeout")

    now = datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC)
    scope = Scope(
        wiki="private",
        scope="private:root",
        backend="none",
        claude_md_path=tmp_path / "CLAUDE.md",
    )
    ctx = BannerContext(
        lore_root=tmp_path,
        scope=scope,
        wiki_config=WikiConfig(breadcrumb=BreadcrumbConfig(mode="quiet")),
        now=now,
        note_count=5,
    )
    banner = render_banner(ctx)
    assert banner is not None
    assert "capture error" in banner
