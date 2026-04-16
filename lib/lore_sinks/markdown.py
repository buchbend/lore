"""Markdown-file briefing sink.

Writes the briefing to a markdown file at a configured path. Simplest
sink — works for wikis that want briefings stored as notes (Obsidian,
git history, GitHub rendering).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from lore_core.io import atomic_write_text


def send(text: str, path: Path) -> None:
    """Write briefing to path (atomic)."""
    # Expand YYYY-MM-DD placeholder
    if "YYYY-MM-DD" in str(path):
        path = Path(str(path).replace("YYYY-MM-DD", date.today().isoformat()))
    atomic_write_text(path, text)
    print(f"Published to {path}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lore-sink-markdown")
    parser.add_argument("command", choices=["send"])
    parser.add_argument("--file", help="Input file (default: stdin)")
    parser.add_argument(
        "--out",
        required=True,
        help="Output path (may contain YYYY-MM-DD placeholder)",
    )
    args = parser.parse_args(argv)

    if args.file:
        text = Path(args.file).read_text()
    else:
        text = sys.stdin.read()

    if not text.strip():
        print("Nothing to send (empty input).", file=sys.stderr)
        return 1

    send(text, Path(os.path.expanduser(args.out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
