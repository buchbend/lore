"""`lore inbox` — classify and archive inbox files.

Two subcommands:
  lore inbox classify     read-only: walk every inbox in the vault,
                          return file list with detected type + routing
                          hint as a JSON envelope
  lore inbox archive PATH move a processed inbox file to .processed/
                          with a date prefix

The skill calls classify via MCP, reads each file (LLM judgment),
composes vault notes (LLM body + Bash write), then runs archive to
move the source out of the inbox.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lore_core.inbox import archive, classify


def _emit_json(envelope: dict) -> None:
    print(json.dumps(envelope, indent=2, default=str))


def _cmd_classify(args: argparse.Namespace) -> int:
    result = classify()
    _emit_json({"schema": "lore.inbox.classify/1", "data": result})
    return 1 if "error" in result else 0


def _cmd_archive(args: argparse.Namespace) -> int:
    result = archive(source=Path(args.path))
    if args.json:
        _emit_json({"schema": "lore.inbox.archive/1", "data": result})
    elif "error" in result:
        print(f"lore: {result['error']}", file=sys.stderr)
    else:
        print(f"archived: {result['archived_to']}")
    return 1 if "error" in result else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lore-inbox", description=__doc__)
    subs = parser.add_subparsers(dest="cmd", required=True)

    p_class = subs.add_parser("classify", help="Walk inboxes, classify what's there")
    p_class.set_defaults(func=_cmd_classify)

    p_arch = subs.add_parser("archive", help="Move a processed file to .processed/")
    p_arch.add_argument("path", help="Path to the inbox file to archive")
    p_arch.add_argument("--json", action="store_true", help="Emit JSON envelope")
    p_arch.set_defaults(func=_cmd_archive)

    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
