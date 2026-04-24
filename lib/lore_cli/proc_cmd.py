"""`lore proc` — view raw subprocess output logs."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from lore_core.timefmt import relative_time

app = typer.Typer(
    add_completion=False,
    help=(
        "View raw subprocess output logs.\n\n"
        "Scenarios:\n"
        "  curator crashed?        lore proc show a\n"
        "  import error?           lore proc show a\n"
        "  transcript sync?        lore proc show transcripts\n"
        "  previous run's output?  lore proc show a --prev"
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()

_ROLES = ("a", "b", "c", "transcripts")
_ERROR_MARKERS = ("Traceback", "Error:", "FATAL")
_POLL_INTERVAL_S = 0.2
_IDLE_TIMEOUT_S = 30 * 60


def _proc_dir(lore_root: Path) -> Path:
    return lore_root / ".lore" / "proc"


def _get_lore_root() -> Path:
    from lore_core.config import get_lore_root
    return get_lore_root()


def _has_errors(path: Path) -> bool:
    try:
        tail = path.read_bytes()[-2048:]
        text = tail.decode("utf-8", errors="replace")
        return any(m in text for m in _ERROR_MARKERS)
    except OSError:
        return False


def _read_meta_summary(proc_dir: Path, role: str, gen: int = 0) -> tuple[str, str]:
    suffix = f".{gen}" if gen > 0 else ""
    meta_path = proc_dir / f"{role}.meta.json{suffix}"
    if not meta_path.exists():
        return ("—", "—")
    try:
        meta = json.loads(meta_path.read_text())
        ec = meta.get("exit_code")
        exit_str = str(ec) if ec is not None else "running"
        start = meta.get("start_ts", 0)
        end = meta.get("end_ts")
        dur_str = f"{end - start:.1f}s" if end else "—"
        return (exit_str, dur_str)
    except (json.JSONDecodeError, OSError):
        return ("?", "?")


def _format_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


@app.command("list")
def list_logs() -> None:
    """List subprocess log files with size, age, and error status."""
    lore_root = _get_lore_root()
    pdir = _proc_dir(lore_root)
    now = datetime.now(UTC)

    if not pdir.is_dir():
        console.print("[dim]No subprocess logs yet.[/dim]")
        return

    table = Table(title=None)
    table.add_column("Role")
    table.add_column("Size")
    table.add_column("Modified")
    table.add_column("Exit")
    table.add_column("Duration")
    table.add_column("Status")

    found = False
    for role in _ROLES:
        log = pdir / f"{role}.log"
        if not log.exists():
            continue
        found = True
        try:
            stat = log.stat()
            size = stat.st_size
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
            rel = relative_time(mtime, now=now)
            if size == 0:
                status = "[dim]empty[/dim]"
            elif _has_errors(log):
                status = "[red]errors detected[/red]"
            else:
                status = "ok"
            exit_str, dur_str = _read_meta_summary(pdir, role)
            table.add_row(role, _format_size(size), rel, exit_str, dur_str, status)
        except OSError:
            table.add_row(role, "?", "?", "?", "?", "[yellow]read error[/yellow]")

    if not found:
        console.print("[dim]No subprocess logs yet.[/dim]")
        return

    console.print(table)


@app.command("show")
def show(
    role: str = typer.Argument(..., help="Subprocess role: a, b, c, or transcripts"),
    prev: bool = typer.Option(False, "--prev", help="Show the previous run's log (.log.1)."),
    gen: int = typer.Option(0, "--gen", "-g", help="Generation (0=current, 1=previous, 2=...)."),
    lines: int = typer.Option(0, "--lines", "-n", help="Limit to last N lines (0 = all)."),
) -> None:
    """Print subprocess log content."""
    if role not in _ROLES:
        console.print(f"[red]Unknown role {role!r}. Choose from: {', '.join(_ROLES)}[/red]")
        raise typer.Exit(code=1)

    effective_gen = 1 if prev else gen
    lore_root = _get_lore_root()
    pdir = _proc_dir(lore_root)
    suffix = f".log.{effective_gen}" if effective_gen > 0 else ".log"
    path = pdir / f"{role}{suffix}"

    if not path.exists():
        label = f"gen {effective_gen} " if effective_gen > 0 else ""
        console.print(f"[dim]No {label}log for {role}.[/dim]")
        return

    # Print metadata header if sidecar exists.
    meta_suffix = f".{effective_gen}" if effective_gen > 0 else ""
    meta_path = pdir / f"{role}.meta.json{meta_suffix}"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            ec = meta.get("exit_code")
            pid = meta.get("pid", "?")
            start = meta.get("start_ts", 0)
            end = meta.get("end_ts")
            dur = f"{end - start:.1f}s" if end else "—"
            console.print(f"[dim]pid={pid}  exit={ec}  duration={dur}[/dim]")
        except (json.JSONDecodeError, OSError):
            pass

    try:
        text = path.read_text(errors="replace")
    except OSError as e:
        console.print(f"[red]Cannot read {path}: {e}[/red]")
        raise typer.Exit(code=1)

    if not text.strip():
        console.print(f"[dim]{role} log is empty (clean run).[/dim]")
        return

    if lines > 0:
        text = "\n".join(text.splitlines()[-lines:])

    console.print(text, highlight=False)


@app.command("tail")
def tail(
    role: str = typer.Argument(..., help="Subprocess role: a, b, c, or transcripts"),
) -> None:
    """Follow a subprocess log file. Exit with Ctrl+C."""
    if role not in _ROLES:
        console.print(f"[red]Unknown role {role!r}. Choose from: {', '.join(_ROLES)}[/red]")
        raise typer.Exit(code=1)

    lore_root = _get_lore_root()
    path = _proc_dir(lore_root) / f"{role}.log"

    if not path.exists():
        console.print(
            f"[dim]No log for {role} yet. Waiting for next spawn...[/dim]"
        )

    pos = 0
    idle_since = time.monotonic()

    try:
        while True:
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                if time.monotonic() - idle_since > _IDLE_TIMEOUT_S:
                    console.print("[yellow]no log file after 30min — exiting.[/yellow]")
                    return
                time.sleep(_POLL_INTERVAL_S)
                continue

            if size < pos:
                pos = 0

            if size > pos:
                with path.open("r", errors="replace") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                if chunk:
                    console.print(chunk, end="", highlight=False)
                idle_since = time.monotonic()

            if time.monotonic() - idle_since > _IDLE_TIMEOUT_S:
                console.print(
                    "[yellow]no new output for 30min — exiting.[/yellow]"
                )
                return

            time.sleep(_POLL_INTERVAL_S)
    except KeyboardInterrupt:
        return
