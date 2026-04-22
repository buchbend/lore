"""``lore log`` — chronological timeline of hook events and curator runs."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer
from rich.console import Console

from lore_cli._compat import argv_main
from lore_core.timefmt import relative_time

console = Console()

app = typer.Typer(
    add_completion=False,
    help="Chronological timeline of hook events and curator runs.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

_DURATION_RE = re.compile(r"^(\d+)(m|h|d)$")


def _parse_duration(s: str) -> timedelta:
    m = _DURATION_RE.match(s)
    if not m:
        raise typer.BadParameter(f"invalid duration {s!r} (use e.g. 30m, 1h, 2d)")
    val, unit = int(m.group(1)), m.group(2)
    if unit == "m":
        return timedelta(minutes=val)
    if unit == "h":
        return timedelta(hours=val)
    return timedelta(days=val)


@dataclass
class TimelineEntry:
    ts: datetime
    kind: str
    event: str
    outcome: str
    detail: str
    raw: dict


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _read_hook_events(lore_root: Path, since: datetime) -> list[TimelineEntry]:
    path = lore_root / ".lore" / "hook-events.jsonl"
    if not path.exists():
        return []
    entries: list[TimelineEntry] = []
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_ts(rec.get("ts"))
            if ts is None or ts < since:
                continue
            entries.append(TimelineEntry(
                ts=ts,
                kind="hook",
                event=rec.get("event", "?"),
                outcome=rec.get("outcome", "?"),
                detail=f"pid {rec['pid']}" if "pid" in rec else "",
                raw=rec,
            ))
    except OSError:
        pass
    return entries


def _read_run_events(lore_root: Path, since: datetime) -> list[TimelineEntry]:
    from lore_core.run_reader import iter_archival_runs, read_run

    entries: list[TimelineEntry] = []
    try:
        for run_path in iter_archival_runs(lore_root):
            try:
                records = read_run(run_path, strict_schema=False)
            except Exception:
                continue
            if not records:
                continue
            first_ts = _parse_ts(records[0].get("ts"))
            if first_ts and first_ts < since:
                break

            short_id = run_path.stem.split("-")[-1]
            for rec in records:
                rtype = rec.get("type")
                ts = _parse_ts(rec.get("ts"))
                if ts is None or ts < since:
                    continue
                if rtype == "run-start":
                    entries.append(TimelineEntry(
                        ts=ts, kind="run-start", event="curator-a",
                        outcome="started", detail=short_id, raw=rec,
                    ))
                elif rtype == "run-end":
                    notes = rec.get("notes_new", 0)
                    duration = rec.get("duration_ms", 0)
                    entries.append(TimelineEntry(
                        ts=ts, kind="run-end", event="curator-a",
                        outcome=f"{notes} note{'s' if notes != 1 else ''}",
                        detail=f"{duration / 1000:.1f}s",
                        raw=rec,
                    ))
    except Exception:
        pass
    return entries


def _resolve_now() -> datetime:
    env = os.environ.get("_LORE_LOG_NOW")
    if env:
        try:
            parsed = datetime.fromisoformat(env.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


_ICONS = {"hook": "~", "run-start": ">", "run-end": "<"}


@app.callback(invoke_without_command=True)
def log(
    since: str = typer.Option("1h", "--since", help="How far back (e.g. 30m, 1h, 2d)."),
    type_filter: str = typer.Option("all", "--type", help="Filter: hook, run, or all."),
    limit: int = typer.Option(50, "--limit", help="Max entries."),
    json_out: bool = typer.Option(False, "--json", help="Raw JSONL output."),
) -> None:
    """Chronological timeline of hook events and curator runs."""
    from lore_core.config import get_lore_root

    try:
        lore_root = get_lore_root()
    except Exception:
        console.print("[red]LORE_ROOT not set.[/red]")
        raise typer.Exit(1)

    now = _resolve_now()
    cutoff = now - _parse_duration(since)

    entries: list[TimelineEntry] = []
    if type_filter in ("all", "hook"):
        entries.extend(_read_hook_events(lore_root, cutoff))
    if type_filter in ("all", "run"):
        entries.extend(_read_run_events(lore_root, cutoff))

    entries.sort(key=lambda e: e.ts)
    entries = entries[-limit:]

    if not entries:
        console.print(f"[dim]No activity in the last {since}.[/dim]")
        return

    if json_out:
        for e in entries:
            print(json.dumps(e.raw))
        return

    for e in entries:
        rel = relative_time(e.ts, now=now, short=True)
        icon = _ICONS.get(e.kind, "?")
        detail_str = f" — {e.detail}" if e.detail else ""
        console.print(f"  {rel:>5}  {icon} {e.event} — {e.outcome}{detail_str}")


main = argv_main(app)
