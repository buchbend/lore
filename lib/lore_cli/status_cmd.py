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


def _render_hook(state: CaptureState, now: datetime) -> tuple[str, str]:
    """Liveness of the capture hook itself.

    Answers "did Claude Code actually invoke my SessionStart/SessionEnd
    hook recently?" — which is structurally different from "did curator
    run?" (runs can be triggered manually; hooks can't).
    """
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
    return (
        _HEALTHY,
        f"Session      loaded {relative_time(ts, now=now)} · /lore:context",
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

    # Work is waiting but the capture hook isn't leaving traces — strong
    # signal that Claude Code is not invoking the hook (plugin install
    # issue, silent scope-resolution failure, timeout kill, etc.).
    if state.pending_transcripts > 0:
        ts = state.last_hook_event_ts
        stale = ts is None or (now - ts) > timedelta(hours=24)
        if stale:
            alerts.append(
                f"{_WARN} no hook events in 24h while {state.pending_transcripts} "
                f"transcript(s) pending — capture hook may not be firing "
                f"(try `lore doctor`)"
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


# ---------------------------------------------------------------------------
# Verbose sections
# ---------------------------------------------------------------------------

_OVERDUE_A_S = 86400      # 24h
_OVERDUE_C_S = 7 * 86400  # 7d


def _render_verbose_curator_schedule(lore_root: Path, now: datetime) -> list[str]:
    from lore_core.ledger import WikiLedger

    lines = ["  Curator Schedule"]
    lore_dir = lore_root / ".lore"
    wikis: list[str] = []
    try:
        for p in sorted(lore_dir.glob("wiki-*-ledger.json")):
            name = p.stem.removeprefix("wiki-").removesuffix("-ledger")
            wikis.append(name)
    except OSError:
        pass

    if not wikis:
        lines.append("no wiki ledgers")
        return lines

    for wiki in wikis:
        entry = WikiLedger(lore_root, wiki).read()
        parts: list[str] = []
        for role, ts, threshold_s in [
            ("A", entry.last_curator_a, _OVERDUE_A_S),
            ("B", entry.last_curator_b, _OVERDUE_A_S),
            ("C", entry.last_curator_c, _OVERDUE_C_S),
        ]:
            if ts is None:
                parts.append(f"{role} —")
            else:
                rel = relative_time(ts, now=now, short=True)
                age_s = (now - ts).total_seconds()
                marker = " !" if age_s > threshold_s else ""
                parts.append(f"{role} {rel}{marker}")
        lines.append(f"    {wiki:12s}  {'  '.join(parts)}")
    return lines


def _render_verbose_recent_hooks(lore_root: Path, n: int = 5) -> list[str]:
    lines = ["  Recent Hooks"]
    events_path = lore_root / ".lore" / "hook-events.jsonl"
    if not events_path.exists():
        lines.append("no hook events")
        return lines

    records: list[dict] = []
    try:
        for raw_line in events_path.read_text().splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                records.append(json.loads(raw_line))
            except json.JSONDecodeError:
                continue
    except OSError:
        lines.append("read error")
        return lines

    tail = records[-n:]
    now = _resolve_now()
    for rec in tail:
        ts_raw = rec.get("ts")
        event = rec.get("event", "?")
        outcome = rec.get("outcome", "?")
        if ts_raw:
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                rel = relative_time(ts, now=now, short=True)
            except (ValueError, TypeError):
                rel = "?"
        else:
            rel = "?"
        lines.append(f"    {rel:>5}  {event}  {outcome}")
    return lines


def _render_verbose_pending(lore_root: Path) -> list[str]:
    from lore_core.ledger import TranscriptLedger, WikiLedger

    lines = ["  Pending Detail"]
    try:
        ledger = TranscriptLedger(lore_root)
        buckets = ledger.pending_by_wiki()
    except Exception:
        lines.append("unavailable")
        return lines

    if not buckets:
        lines.append("no pending transcripts")
        return lines

    for wiki, entries in sorted(buckets.items()):
        n = len(entries)
        label = "transcript" if n == 1 else "transcripts"
        token_str = ""
        if wiki not in ("__orphan__", "__unattached__"):
            try:
                wl_entry = WikiLedger(lore_root, wiki).read()
                if wl_entry.pending_tokens_est > 0:
                    token_str = f"  ~{wl_entry.pending_tokens_est // 1000}k tokens"
            except Exception:
                pass
        lines.append(f"    {wiki:12s}  {n} {label}{token_str}")
    return lines


def _verbose_json_data(lore_root: Path, now: datetime) -> dict:
    from lore_core.ledger import TranscriptLedger, WikiLedger

    wiki_schedules: list[dict] = []
    lore_dir = lore_root / ".lore"
    try:
        for p in sorted(lore_dir.glob("wiki-*-ledger.json")):
            name = p.stem.removeprefix("wiki-").removesuffix("-ledger")
            entry = WikiLedger(lore_root, name).read()
            wiki_schedules.append({
                "wiki": name,
                "last_curator_a": entry.last_curator_a.isoformat() if entry.last_curator_a else None,
                "last_curator_b": entry.last_curator_b.isoformat() if entry.last_curator_b else None,
                "last_curator_c": entry.last_curator_c.isoformat() if entry.last_curator_c else None,
                "pending_tokens_est": entry.pending_tokens_est,
            })
    except OSError:
        pass

    recent_hooks: list[dict] = []
    events_path = lore_root / ".lore" / "hook-events.jsonl"
    if events_path.exists():
        try:
            raw_lines = events_path.read_text().splitlines()
            for line in raw_lines[-5:]:
                line = line.strip()
                if line:
                    try:
                        recent_hooks.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except OSError:
            pass

    pending: dict[str, int] = {}
    try:
        buckets = TranscriptLedger(lore_root).pending_by_wiki()
        pending = {k: len(v) for k, v in buckets.items()}
    except Exception:
        pass

    return {
        "wiki_schedules": wiki_schedules,
        "recent_hooks": recent_hooks,
        "pending_by_wiki": pending,
    }


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
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show curator schedule, recent hooks, and pending breakdown."),
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
        data = json.loads(_state_to_json(state))
        if verbose:
            data["verbose"] = _verbose_json_data(lore_root, now)
        print(json.dumps(data, indent=2))
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
        _render_hook(state, now),
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

    if verbose:
        lines.append("")
        lines.extend(_render_verbose_curator_schedule(lore_root, now))
        lines.append("")
        lines.extend(_render_verbose_recent_hooks(lore_root))
        lines.append("")
        lines.extend(_render_verbose_pending(lore_root))

    print("\n".join(lines))


# argv_main shim for the main CLI dispatcher (lore_cli.__main__).
from lore_cli._compat import argv_main  # noqa: E402

main = argv_main(app)
