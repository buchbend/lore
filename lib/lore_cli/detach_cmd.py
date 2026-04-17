"""`lore detach` — remove the managed `## Lore` section from CLAUDE.md.

Leaves all content outside the section untouched. No-op if absent.
"""

from __future__ import annotations

import argparse
import json
import sys

from rich.console import Console

from lore_cli.attach_cmd import _resolve_claude_md, remove_section

console = Console()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lore-detach", description=__doc__)
    parser.add_argument("--path", default=".", help="Folder or CLAUDE.md path")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON envelope on stdout (lore.detach/1)",
    )
    args = parser.parse_args(argv)

    target = _resolve_claude_md(args.path)
    changed = remove_section(target)
    if args.json:
        print(
            json.dumps(
                {
                    "schema": "lore.detach/1",
                    "data": {"path": str(target), "removed": changed},
                },
                indent=2,
            )
        )
    elif changed:
        console.print(f"[green]Detached — removed ## Lore from {target}[/green]")
    else:
        console.print(f"[yellow]No ## Lore section found in {target}[/yellow]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
