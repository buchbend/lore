"""`lore briefing` — gather, publish, and ledger-mark briefings.

Three subcommands:
  lore briefing gather --wiki <name>   read-only: returns new sessions
                                        + sink config + ledger state
                                        as JSON envelope
  lore briefing publish --sink <uri>   publish stdin/--file via the
                                        named sink (markdown:<path>,
                                        matrix, …)
  lore briefing mark --wiki <name>     update the ledger and
    --session <path> [...]              optionally include them in
                                        the next briefing's exclude set

The skill calls gather via MCP (silent), composes prose (LLM), then
shells out to publish + mark (visible side effects).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from lore_cli._argv_compat import argv_main
from lore_core.briefing import (
    UnknownSinkError,
    dispatch,
    gather,
    mark_incorporated,
    registered_sinks,
)

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _emit_json(envelope: dict) -> None:
    print(json.dumps(envelope, indent=2, default=str))


@app.command("gather")
def cmd_gather(
    wiki: str = typer.Option(..., "--wiki"),
    since: str = typer.Option(None, "--since", help="ISO date floor (YYYY-MM-DD)."),
    no_sections: bool = typer.Option(
        False,
        "--no-sections",
        help="Skip extracting body H2 sections (smaller payload).",
    ),
) -> None:
    """Read new sessions since the last briefing."""
    result = gather(wiki=wiki, since=since, include_body_sections=not no_sections)
    _emit_json({"schema": "lore.briefing.gather/1", "data": result})
    if "error" in result:
        raise typer.Exit(code=1)


@app.command("publish")
def cmd_publish(
    sink: str = typer.Option(
        ...,
        "--sink",
        help=(
            "Sink URI (scheme[:target]). Registered schemes: "
            f"{', '.join(registered_sinks())}. "
            "Examples: 'markdown:/tmp/briefing-YYYY-MM-DD.md', 'matrix'."
        ),
    ),
    file: str = typer.Option(
        None, "--file", help="Briefing markdown (default: stdin)."
    ),
    out: str = typer.Option(
        None,
        "--out",
        help=(
            "Compatibility shim — appended to --sink as ':<out>'. "
            "Prefer the URI form."
        ),
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON envelope."),
) -> None:
    """Publish a briefing through the registered sink dispatcher."""
    text = Path(file).read_text() if file else sys.stdin.read()
    if not text.strip():
        print("lore: nothing to publish (empty input)", file=sys.stderr)
        raise typer.Exit(code=1)

    uri = sink
    if out and ":" not in sink:
        uri = f"{sink}:{out}"

    try:
        dispatch(uri, text)
    except UnknownSinkError as exc:
        print(
            f"lore: unknown sink '{exc}'. Known: {', '.join(registered_sinks())}",
            file=sys.stderr,
        )
        raise typer.Exit(code=2) from None
    except Exception as exc:  # noqa: BLE001 — surfaced to user
        print(f"lore: sink failed: {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from None

    if json_out:
        _emit_json({"schema": "lore.briefing.publish/1", "data": {"sink": uri}})


@app.command("mark")
def cmd_mark(
    wiki: str = typer.Option(..., "--wiki"),
    session: list[str] = typer.Option(
        None,
        "--session",
        help="Session path or filename (repeatable).",
    ),
) -> None:
    """Append session(s) to the briefing ledger."""
    result = mark_incorporated(wiki=wiki, session_paths=session or [])
    _emit_json({"schema": "lore.briefing.mark/1", "data": result})
    if "error" in result:
        raise typer.Exit(code=1)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
