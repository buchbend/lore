"""`lore drain` — maintenance for the per-session and `_system` drain stores.

Today the only subcommand is ``prune``, which drops orphan rows from
``_system.jsonl`` whose referenced note no longer exists. The drain is
append-only by design; ``prune`` is the explicit escape hatch for
cleanup. The producer-side guard in ``DrainStore.emit()`` (Change C)
prevents new pollution; ``prune`` excises legacy rows that predate it.
The retention janitor (#190) folds this into its opportunistic sweep too
— :func:`prune_orphans` is the shared core both callers use.

Scope is intentionally narrow:

* Only ``_system.jsonl`` — per-session drains die with their session,
  so orphans there self-clean.
* Only events in ``{note-filed, note-appended, surface-proposed}`` with
  a ``data.path`` field — ``transcript-synced`` rows have no path and
  are kept regardless.
* ``Path(data.path).exists()`` is the single eviction predicate. Rows
  without ``data.path`` and rows whose path still exists are kept.

Atomicity matches ``DrainStore.write_cursor``: write the survivors to
``_system.jsonl.tmp``, then ``os.replace`` over the original.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import typer
from lore_core.drain import SYSTEM_SESSION
from lore_core.spine import SpineWriter
from rich.console import Console
from rich.markup import escape

from lore_cli._argv_compat import argv_main

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    add_completion=False,
    help="Maintenance for the drain event log.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.callback()
def _root() -> None:
    """Force typer to treat this app as a multi-command group rather than
    collapsing onto its sole subcommand. Without this, ``lore drain prune``
    parses ``prune`` as a positional arg to the only command instead of
    routing to it by name."""
    return None


_PRUNABLE_EVENTS = frozenset({"note-filed", "note-appended", "surface-proposed"})


def _lore_root_or_die() -> Path:
    from lore_cli._cli_helpers import lore_root_or_die
    return lore_root_or_die(err_console)


def _is_orphan_row(obj: dict) -> bool:
    """True if ``obj`` is a note-style row whose referenced path is gone.

    Conservative: rows missing ``event``/``data``, rows with no
    ``data.path``, and rows whose path still exists are all kept.
    """
    event = obj.get("event")
    if event not in _PRUNABLE_EVENTS:
        return False
    data = obj.get("data")
    if not isinstance(data, dict):
        return False
    path = data.get("path")
    if not isinstance(path, str) or not path:
        return False
    return not Path(path).exists()


@dataclass
class PruneResult:
    """Outcome of one :func:`prune_orphans` pass."""

    file_existed: bool = False
    dropped: list[dict] = field(default_factory=list)
    applied: bool = False  # True iff the file was actually rewritten
    failed: bool = False
    error: str | None = None

    @property
    def dropped_count(self) -> int:
        return len(self.dropped)


def prune_orphans(lore_root: Path, *, dry_run: bool = False) -> PruneResult:
    """Drop `_system.jsonl` rows whose referenced note is gone.

    Shared core for ``lore drain prune`` and the retention janitor's
    opportunistic sweep (#190) — folding orphan pruning into the janitor's
    policy means it no longer requires a manual invocation to happen.
    A non-dry-run pass that actually drops rows emits ONE aggregate
    ``source="janitor"`` spine event (not one per row — the drain itself
    already narrated each note's own event); a write failure emits a warn
    event instead of the caller silently losing it.
    """
    target = lore_root / ".lore" / "drain" / f"{SYSTEM_SESSION}.jsonl"
    if not target.exists():
        return PruneResult(file_existed=False)

    survivors: list[str] = []
    dropped: list[dict] = []
    with target.open("r", encoding="utf-8", errors="replace") as fp:
        for raw in fp:
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # Malformed lines: keep — prune is not a validator.
                survivors.append(line)
                continue
            if isinstance(obj, dict) and _is_orphan_row(obj):
                dropped.append(obj)
                continue
            survivors.append(line)

    result = PruneResult(file_existed=True, dropped=dropped)
    if not dropped or dry_run:
        return result

    # Atomic rewrite: write the survivors, fsync, replace.
    tmp = target.with_suffix(".jsonl.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as out:
            for line in survivors:
                out.write(line + "\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, target)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        result.failed = True
        result.error = str(exc)
        SpineWriter(lore_root).emit(
            source="janitor",
            event="retention-delete-failed",
            level="warn",
            data={"family": "drain-orphans", "error": str(exc)},
        )
        return result

    result.applied = True
    SpineWriter(lore_root).emit(
        source="janitor",
        event="retention-delete",
        data={"family": "drain-orphans", "dropped": len(dropped)},
    )
    return result


@app.command("prune", help="Drop orphan rows from `_system.jsonl`.")
def cmd_prune(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would be dropped without rewriting.",
    ),
) -> None:
    """Walk `_system.jsonl`; drop note-style rows whose `data.path` is gone."""
    lore_root = _lore_root_or_die()
    result = prune_orphans(lore_root, dry_run=dry_run)

    if not result.file_existed:
        console.print("[dim]nothing to prune (no _system.jsonl)[/dim]")
        return
    if not result.dropped:
        console.print("[dim]nothing to prune[/dim]")
        return

    if dry_run:
        console.print(
            f"[yellow]would prune {result.dropped_count} orphan row"
            f"{'s' if result.dropped_count != 1 else ''}[/yellow]"
        )
        for obj in result.dropped:
            wikilink = (obj.get("data") or {}).get("wikilink", "?")
            path = (obj.get("data") or {}).get("path", "?")
            console.print(
                f"  · {escape(str(wikilink))} [dim](path: {escape(str(path))})[/dim]"
            )
        return

    if result.failed:
        err_console.print(f"[red]prune failed: {result.error}[/red]")
        raise typer.Exit(1)

    noun = "row" if result.dropped_count == 1 else "rows"
    console.print(f"[green]pruned {result.dropped_count} orphan {noun}[/green]")
    for obj in result.dropped:
        wikilink = (obj.get("data") or {}).get("wikilink", "?")
        console.print(f"  · {escape(str(wikilink))}")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
