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

import argparse
import importlib
import json
import sys
from pathlib import Path

from lore_core.briefing import gather, mark_incorporated


def _emit_json(envelope: dict) -> None:
    print(json.dumps(envelope, indent=2, default=str))


# ---------------------------------------------------------------------------
# `lore briefing gather`
# ---------------------------------------------------------------------------


def _cmd_gather(args: argparse.Namespace) -> int:
    result = gather(
        wiki=args.wiki,
        since=args.since,
        include_body_sections=not args.no_sections,
    )
    _emit_json({"schema": "lore.briefing.gather/1", "data": result})
    return 1 if "error" in result else 0


# ---------------------------------------------------------------------------
# `lore briefing publish`
# ---------------------------------------------------------------------------


_KNOWN_SINKS = {"matrix", "markdown"}


def _cmd_publish(args: argparse.Namespace) -> int:
    if args.sink not in _KNOWN_SINKS:
        print(
            f"lore: unknown sink '{args.sink}'. "
            f"Known: {', '.join(sorted(_KNOWN_SINKS))}",
            file=sys.stderr,
        )
        return 2
    text: str
    if args.file:
        text = Path(args.file).read_text()
    else:
        text = sys.stdin.read()
    if not text.strip():
        print("lore: nothing to publish (empty input)", file=sys.stderr)
        return 1
    # Dispatch into the sink module's main()
    sink_argv: list[str] = ["send"]
    if args.file:
        sink_argv += ["--file", args.file]
    if args.sink == "markdown":
        if not args.out:
            print("lore: markdown sink requires --out <path>", file=sys.stderr)
            return 2
        sink_argv += ["--out", args.out]
    module = importlib.import_module(f"lore_sinks.{args.sink}")
    # Sinks read stdin themselves when --file is omitted; for consistency
    # if we already consumed stdin above, write a temp file.
    if not args.file:
        import tempfile

        with tempfile.NamedTemporaryFile(
            "w", suffix=".md", delete=False, prefix="lore-briefing-"
        ) as tmp:
            tmp.write(text)
            tmp_path = tmp.name
        sink_argv = ["send", "--file", tmp_path]
        if args.sink == "markdown" and args.out:
            sink_argv += ["--out", args.out]
    rc = int(module.main(sink_argv) or 0)
    if args.json:
        _emit_json(
            {
                "schema": "lore.briefing.publish/1",
                "data": {"sink": args.sink, "rc": rc},
            }
        )
    return rc


# ---------------------------------------------------------------------------
# `lore briefing mark`
# ---------------------------------------------------------------------------


def _cmd_mark(args: argparse.Namespace) -> int:
    result = mark_incorporated(wiki=args.wiki, session_paths=args.session or [])
    _emit_json({"schema": "lore.briefing.mark/1", "data": result})
    return 1 if "error" in result else 0


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lore-briefing", description=__doc__)
    subs = parser.add_subparsers(dest="cmd", required=True)

    p_gather = subs.add_parser("gather", help="Read new sessions since last briefing")
    p_gather.add_argument("--wiki", required=True)
    p_gather.add_argument("--since", default=None, help="ISO date floor (YYYY-MM-DD)")
    p_gather.add_argument(
        "--no-sections",
        action="store_true",
        help="Skip extracting body H2 sections (smaller payload)",
    )
    p_gather.set_defaults(func=_cmd_gather)

    p_pub = subs.add_parser("publish", help="Publish briefing via a sink adapter")
    p_pub.add_argument("--sink", required=True, help=f"Sink name ({', '.join(sorted(_KNOWN_SINKS))})")
    p_pub.add_argument("--file", default=None, help="Briefing markdown (default: stdin)")
    p_pub.add_argument("--out", default=None, help="Sink-specific output target (markdown sink: file path)")
    p_pub.add_argument("--json", action="store_true", help="Emit JSON envelope")
    p_pub.set_defaults(func=_cmd_publish)

    p_mark = subs.add_parser("mark", help="Append session(s) to the briefing ledger")
    p_mark.add_argument("--wiki", required=True)
    p_mark.add_argument(
        "--session",
        action="append",
        help="Session path or filename (repeatable)",
    )
    p_mark.set_defaults(func=_cmd_mark)

    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
