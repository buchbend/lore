"""``lore status`` — unified health dashboard (issue #193).

The single glanceable "is Lore healthy right now?" surface. One line per
concern across six sections, every alert paired with the exact drill-down
command (PRD 0005, "Dashboard grammar"):

    lore: active · private/proj:test · attached at ~/project

    capture
      · Hook         12m ago · session-start · captured
      · Session      not loaded in this shell

    wikis
      · private      clean · ahead 0 · behind 0 · reachable

    flags
      · private      written 3 · pending 1 · accepted 2 · declined 0

    retention
      · hot 0.0/10 MB · janitor 5m ago

    news
      · nothing new

    alerts
      · none

Every row here reflects a state some code writes. A row whose producer is
gone is a defect, not a blank: issue #377 removed the four capture rows
and the flushes panel the compose pipeline used to feed.

Reads only — the event spine (#185), retention janitor status (#190), the
flag spine (#360, ``lore_core.flag_metrics``), and per-wiki git state.
Absorbs ``lore news`` (drain events for the current session, cursor
advance preserved). Rendered as plain text (no ANSI) so it degrades
cleanly for non-TTY / ``--plain`` and scripting. Exit code is 0 when
healthy and nonzero when any alert is earned.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer
from lore_core.capture_state import CaptureState, query_capture_state
from lore_core.config import get_lore_root, list_wikis
from lore_core.timefmt import relative_time
from rich.console import Console

console = Console()

app = typer.Typer(
    add_completion=False,
    help="Lore health dashboard — capture, wikis, flags, retention, news, alerts.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

_HEALTHY = "·"
_WARN = "!"
_ERROR = "x"


def _line(glyph: str, message: str) -> str:
    return f"  {glyph} {message}"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _resolve_now() -> datetime:
    """Allow tests to pin `now` via _LORE_STATUS_NOW env var."""
    env = os.environ.get("_LORE_STATUS_NOW")
    if env:
        try:
            parsed = datetime.fromisoformat(env.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


def _format_scope_root(p: Path | None) -> str:
    if p is None:
        return "?"
    try:
        home = Path.home()
        if p.is_relative_to(home):
            return "~/" + str(p.relative_to(home))
    except (AttributeError, ValueError):
        pass
    return str(p)


def _session_loaded_ts(now: datetime) -> datetime | None:
    """Newest mtime in ``~/.cache/lore/sessions/`` within the last hour."""
    cache_env = os.environ.get("LORE_CACHE")
    cache_dir = Path(cache_env).expanduser() if cache_env else Path.home() / ".cache" / "lore"
    sessions_dir = cache_dir / "sessions"
    if not sessions_dir.is_dir():
        return None
    newest_mtime: float | None = None
    try:
        for p in sessions_dir.iterdir():
            if not p.is_file():
                continue
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if newest_mtime is None or m > newest_mtime:
                newest_mtime = m
    except OSError:
        return None
    if newest_mtime is None:
        return None
    ts = datetime.fromtimestamp(newest_mtime, tz=UTC)
    if (now - ts) > timedelta(hours=1):
        return None
    return ts


# ---------------------------------------------------------------------------
# capture section
# ---------------------------------------------------------------------------


def _render_hook(state: CaptureState, now: datetime) -> tuple[str, str]:
    ts = state.last_hook_event_ts
    if ts is None:
        return (_HEALTHY, "Hook         —")
    when = relative_time(ts, now=now)
    kind = state.last_hook_event_kind or "?"
    outcome = state.last_hook_event_outcome or "?"
    return (_HEALTHY, f"Hook         {when} · {kind} · {outcome}")


def _render_session(now: datetime) -> tuple[str, str]:
    ts = _session_loaded_ts(now)
    if ts is None:
        return (_HEALTHY, "Session      not loaded in this shell")
    return (_HEALTHY, f"Session      loaded {relative_time(ts, now=now)} · /lore:context")


def _capture_lines(state: CaptureState, now: datetime) -> list[str]:
    rows = [
        _render_hook(state, now),
        _render_session(now),
    ]
    return [_line(g, m) for g, m in rows]


# ---------------------------------------------------------------------------
# wikis section — per-wiki connection health (FAST local-first, #193)
# ---------------------------------------------------------------------------


def _render_wiki_line(name: str, offline: bool, lore_root: Path) -> str:
    from lore_core.git_sync import wiki_health

    h = wiki_health(lore_root / "wiki" / name, offline=offline)
    if not h.is_repo:
        return _line(_HEALTHY, f"{name:12s} local (no git)")
    state = "dirty" if h.dirty else "clean"
    if not h.has_remote:
        return _line(_HEALTHY, f"{name:12s} {state} · no remote")
    parts = [state, f"ahead {h.ahead}", f"behind {h.behind}"]
    if not offline:
        if h.reachable is True:
            parts.append("reachable")
        elif h.reachable is False:
            parts.append("unreachable")
    return _line(_HEALTHY, f"{name:12s} " + " · ".join(parts))


def _wikis_lines(lore_root: Path, offline: bool) -> list[str]:
    dirs = list_wikis(lore_root)
    if not dirs:
        return [_line(_HEALTHY, "no wikis")]
    return [_render_wiki_line(d.name, offline, lore_root) for d in dirs]


# ---------------------------------------------------------------------------
# flags section — per-wiki counters from the flag spine (#360)
#
# The flag is the crossing from a private session to the team wiki, and the
# only one (PRD 0011); under-flagging is invisible without this. Reads only —
# lore_core.flag_metrics aggregates flag.py's own spine events plus
# flag.count_pending (never reconstructed, ADR 0008).
# ---------------------------------------------------------------------------


def _render_flags_line(lore_root: Path, wiki_name: str) -> str:
    from lore_core.flag_metrics import flag_counts

    c = flag_counts(lore_root, wiki_name)
    withheld = f" ({c.withheld} withheld)" if c.withheld else ""
    retargeted = f" · retargeted {c.retargeted}" if c.retargeted else ""
    msg = (
        f"{wiki_name:12s} written {c.written}{withheld} · pending {c.pending} "
        f"· accepted {c.accepted} · declined {c.declined}{retargeted}"
    )
    return _line(_HEALTHY, msg)


def _flags_lines(lore_root: Path) -> list[str]:
    dirs = list_wikis(lore_root)
    if not dirs:
        return [_line(_HEALTHY, "no wikis")]
    return [_render_flags_line(lore_root, d.name) for d in dirs]


# ---------------------------------------------------------------------------
# retention section — consumes #190's read_janitor_status
# ---------------------------------------------------------------------------


def _retention_lines(lore_root: Path, now: datetime) -> list[str]:
    from lore_core.janitor import read_janitor_status
    from lore_core.root_config import load_root_config

    cap_mb = load_root_config(lore_root).observability.hook_events.max_size_mb
    spine = lore_root / ".lore" / "spine.jsonl"
    try:
        hot_bytes = spine.stat().st_size
    except OSError:
        hot_bytes = 0
    hot_mb = hot_bytes / (1024 * 1024)

    status = read_janitor_status(lore_root)
    if status and status.get("last_run_at"):
        janitor = f"janitor {relative_time(status['last_run_at'], now=now)}"
    else:
        janitor = "janitor never run"

    failed = (status or {}).get("failed", 0) or 0
    glyph = _WARN if failed else _HEALTHY
    msg = f"hot {hot_mb:.1f}/{cap_mb} MB · {janitor}"
    if failed:
        msg += f" · {failed} failed deletion(s)"
    return [_line(glyph, msg)]


# ---------------------------------------------------------------------------
# news section — absorbs the former `lore news` (cursor advance preserved)
# ---------------------------------------------------------------------------

# Human-facing labels for the drain's machine event vocabulary. Display-only.
_NEWS_COPY = {
    "note-filed": "new note",
    "note-appended": "added to today's note",
    "surface-proposed": "surface proposed",
    "transcript-synced": "transcript synced",
}


def _collect_drain_events(lore_root, session_id, cutoff, wiki, limit):
    """Load session + system drain events since ``cutoff``, filtered by ``wiki``."""
    from lore_core.drain import SYSTEM_SESSION, DrainStore

    session_store = DrainStore(lore_root, session_id)
    system_store = DrainStore(lore_root, SYSTEM_SESSION)
    session_events = session_store.read(since=cutoff, limit=limit)
    system_events = system_store.read(since=cutoff, limit=limit)
    if wiki:
        session_events = [e for e in session_events if e.wiki == wiki]
        system_events = [e for e in system_events if e.wiki == wiki]
    return session_store, system_store, session_events, system_events


def _advance_drain_cursor(store, events) -> None:
    """Stamp the store's cursor at the newest ts in ``events``; no-op if empty."""
    if events:
        store.write_cursor(max(e.ts for e in events))


