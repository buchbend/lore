"""``lore status`` — activity-first liveness surface.

The single "is lore doing anything for me right now?" command. Renders
``CaptureState`` (from ``lore_core.capture_state``) with decay-first
line ordering and loud-on-earning alerts.

Output shape on a healthy vault (exactly 7 newlines)::

    lore: active · private/proj:test · attached at <scope_root>

      · Last note    [[...]] · 18h ago
      · Last run     2h ago · 1 note from 1 transcript
      · Pending      no transcripts
      · Session      not loaded in this shell
      · Lock         free

Loud-on-earning lines are appended below the healthy block only when
thresholds are crossed. No ``--plumbing`` flag — ``lore doctor`` owns
install integrity. This command is strictly about activity.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer
from rich.console import Console

from lore_core.capture_state import CaptureState, query_capture_state
from lore_core.config import get_lore_root
from lore_core.timefmt import relative_time


console = Console()

app = typer.Typer(
    add_completion=False,
    help="Is lore doing anything for me right now? Shows activity, staleness, and alerts.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

_HEALTHY = "·"
_WARN = "!"
_ERROR = "x"


def _session_loaded_ts(now: datetime) -> datetime | None:
    """Newest mtime in ``~/.cache/lore/sessions/`` (PID-keyed cache).

    Represents "a Claude session loaded lore recently." Returns None if
    no sessions/ directory or no files within the last hour.
    """
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
    # Only count "loaded" if within the last hour — otherwise the cache
    # file is just stale from a prior session that never cleaned up.
    if (now - ts) > timedelta(hours=1):
        return None
    return ts


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


def _last_two_zero_note_runs(lore_root: Path) -> list[str] | None:
    """Return [short_id1, short_id2] if the last two runs both filed 0 notes.

    Used for the "loud-on-earning" 0-note alert. Returns None otherwise.
    """
    from lore_core.run_reader import iter_archival_runs

    short_ids: list[str] = []
    for path in iter_archival_runs(lore_root, limit=2):
        try:
            lines = path.read_text().splitlines()
        except OSError:
            return None
        end = None
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") == "run-end":
                end = rec
                break
        if end is None:
            return None
        if end.get("notes_new", 0) != 0 or end.get("notes_merged", 0) != 0:
            return None
        short_ids.append(path.stem.split("-")[-1])
    return short_ids if len(short_ids) == 2 else None


def _render_last_note(state: CaptureState, now: datetime) -> tuple[str, str]:
    """Return (glyph, message) for the Last note line."""
    if state.last_note_filed is None:
        return (_HEALTHY, "Last note    —")
    ts, wikilink = state.last_note_filed
    age = now - ts
    glyph = _HEALTHY
    if age > timedelta(days=3):
        glyph = _ERROR
    elif age > timedelta(hours=24):
        glyph = _WARN
    return (glyph, f"Last note    {wikilink} · {relative_time(ts, now=now)}")


def _render_last_run(state: CaptureState, now: datetime) -> tuple[str, str]:
    a = next(c for c in state.curators if c.role == "a")
    if a.last_run_ts is None:
        return (_HEALTHY, "Last run     —")
    when = relative_time(a.last_run_ts, now=now)
    notes = a.last_run_notes_new if a.last_run_notes_new is not None else 0
    notes_label = "note" if notes == 1 else "notes"
    return (_HEALTHY, f"Last run     {when} · {notes} {notes_label}")


def _render_pending(state: CaptureState) -> tuple[str, str]:
    n = state.pending_transcripts
    if n == 0:
        return (_HEALTHY, "Pending      no transcripts")
    label = "transcript" if n == 1 else "transcripts"
    return (_HEALTHY, f"Pending      {n} {label}")


def _render_session(now: datetime) -> tuple[str, str]:
    ts = _session_loaded_ts(now)
    if ts is None:
        return (_HEALTHY, "Session      not loaded in this shell")
    return (
        _HEALTHY,
        f"Session      loaded {relative_time(ts, now=now)} · /lore:loaded",
    )


def _render_lock(state: CaptureState) -> tuple[str, str]:
    if any(c.work_lock_held for c in state.curators):
        return (_WARN, "Lock         curator running (work lock held)")
    return (_HEALTHY, "Lock         free")


def _render_alerts(state: CaptureState, now: datetime) -> list[str]:
    """Return additional alert lines appended after the healthy block.

    Each entry is already prefixed with its glyph.
    """
    alerts: list[str] = []
    lore_root = state.lore_root

    zero_runs = _last_two_zero_note_runs(lore_root)
    if zero_runs:
        alerts.append(
            f"{_WARN} last 2 runs ({zero_runs[0]}, {zero_runs[1]}) filed 0 notes "
            f"— lore runs show {zero_runs[0]}"
        )

    if state.hook_log_failed_marker_age_s is not None:
        if state.hook_log_failed_marker_age_s < 86400:
            alerts.append(
                f"{_ERROR} hook log write failed "
                f"{relative_time(now - timedelta(seconds=state.hook_log_failed_marker_age_s), now=now)} "
                f"— check disk / permissions"
            )

    if state.simple_tier_fallback_active:
        alerts.append(
            f"{_WARN} simple-tier fallback active — high tier unavailable"
        )

    return alerts


def _render_unattached(lore_root: Path, cwd: Path) -> str:
    """UX-verbatim copy for the unattached-cwd case."""
    from lore_core.config import get_wiki_root

    vault_names: list[str] = []
    try:
        wiki_root = get_wiki_root()
        if wiki_root.exists():
            for d in sorted(wiki_root.iterdir()):
                if d.is_dir():
                    vault_names.append(f"{d.name}/lore at {_format_scope_root(d.parent.parent)}")
    except Exception:
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


def _state_to_json(state: CaptureState) -> str:
    def _default(obj):
        if isinstance(obj, datetime):
            return obj.isoformat().replace("+00:00", "Z")
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, tuple):
            return list(obj)
        raise TypeError(f"not serializable: {type(obj).__name__}")

    return json.dumps(asdict(state), default=_default, indent=2)


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


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def status(
    cwd: str = typer.Option(None, "--cwd", help="Directory to resolve scope from (default: $PWD)."),
    json_out: bool = typer.Option(False, "--json", help="Emit the raw CaptureState as JSON."),
) -> None:
    """Is lore doing anything for me right now?"""
    resolved_cwd = Path(cwd) if cwd else Path(os.getcwd())
    now = _resolve_now()

    try:
        lore_root = get_lore_root()
    except Exception:
        console.print("[red]LORE_ROOT not set.[/red] Run `lore init` or export $LORE_ROOT.")
        raise typer.Exit(1)

    state = query_capture_state(lore_root, cwd=resolved_cwd, now=now)

    if json_out:
        print(_state_to_json(state))
        return

    if not state.scope_attached:
        print(_render_unattached(lore_root, resolved_cwd))
        return

    # Happy-path body — exactly 5 indented lines between header + trailing.
    lines: list[str] = [
        f"lore: active · {state.scope_name} · attached at {_format_scope_root(state.scope_root)}",
        "",
    ]
    for glyph, message in [
        _render_last_note(state, now),
        _render_last_run(state, now),
        _render_pending(state),
        _render_session(now),
        _render_lock(state),
    ]:
        lines.append(f"  {glyph} {message}")

    alerts = _render_alerts(state, now)
    if alerts:
        lines.append("")
        for a in alerts:
            lines.append(f"  {a}")

    print("\n".join(lines))


# argv_main shim for the main CLI dispatcher (lore_cli.__main__).
from lore_cli._compat import argv_main  # noqa: E402

main = argv_main(app)
