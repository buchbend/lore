"""Mid-session heartbeat: drain notification + turn-aware curator spawn.

Runs on every user prompt, so both entry points are cooldown-gated on their
own stamp file before doing any real work.

The spawn/stamp primitives are injected rather than imported. They live in
the CLI layer (which owns subprocess launching), and the layering fence only
permits ``lore_cli`` → ``lore_curator``, never the reverse. Passing the three
callables in keeps the scheduling logic here, the process handling there, and
the caller's module globals as the single monkeypatch seam.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from lore_core.drain_banner import format_drain_summary, tally_drain
from lore_core.ledger import TranscriptLedger
from lore_core.wiki_config import load_wiki_config

from lore_curator.capture_routing import now_utc, wiki_should_spawn

if TYPE_CHECKING:
    from lore_core.types import Scope
    from lore_core.wiki_config import WikiConfig


def read_cursor(path: Path) -> datetime | None:
    """Read a drain cursor file. Returns None if missing or unparseable."""
    if not path.exists():
        return None
    try:
        raw = path.read_text().strip()
        if raw:
            return datetime.fromisoformat(raw).replace(tzinfo=UTC)
    except (OSError, ValueError):
        pass
    return None


def write_cursor(path: Path, ts: datetime) -> None:
    """Atomic cursor write; best-effort."""
    try:
        tmp = path.with_suffix(".cursor.tmp")
        tmp.write_text((ts + timedelta(microseconds=1)).isoformat())
        os.replace(tmp, path)
    except OSError:
        pass


def heartbeat(
    lore_root: Path,
    cwd: Path,
    wiki_cfg: WikiConfig,
    *,
    pid: int | None = None,
    stamp_within_cooldown: Callable[[Path, int], bool],
    write_stamp: Callable[[Path], None],
    resolve_pid: Callable[[], int | None],
) -> tuple[str | None, str | None]:
    """Check drain for new events; return (system_message, additional_context).

    Reads both the system drain (background work) and session-scoped
    drain (notes filed for this session). Both may be None. Cooldown-
    gated: returns (None, None) when the stamp is fresh.
    """
    from lore_core.drain import SYSTEM_SESSION, DrainStore, resolve_session_id

    hb = wiki_cfg.heartbeat
    if not hb.enabled:
        return None, None

    stamp = lore_root / ".lore" / "curator-heartbeat.spawn.stamp"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    if stamp_within_cooldown(stamp, hb.cooldown_s):
        return None, None

    effective_pid = pid or resolve_pid() or os.getpid()
    drain_dir = lore_root / ".lore" / "drain"
    drain_dir.mkdir(parents=True, exist_ok=True)

    # Session cursor stays pid-keyed: each Claude OS process tracks its
    # own "have I shown this session event" mark, so two parallel
    # windows don't steal each other's notifications.
    sess_cursor_path = drain_dir / f"heartbeat-session-{effective_pid}.cursor"
    sess_cursor_ts = read_cursor(sess_cursor_path)

    # System cursor is process-shared: events surface to whichever
    # reader (SessionStart or any heartbeat) gets there first, then
    # never again. ``read_or_init_cursor`` cold-starts to ``now`` so a
    # fresh install never reaches back through history.
    system_store = DrainStore(lore_root, SYSTEM_SESSION)
    sys_cursor_ts = system_store.read_or_init_cursor()
    system_events = system_store.read(since=sys_cursor_ts, limit=200)

    sid, _ = resolve_session_id(cwd)
    session_store = DrainStore(lore_root, sid)
    session_events = session_store.read(since=sess_cursor_ts, limit=200)

    events = system_events + session_events

    if not events:
        write_stamp(stamp)
        return None, None

    counts = tally_drain(events)
    summary = format_drain_summary(counts, events)
    sys_msg = f"lore: {summary}" if summary else None

    ctx = None
    if hb.push_context and events:
        wikilinks = []
        for e in events:
            wl = e.data.get("wikilink")
            if wl:
                wikilinks.append(wl)
        if wikilinks:
            ctx = "New in vault: " + ", ".join(dict.fromkeys(wikilinks))

    if system_events:
        newest = max(e.ts for e in system_events)
        system_store.write_cursor(newest + timedelta(microseconds=1))
    if session_events:
        write_cursor(sess_cursor_path, max(e.ts for e in session_events))

    write_stamp(stamp)
    return sys_msg, ctx


def spawn_curator_a_if_due(
    lore_root: Path,
    scope: Scope,
    *,
    cooldown_s: int = 120,
    stamp_within_cooldown: Callable[[Path, int], bool],
    write_stamp: Callable[[Path], None],
    spawn_curator_a: Callable[..., bool],
) -> str | None:
    """Evaluate the spawn-gate for the current scope's wiki; spawn if it crosses.

    Called from the UserPromptSubmit hook after the drain heartbeat. This is
    the mid-session snappy lever: long active sessions hit this every prompt,
    so accumulated turn count or stale pending-age can trigger Curator A
    without waiting for the next session-start/end boundary.

    Independent 120s cooldown stamp (``curator-heartbeat-spawn.stamp``) so
    this never thrashes regardless of prompt cadence. The actual spawn also
    runs through its own 60s lock+stamp, so two layers of rate-limiting
    prevent storms.

    Returns a reason string for telemetry, or None when no spawn was made.
    """
    stamp = lore_root / ".lore" / "curator-heartbeat-spawn.stamp"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    if stamp_within_cooldown(stamp, cooldown_s):
        return None

    try:
        tledger = TranscriptLedger(lore_root)
        buckets = tledger.pending_by_wiki()
    except Exception:
        return None

    entries = buckets.get(scope.wiki, [])
    if not entries:
        write_stamp(stamp)
        return None

    try:
        wiki_cfg = load_wiki_config(lore_root / "wiki" / scope.wiki)
    except Exception:
        return None

    should, reason = wiki_should_spawn(entries, wiki_cfg, now=now_utc())
    write_stamp(stamp)
    if not should:
        return None

    spawned = spawn_curator_a(
        lore_root, cooldown_s=wiki_cfg.curator.curator_a_cooldown_s
    )
    return reason if spawned else None
