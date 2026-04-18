"""`lore briefing` — gather, publish, and ledger-mark briefings.

Three subcommands:
  lore briefing gather --wiki <name>   read-only: returns new sessions
                                        + sink config + ledger state
                                        as JSON envelope
  lore briefing publish --sink <name>  publish stdin/--file via the
                                        named sink adapter
  lore briefing mark --wiki <name>     update the ledger and
    --session <path> [...]              optionally include them in
                                        the next briefing's exclude set

The skill calls gather via MCP (silent), composes prose (LLM), then
shells out to publish + mark (visible side effects).
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
from pathlib import Path

import typer

from lore_cli._compat import argv_main
from lore_core.briefing import gather, mark_incorporated

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)

_KNOWN_SINKS = {"matrix", "markdown"}


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
        ..., "--sink", help=f"Sink name ({', '.join(sorted(_KNOWN_SINKS))})."
    ),
    file: str = typer.Option(
        None, "--file", help="Briefing markdown (default: stdin)."
    ),
    out: str = typer.Option(
        None,
        "--out",
        help="Sink-specific output target (markdown sink: file path).",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON envelope."),
) -> None:
    """Publish a briefing via the named sink adapter."""
    if sink not in _KNOWN_SINKS:
        print(
            f"lore: unknown sink '{sink}'. Known: {', '.join(sorted(_KNOWN_SINKS))}",
            file=sys.stderr,
        )
        raise typer.Exit(code=2)
    text: str
    if file:
        text = Path(file).read_text()
    else:
        text = sys.stdin.read()
    if not text.strip():
        print("lore: nothing to publish (empty input)", file=sys.stderr)
        raise typer.Exit(code=1)

    sink_argv: list[str] = ["send"]
    if file:
        sink_argv += ["--file", file]
    if sink == "markdown":
        if not out:
            print("lore: markdown sink requires --out <path>", file=sys.stderr)
            raise typer.Exit(code=2)
        sink_argv += ["--out", out]
    module = importlib.import_module(f"lore_sinks.{sink}")
    # Sinks read stdin themselves when --file is omitted; if we already
    # consumed stdin above, write a temp file so the sink can re-read it.
    if not file:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".md", delete=False, prefix="lore-briefing-"
        ) as tmp:
            tmp.write(text)
            tmp_path = tmp.name
        sink_argv = ["send", "--file", tmp_path]
        if sink == "markdown" and out:
            sink_argv += ["--out", out]

    rc = int(module.main(sink_argv) or 0)
    if json_out:
        _emit_json(
            {
                "schema": "lore.briefing.publish/1",
                "data": {"sink": sink, "rc": rc},
            }
        )
    if rc != 0:
        raise typer.Exit(code=rc)


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