def _news_lines(lore_root: Path, cwd: Path) -> list[str]:
    from lore_core.drain import DrainStore, resolve_session_id

    sid = resolve_session_id(cwd)[0]
    cutoff = DrainStore(lore_root, sid).read_cursor()
    session_store, system_store, session_events, system_events = _collect_drain_events(
        lore_root, sid, cutoff, None, limit=10
    )
    events = session_events + system_events
    if not events:
        lines = [_line(_HEALTHY, "nothing new")]
    else:
        lines = []
        for e in events:
            label = _NEWS_COPY.get(e.event, e.event)
            wiki = f" ({e.wiki})" if e.wiki else ""
            wikilink = e.data.get("wikilink")
            detail = f" {wikilink}" if wikilink else ""
            lines.append(_line(_HEALTHY, f"{label}{wiki}{detail}"))

    # Advance both cursors so each event surfaces once (news semantics).
    _advance_drain_cursor(session_store, session_events)
    _advance_drain_cursor(system_store, system_events)
    return lines


# ---------------------------------------------------------------------------
# alerts section — every earned warn/error paired with its drill-down cmd
# ---------------------------------------------------------------------------


def _diverged_wikis(lore_root: Path) -> list[str]:
    from lore_core.git_sync import is_diverged

    diverged: list[str] = []
    for wiki_dir in list_wikis(lore_root):
        try:
            if is_diverged(wiki_dir):
                diverged.append(wiki_dir.name)
        except Exception:  # noqa: BLE001 — never fail status on a git probe
            continue
    return diverged


