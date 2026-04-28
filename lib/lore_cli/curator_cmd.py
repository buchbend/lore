"""`lore curator` — manual entry point for the curator triad.

Bare `lore curator` runs the Curator C hygiene passes (stale, supersession,
backfill, implements-propagation). `lore curator run` is the full pipeline:
classify pending transcripts → file session notes → optional surface
extraction (`--abstract`) and weekly defrag (`--defrag`).

Curator A / B / C labels are internal; user-facing copy says "Curator"
or the role name.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
from lore_core.lint import STALENESS_DAYS
from lore_core.run_render import pick_icon_set, render_flat_log, should_use_color
from lore_curator.defrag_curator import (
    _resolve_backend,
    run_curator_c,
    run_open_items_migration,
)
from rich.console import Console

from lore_cli._argv_compat import argv_main

console = Console()

app = typer.Typer(
    add_completion=False,
    help="Curator — flag stale notes, propagate `implements:` status flips, etc.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def curator(
    ctx: typer.Context,
    wiki: str = typer.Option(None, "--wiki", help="Scope to one wiki."),
    apply: bool = typer.Option(
        False, "--apply", help="Actually write changes. Without this, runs dry."
    ),
    stale_threshold: int = typer.Option(
        STALENESS_DAYS,
        "--stale-threshold",
        help=f"Days after which active notes become stale (default {STALENESS_DAYS}).",
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit machine-readable summary."
    ),
    migrate_open_items: bool = typer.Option(
        False,
        "--migrate-open-items",
        help="Interactive v1 → v2 migration for `## Open items` session sections.",
    ),
) -> None:
    """Run curator passes — flag stale, propagate implements, etc."""
    # If a subcommand was invoked (e.g. `lore curator run --defrag`), let it
    # handle the flow; the callback's hygiene-only path is for bare
    # `lore curator` only.
    if ctx.invoked_subcommand is not None:
        return
    if migrate_open_items:
        run_open_items_migration(wiki_filter=wiki, dry_run=not apply)
        return

    reports = run_curator_c(
        wiki_filter=wiki,
        dry_run=not apply,
        stale_threshold=stale_threshold,
    )

    if json_out:
        print(
            json.dumps(
                {
                    "schema": "lore.curator/1",
                    "data": {
                        "dry_run": not apply,
                        "wikis": [
                            {
                                "wiki": r.wiki,
                                "actions": [
                                    {
                                        "kind": a.kind,
                                        "path": str(a.path),
                                        "reason": a.reason,
                                    }
                                    for a in r.actions
                                ],
                                "skipped": [
                                    {"path": str(p), "reason": reason}
                                    for p, reason in r.skipped
                                ],
                            }
                            for r in reports
                        ],
                    },
                },
                indent=2,
            )
        )


def _discover_wikis(lore_root: Path) -> list[str]:
    """Return sorted list of wiki directory names under lore_root/wiki/."""
    wiki_dir = lore_root / "wiki"
    if not wiki_dir.exists():
        return []
    return sorted([d.name for d in wiki_dir.iterdir() if d.is_dir()])


def _make_live_renderer(con: Console):
    """Return an on_record callback that prints the same format as ``lore runs tail``."""
    icons = pick_icon_set()
    use_color = should_use_color()

    def _render(_record_type: str, payload: dict[str, Any]) -> None:
        con.print(render_flat_log([payload], icons=icons, use_color=use_color))

    return _render


_BACKEND_LABELS = {
    "subprocess": "Claude Code subscription (claude -p)",
    "sdk": "Anthropic API (anthropic SDK)",
    "openai": "OpenAI-compatible endpoint",
}


def _print_backend_label(con: Console, llm_client: object) -> None:
    backend_name = getattr(llm_client, "backend_name", "") or ""
    label = _BACKEND_LABELS.get(backend_name, backend_name or "unknown backend")
    con.print(f"[dim]Curator backend: {label}[/dim]")


@app.command("run")
def run_command(
    scope: str = typer.Option(None, "--scope", help="Filter to one scope, e.g. 'mywiki:subproject'."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Classify but don't write notes or advance ledger."),
    abstract: bool = typer.Option(False, "--abstract", help="Also run the surface-extraction pass after filing session notes."),
    defrag: bool = typer.Option(False, "--defrag", help="Run Curator C weekly defragmentation (hygiene + LLM adjacent-merge / auto-supersede / orphan-repair / draft-promotion)."),
    wiki: str = typer.Option(None, "--wiki", help="Limit the surface-extraction / defrag pass to a single wiki."),
    trace_llm: bool = typer.Option(False, "--trace-llm", help="Capture LLM prompts/responses to runs/<id>.trace.jsonl (equivalent to LORE_TRACE_LLM=1)."),
    backend: str = typer.Option(None, "--backend", help="LLM backend: subscription | api | openai | auto. Overrides LORE_LLM_BACKEND and curator.backend config."),
) -> None:
    """Run the curator.

    Default: classify pending transcripts and file session notes.
    --abstract also runs the surface-extraction pass.
    --defrag runs the weekly whole-wiki defragmentation (hygiene passes
    + LLM proposals for adjacent-concept merges, auto-supersession,
    orphan wikilink repair, and draft promotions).
    """
    if defrag:
        # --defrag runs Curator C directly, not Curator A. Bypass the
        # transcript classification path and go straight to the whole-wiki
        # pipeline.
        import os
        from lore_cli._cli_helpers import lore_root_or_die
        err_console = Console(stderr=True)
        lore_root_defrag = lore_root_or_die(err_console)
        effective_backend = _resolve_backend(backend, lore_root_defrag)

        # LLM client resolution (same seam as Curator A).
        from lore_curator.llm_client import LlmClientError, make_llm_client
        api_key = os.environ.get("ANTHROPIC_API_KEY", "") or None
        try:
            llm_client = make_llm_client(
                backend=effective_backend,
                api_key=api_key,
                lore_root=lore_root_defrag,
            )
        except LlmClientError as exc:
            err_console.print(f"[yellow]Warning:[/yellow] {exc}")
            llm_client = None
        if llm_client is None:
            console.print(
                "[yellow]Running --defrag without an LLM client — "
                "LLM passes (adjacent-merge, auto-supersede, orphan-repair) "
                "will be skipped.[/yellow]"
            )
        else:
            _print_backend_label(console, llm_client)

        run_curator_c(
            wiki_filter=wiki,
            dry_run=dry_run,
            defrag=True,
            llm_client=llm_client,
        )
        # Exit success — report already printed by run_curator_c.
        return
    import os
    from datetime import UTC, datetime
    from pathlib import Path

    from lore_cli._cli_helpers import lore_root_or_die
    from lore_curator.session_curator import run_curator_a

    err_console = Console(stderr=True)
    lore_root = lore_root_or_die(err_console)

    # Build scope filter if provided
    scope_obj = None
    if scope:
        from lore_core.types import Scope
        wiki_name = scope.split(":")[0] if ":" in scope else scope
        scope_obj = Scope(
            wiki=wiki_name,
            scope=scope,
            backend="none",
            claude_md_path=Path("."),
        )

    # Resolve LLM backend via factory
    from lore_curator.llm_client import LlmClientError, make_llm_client

    effective_backend = _resolve_backend(backend, lore_root)

    err_console = Console(stderr=True)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "") or None
    backend_error: LlmClientError | None = None
    try:
        llm_client = make_llm_client(
            backend=effective_backend,
            api_key=api_key,
            lore_root=lore_root,
        )
    except LlmClientError as exc:
        err_console.print(f"[yellow]Warning:[/yellow] {exc}")
        llm_client = None
        backend_error = exc

    if llm_client is None:
        if backend_error is None:
            # Auto-detection had nothing to pick — print the skip-AI warning.
            err_console.print(
                "[yellow]Warning:[/yellow] Curator will skip AI classification: "
                "neither 'claude' CLI on PATH nor ANTHROPIC_API_KEY set. Install "
                "Claude Code for subscription inference, or export "
                "ANTHROPIC_API_KEY for API inference."
            )
        # If backend_error was set, the specific warning was already printed.
    else:
        _print_backend_label(console, llm_client)

    # Interactive (TTY) runs wait up to 60s for the lock; detached hook spawns
    # keep timeout=0.0 (fire-and-forget, yield to any active curator).
    is_tty = sys.stdout.isatty()
    lock_timeout = 60.0 if is_tty else 0.0

    # Compute effective trace_llm from flag or environment variable
    effective_trace = trace_llm or os.environ.get("LORE_TRACE_LLM") == "1"

    live_callback = _make_live_renderer(console) if is_tty else None

    result = run_curator_a(
        lore_root=lore_root,
        scope=scope_obj,
        llm_client=llm_client,
        dry_run=dry_run,
        now=datetime.now(UTC),
        lock_timeout=lock_timeout,
        trigger="manual",
        trace_llm=effective_trace,
        on_record=live_callback,
    )

    skipped_summary = ", ".join(
        f"{k}: {v}" for k, v in result.skipped_reasons.items()
    ) or "none"

    console.print()
    console.print(
        f"[bold]Curator A[/bold] — {result.transcripts_considered} transcript(s) considered"
    )
    console.print(f"  noteworthy: {result.noteworthy_count}")
    console.print(
        f"  new notes: {len(result.new_notes)}, merged: {len(result.merged_notes)}"
    )
    console.print(f"  skipped: {{{skipped_summary}}}")
    console.print(f"  took: {result.duration_seconds:.2f}s")

    # Run Curator B if --abstract is specified
    if abstract:
        from lore_curator.daily_curator import run_curator_b

        wikis_to_process = [wiki] if wiki else _discover_wikis(lore_root)

        for wiki_name in wikis_to_process:
            b_result = run_curator_b(
                lore_root=lore_root,
                wiki=wiki_name,
                llm_client=llm_client,
                dry_run=dry_run,
                now=datetime.now(UTC),
                lock_timeout=lock_timeout,
            )

            skipped_b_summary = ", ".join(
                f"{k}: {v}" for k, v in b_result.skipped_reasons.items()
            ) or "none"

            console.print(
                f"[bold]Curator B[/bold] ({wiki_name}) — {b_result.notes_considered} note(s) considered"
            )
            console.print(f"  clusters: {b_result.clusters_formed}")
            console.print(f"  surfaces: {len(b_result.surfaces_emitted)}")
            console.print(f"  skipped: {{{skipped_b_summary}}}")
            console.print(f"  took: {b_result.duration_seconds:.2f}s")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
