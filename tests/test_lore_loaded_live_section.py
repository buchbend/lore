"""Task 13: /lore:loaded grows a live-state section (live first, cache below).

Per UX review: a Claude session opening /lore:loaded wants to know
"what's true NOW" before "what was injected at SessionStart."

The skill routes to `lore hook why`; we extend _why() with a live
section rendered from CaptureState.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lore_cli.hooks import _why


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _seed_cache(tmp_path: Path, pid: int, body: str) -> Path:
    """Write a SessionStart cache file that _why() will find."""
    sessions = tmp_path / "sessions"
    sessions.mkdir(parents=True)
    cache = sessions / f"{pid}.md"
    cache.write_text(body)
    return cache


def _set_env(monkeypatch, cache_dir: Path, lore_root: Path) -> None:
    monkeypatch.setenv("LORE_CACHE", str(cache_dir))
    monkeypatch.setenv("LORE_ROOT", str(lore_root))


# ---------------------------------------------------------------------------
# Output shape: live first, cache below
# ---------------------------------------------------------------------------


def test_lore_loaded_contains_live_then_cache(tmp_path: Path, monkeypatch) -> None:
    cache_dir = tmp_path / "cache"
    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    (lore_root / "wiki" / "private").mkdir(parents=True)

    _seed_cache(cache_dir, pid=99999, body="CACHED_BODY_MARKER\n")
    _set_env(monkeypatch, cache_dir, lore_root)
    # Force _claude_code_pid to resolve to the seeded pid.
    monkeypatch.setattr("lore_cli.hooks._claude_code_pid", lambda: 99999)

    out = _why()
    assert "Live state" in out, f"expected live section header; got:\n{out}"
    assert "CACHED_BODY_MARKER" in out, "cached section must still be present"
    # Live must come before cache (bytes-wise).
    assert out.find("Live state") < out.find("CACHED_BODY_MARKER"), (
        f"live section must precede cache; got:\n{out}"
    )


def test_lore_loaded_handles_missing_cache(tmp_path: Path, monkeypatch) -> None:
    """With no cache, still show live state + the "no cache" message below."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)

    _set_env(monkeypatch, cache_dir, lore_root)
    monkeypatch.setattr("lore_cli.hooks._claude_code_pid", lambda: 99999)

    out = _why()
    assert "Live state" in out
    assert "no SessionStart cache" in out.lower() or "not fired" in out.lower()


def test_lore_loaded_live_section_updates_across_calls(tmp_path: Path, monkeypatch) -> None:
    """A pending transcript added between two calls is reflected in the
    second call's live section — demonstrates the state is queried fresh.
    """
    from datetime import UTC, datetime as _dt
    from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry
    from lore_core.state.attachments import Attachment, AttachmentsFile

    cache_dir = tmp_path / "cache"
    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    (lore_root / "wiki" / "private").mkdir(parents=True)

    # Registry-era: register the test's expected cwd as an attachment so
    # the "Live state" branch renders ("not attached" is the alternative).
    project = tmp_path / "proj"
    project.mkdir()
    af = AttachmentsFile(lore_root); af.load()
    af.add(Attachment(
        path=project, wiki="private", scope="proj:test",
        attached_at=_dt.now(UTC), source="manual",
    ))
    af.save()
    monkeypatch.chdir(project)

    _seed_cache(cache_dir, pid=99999, body="body\n")
    _set_env(monkeypatch, cache_dir, lore_root)
    monkeypatch.setattr("lore_cli.hooks._claude_code_pid", lambda: 99999)

    first = _why()
    assert "Pending" in first

    # Add a pending transcript between calls.
    tledger = TranscriptLedger(lore_root)
    tledger.upsert(
        TranscriptLedgerEntry(
            host="fake",
            transcript_id="t1",
            path=lore_root / "t1.jsonl",
            directory=lore_root,
            digested_hash=None,
            digested_index_hint=None,
            synthesised_hash=None,
            last_mtime=datetime.now(UTC),
            curator_a_run=None,
            noteworthy=None,
            session_note=None,
        )
    )

    second = _why()
    # The Pending line changed between calls (from "no transcripts" to "1 transcript").
    assert first != second, (
        "live section must refresh across calls — first and second should differ"
    )
    assert "1 transcript" in second


def test_lore_loaded_handles_capture_state_failure(tmp_path: Path, monkeypatch) -> None:
    """If query_capture_state raises, live section degrades to a one-line
    error; cache section still renders.
    """
    cache_dir = tmp_path / "cache"
    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)

    _seed_cache(cache_dir, pid=99999, body="CACHED_MARKER\n")
    _set_env(monkeypatch, cache_dir, lore_root)
    monkeypatch.setattr("lore_cli.hooks._claude_code_pid", lambda: 99999)

    def boom(*_a, **_kw):
        raise RuntimeError("synthetic capture_state failure")

    monkeypatch.setattr("lore_core.capture_state.query_capture_state", boom)

    out = _why()
    assert "CACHED_MARKER" in out, "cache must still render when live fails"
    assert "unavailable" in out.lower() or "error" in out.lower(), (
        f"live section should degrade gracefully; got:\n{out}"
    )
