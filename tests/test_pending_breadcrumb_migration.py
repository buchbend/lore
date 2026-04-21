"""Task 9b: pending-breadcrumb storage moves from .txt file → hook-events.jsonl.

Pre-Task-9b: a parallel little storage system at
``$LORE_ROOT/.lore/pending-breadcrumb.txt`` — one file, read-delete once,
staleness derived from file mtime. Architect and merciless-dev both
flagged this as a duplicated persistence layer that should collapse
into ``hook-events.jsonl`` (the existing append-only event log).

After Task 9b:
- ``write_pending_breadcrumb`` emits a ``pending-breadcrumb-written``
  event.
- ``consume_pending_breadcrumb`` scans for the most recent written/
  consumed pair. Returns the line iff written is newer than consumed
  AND younger than 3600s. On success, emits a
  ``pending-breadcrumb-consumed`` event (shows at most once).
- Legacy ``pending-breadcrumb.txt`` migrates on first SessionStart
  post-upgrade: read + emit a written event with the file's mtime +
  unlink. Idempotent: second call has no file to migrate.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from lore_cli.breadcrumb import (
    _PENDING_BREADCRUMB_MAX_AGE_S,
    consume_pending_breadcrumb,
    migrate_legacy_pending_breadcrumb,
    write_pending_breadcrumb,
)


def _read_events(lore_root: Path) -> list[dict]:
    p = lore_root / ".lore" / "hook-events.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_write_pending_breadcrumb_emits_event(tmp_path: Path) -> None:
    write_pending_breadcrumb(tmp_path, "lore: capture queued · pending 2")

    events = _read_events(tmp_path)
    written = [e for e in events if e.get("event") == "pending-breadcrumb-written"]
    assert len(written) == 1
    assert written[0]["line"] == "lore: capture queued · pending 2"


def test_pending_breadcrumb_round_trips_via_hook_events(tmp_path: Path) -> None:
    write_pending_breadcrumb(tmp_path, "lore: pending 3")

    line = consume_pending_breadcrumb(tmp_path)
    assert line == "lore: pending 3"


def test_consume_emits_consumed_event(tmp_path: Path) -> None:
    write_pending_breadcrumb(tmp_path, "lore: test")
    consume_pending_breadcrumb(tmp_path)

    events = _read_events(tmp_path)
    consumed = [e for e in events if e.get("event") == "pending-breadcrumb-consumed"]
    assert len(consumed) == 1, f"consume must append a consumed event; got {events}"


def test_consume_returns_none_on_second_call(tmp_path: Path) -> None:
    """Shows at most once — second consume after a single write returns None."""
    write_pending_breadcrumb(tmp_path, "lore: once")
    first = consume_pending_breadcrumb(tmp_path)
    second = consume_pending_breadcrumb(tmp_path)

    assert first == "lore: once"
    assert second is None


def test_consume_returns_latest_written(tmp_path: Path) -> None:
    """A second write between a consume and the next overrides the stored line."""
    write_pending_breadcrumb(tmp_path, "lore: first")
    consume_pending_breadcrumb(tmp_path)

    write_pending_breadcrumb(tmp_path, "lore: second")
    assert consume_pending_breadcrumb(tmp_path) == "lore: second"


def test_consume_skips_stale_written(tmp_path: Path) -> None:
    """A written event older than _PENDING_BREADCRUMB_MAX_AGE_S is ignored.

    Staleness derived from event ``ts`` (not file mtime).
    """
    # Seed an old written event directly.
    (tmp_path / ".lore").mkdir()
    events_path = tmp_path / ".lore" / "hook-events.jsonl"
    old_ts = f"2020-01-01T00:00:00Z"
    events_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "ts": old_ts,
                "event": "pending-breadcrumb-written",
                "line": "ancient message",
            }
        ) + "\n"
    )

    assert consume_pending_breadcrumb(tmp_path) is None


def test_consume_returns_none_when_no_written_event(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    (tmp_path / ".lore" / "hook-events.jsonl").write_text(
        json.dumps(
            {"schema_version": 2, "ts": "2026-04-21T12:00:00Z", "event": "session-start"}
        ) + "\n"
    )
    assert consume_pending_breadcrumb(tmp_path) is None


def test_consume_returns_none_on_fresh_vault(tmp_path: Path) -> None:
    assert consume_pending_breadcrumb(tmp_path) is None


# ---------------------------------------------------------------------------
# Legacy file migration
# ---------------------------------------------------------------------------


def test_legacy_pending_breadcrumb_txt_migrates_on_call(tmp_path: Path) -> None:
    """Pre-existing .lore/pending-breadcrumb.txt is converted + unlinked."""
    lore = tmp_path / ".lore"
    lore.mkdir()
    legacy = lore / "pending-breadcrumb.txt"
    legacy.write_text("lore: legacy breadcrumb from before Task 9b")

    migrate_legacy_pending_breadcrumb(tmp_path)

    assert not legacy.exists(), "legacy file must be unlinked after migration"
    events = _read_events(tmp_path)
    written = [e for e in events if e.get("event") == "pending-breadcrumb-written"]
    assert len(written) == 1
    assert "legacy breadcrumb" in written[0]["line"]


def test_legacy_migration_is_idempotent(tmp_path: Path) -> None:
    lore = tmp_path / ".lore"
    lore.mkdir()
    legacy = lore / "pending-breadcrumb.txt"
    legacy.write_text("lore: once")

    migrate_legacy_pending_breadcrumb(tmp_path)
    migrate_legacy_pending_breadcrumb(tmp_path)  # second call = no-op

    events = _read_events(tmp_path)
    written = [e for e in events if e.get("event") == "pending-breadcrumb-written"]
    assert len(written) == 1, f"second migration must be a no-op; got {events}"


def test_legacy_migration_preserves_mtime_in_event_ts(tmp_path: Path) -> None:
    """Migration uses file mtime as the event ts so old staleness checks still work."""
    import os as _os
    from datetime import UTC, datetime
    lore = tmp_path / ".lore"
    lore.mkdir()
    legacy = lore / "pending-breadcrumb.txt"
    legacy.write_text("lore: historical")

    target_mtime = time.time() - 100  # 100s ago
    _os.utime(legacy, (target_mtime, target_mtime))

    migrate_legacy_pending_breadcrumb(tmp_path)

    events = _read_events(tmp_path)
    written = [e for e in events if e.get("event") == "pending-breadcrumb-written"]
    assert len(written) == 1
    event_ts = datetime.fromisoformat(written[0]["ts"].replace("Z", "+00:00"))
    target_dt = datetime.fromtimestamp(target_mtime, tz=UTC)
    delta = abs((event_ts - target_dt).total_seconds())
    assert delta < 2.0, (
        f"migrated event ts should match file mtime within 2s; "
        f"event={event_ts}, mtime={target_dt}, delta={delta}"
    )


def test_legacy_migration_on_missing_file_is_noop(tmp_path: Path) -> None:
    """No legacy file → no events emitted, no exception."""
    migrate_legacy_pending_breadcrumb(tmp_path)
    events = _read_events(tmp_path)
    written = [e for e in events if e.get("event") == "pending-breadcrumb-written"]
    assert written == []


# ---------------------------------------------------------------------------
# Cleanup: no direct pending-breadcrumb.txt writes in the codebase anymore
# ---------------------------------------------------------------------------


def test_no_direct_writes_to_legacy_file_remain() -> None:
    """No code path writes to pending-breadcrumb.txt anymore.

    Narrower than a filename grep (which catches innocent docstring
    mentions): targets file I/O operations specifically.
    """
    import subprocess
    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [
            "grep", "-rEn",
            r"(write_text|open.*[\"']w[\"'])\s*\(.*pending-breadcrumb",
            str(repo / "lib"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert not result.stdout.strip(), (
        f"no code should write to pending-breadcrumb.txt anymore; found:\n"
        f"{result.stdout}"
    )