def _compute_alerts(state: CaptureState, now: datetime, lore_root: Path) -> list[str]:
    """Return earned alert lines (already glyph-prefixed), each naming a fix."""
    from lore_core.janitor import read_janitor_status

    alerts: list[str] = []

    if (
        state.spine_write_failed_marker_age_s is not None
        and state.spine_write_failed_marker_age_s < 86400
    ):
        when = relative_time(
            now - timedelta(seconds=state.spine_write_failed_marker_age_s), now=now
        )
        alerts.append(
            f"{_ERROR} spine write failed {when} — check disk / permissions (lore doctor)"
        )

    if state.simple_tier_fallback_active:
        alerts.append(f"{_WARN} simple-tier fallback active — high tier unavailable (lore doctor)")

    # Subprocess role logs with error tails. The drill-down is the log
    # itself: `lore trace` correlates a flush, and no flush produced this.
    proc_dir = lore_root / ".lore" / "proc"
    if proc_dir.is_dir():
        for role_log in sorted(proc_dir.glob("*.log")):
            if role_log.name.endswith(".log.1"):
                continue
            try:
                if role_log.stat().st_size == 0:
                    continue
                text = role_log.read_bytes()[-2048:].decode("utf-8", errors="replace")
                if any(m in text for m in ("Traceback", "Error:", "FATAL")):
                    role = role_log.stem
                    alerts.append(
                        f"{_ERROR} subprocess {role} has errors — see {role_log}"
                    )
            except OSError:
                pass

    # Retention janitor delete failures.
    js = read_janitor_status(lore_root)
    if js and (js.get("failed", 0) or 0) > 0:
        alerts.append(
            f"{_WARN} retention janitor had {js['failed']} failed deletion(s) — lore doctor"
        )

    # Cross-host divergence — auto_pull skips diverged trees silently.
    for wiki in _diverged_wikis(lore_root):
        alerts.append(f"{_WARN} wiki [[{wiki}]] diverged from origin — git pull manually")

    # Plugin-cache drift — reuse doctor's canonical check (guarded: it is a
    # sibling-owned private, and this alert is best-effort). Drill-down: doctor.
    try:
        from lore_cli.doctor_cmd import _check_claude_plugin_cache_drift

        ok, _msg = _check_claude_plugin_cache_drift(str(lore_root))
        if not ok:
            alerts.append(f"{_WARN} plugin cache drift — lore doctor")
    except Exception:  # noqa: BLE001 — never fail status on the drift probe
        pass

    return alerts


# ---------------------------------------------------------------------------
# unattached copy + json
# ---------------------------------------------------------------------------


