"""`lore drain` — maintenance for the per-session and `_system` drain stores.

Today the only subcommand is ``prune``, which drops orphan rows from
``_system.jsonl`` whose referenced note no longer exists. The drain is
append-only by design; ``prune`` is the explicit escape hatch for
cleanup. The producer-side guard in ``DrainStore.emit()`` (Change C)
prevents new pollution; ``prune`` excises legacy rows that predate it.

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
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from lore_cli._argv_compat import argv_main
from lore_core.drain import SYSTEM_SESSION

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


@app.command("prune", help="Drop orphan rows from `_system.jsonl`.")
def cmd_prune(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would be dropped without rewriting.",
    ),
) -> None:
    """Walk `_system.jsonl`; drop note-style rows whose `data.path` is gone."""
    lore_root = _lore_root_or_die()
    target = lore_root / ".lore" / "drain" / f"{SYSTEM_SESSION}.jsonl"
    if not target.exists():
        console.print("[dim]nothing to prune (no _system.jsonl)[/dim]")
        return

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

    if not dropped:
        console.print("[dim]nothing to prune[/dim]")
        return

    if dry_run:
        console.print(
            f"[yellow]would prune {len(dropped)} orphan row"
            f"{'s' if len(dropped) != 1 else ''}[/yellow]"
        )
        for obj in dropped:
            wikilink = (obj.get("data") or {}).get("wikilink", "?")
            path = (obj.get("data") or {}).get("path", "?")
            console.print(
                f"  · {escape(str(wikilink))} [dim](path: {escape(str(path))})[/dim]"
            )
        return

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
        err_console.print(f"[red]prune failed: {exc}[/red]")
        try:
            tmp.unlink()
        except OSError:
            pass
        raise typer.Exit(1)

    noun = "row" if len(dropped) == 1 else "rows"
    console.print(f"[green]pruned {len(dropped)} orphan {noun}[/green]")
    for obj in dropped:
        wikilink = (obj.get("data") or {}).get("wikilink", "?")
        console.print(f"  · {escape(str(wikilink))}")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