def _render_unattached(lore_root: Path, cwd: Path) -> str:
    from lore_core.config import get_wiki_root

    vault_names: list[str] = []
    try:
        wiki_root = get_wiki_root()
        for d in list_wikis(wiki_root.parent):
            vault_names.append(f"{d.name}/lore at {_format_scope_root(d.parent.parent)}")
    except Exception:  # noqa: BLE001
        pass

    vaults_str = ", ".join(vault_names) if vault_names else f"none found in {lore_root}"
    cwd_str = _format_scope_root(cwd)
    return (
        "lore: not attached here\n"
        "\n"
        f"  cwd {cwd_str} is not inside a configured wiki.\n"
        "  Run /lore:attach to bind this folder, or cd into an attached vault.\n"
        f"  (Configured vaults: {vaults_str})"
    )


def _state_to_json(state: CaptureState) -> dict:
    def _default(obj):
        if isinstance(obj, datetime):
            return obj.isoformat().replace("+00:00", "Z")
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, tuple):
            return list(obj)
        raise TypeError(f"not serializable: {type(obj).__name__}")

    return json.loads(json.dumps(asdict(state), default=_default))


def _json_payload(state: CaptureState, lore_root: Path, now: datetime, offline: bool) -> dict:
    from lore_core.git_sync import wiki_health
    from lore_core.janitor import read_janitor_status

    data = _state_to_json(state)
    data["retention"] = read_janitor_status(lore_root)
    wikis: list[dict] = []
    for d in list_wikis(lore_root):
        h = wiki_health(d, offline=offline)
        wikis.append(
            {
                "wiki": d.name,
                "is_repo": h.is_repo,
                "has_remote": h.has_remote,
                "dirty": h.dirty,
                "ahead": h.ahead,
                "behind": h.behind,
                "reachable": h.reachable,
            }
        )
    data["wikis"] = wikis
    from lore_core.flag_metrics import flag_counts

    data["flags"] = {d.name: asdict(flag_counts(lore_root, d.name)) for d in list_wikis(lore_root)}
    data["alerts"] = _compute_alerts(state, now, lore_root)
    return data


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def status(
    cwd: str = typer.Option(None, "--cwd", help="Directory to resolve scope from (default: $PWD)."),
    json_out: bool = typer.Option(False, "--json", help="Emit the dashboard state as JSON."),
    offline: bool = typer.Option(
        False, "--offline", help="Skip wiki network reachability probes (local-first)."
    ),
    plain: bool = typer.Option(
        False, "--plain", help="Force plain output (default for non-TTY / scripting)."
    ),
) -> None:
    """Lore health dashboard — is Lore healthy right now?"""
    resolved_cwd = Path(cwd) if cwd else Path(os.getcwd())
    now = _resolve_now()

    try:
        lore_root = get_lore_root()
    except Exception:  # noqa: BLE001
        console.print("[red]LORE_ROOT not set.[/red] Run `lore init` or export $LORE_ROOT.")
        raise typer.Exit(1) from None

    state = query_capture_state(lore_root, cwd=resolved_cwd, now=now)

    if json_out:
        payload = _json_payload(state, lore_root, now, offline)
        print(json.dumps(payload, indent=2))
        if payload["alerts"]:
            raise typer.Exit(1)
        return

    if not state.scope_attached:
        print(_render_unattached(lore_root, resolved_cwd))
        return

    alerts = _compute_alerts(state, now, lore_root)

    lines: list[str] = [
        f"lore: active · {state.scope_name} · attached at {_format_scope_root(state.scope_root)}",
        "",
        "capture",
        *_capture_lines(state, now),
        "",
        "wikis" + ("  (offline)" if offline else ""),
        *_wikis_lines(lore_root, offline),
        "",
        "flags",
        *_flags_lines(lore_root),
        "",
        "retention",
        *_retention_lines(lore_root, now),
        "",
        "news",
        *_news_lines(lore_root, resolved_cwd),
        "",
        "alerts",
    ]
    if alerts:
        lines.extend(f"  {a}" for a in alerts)
    else:
        lines.append(_line(_HEALTHY, "none"))

    print("\n".join(lines))

    if alerts:
        raise typer.Exit(1)


# argv_main shim for the main CLI dispatcher (lore_cli.__main__).
from lore_cli._argv_compat import argv_main  # noqa: E402

main = argv_main(app)
