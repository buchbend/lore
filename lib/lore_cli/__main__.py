"""`lore` command — dispatches to subcommand modules.

Phase A ships: `lore lint`, `lore migrate`.
Phase B adds: `lore search`, `lore mcp`.
Phase D adds: `lore init`, `lore new-wiki`.
"""

from __future__ import annotations

import sys

SUBCOMMANDS = {
    "lint": ("lore_core.lint", "main"),
    "migrate": ("lore_core.migrate", "main"),
    "hook": ("lore_cli.hooks", "main"),
    "search": ("lore_search.cli", "main"),
    "mcp": ("lore_mcp.server", "main"),
    "curator": ("lore_curator.core", "main"),
    "init": ("lore_cli.init_cmd", "main"),
    "new-wiki": ("lore_cli.new_wiki_cmd", "main"),
    "attach": ("lore_cli.attach_cmd", "main"),
    "detach": ("lore_cli.detach_cmd", "main"),
    "resume": ("lore_cli.resume_cmd", "main"),
}


def _usage() -> None:
    print("lore — knowledge-graph tooling for AI-coding teams", file=sys.stderr)
    print(file=sys.stderr)
    print("Usage: lore <subcommand> [args...]", file=sys.stderr)
    print(file=sys.stderr)
    print("Available subcommands:", file=sys.stderr)
    for name in sorted(SUBCOMMANDS):
        print(f"  {name}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        _usage()
        return 0 if argv else 2
    cmd = argv[0]
    rest = argv[1:]
    if cmd not in SUBCOMMANDS:
        print(f"lore: unknown subcommand '{cmd}'", file=sys.stderr)
        _usage()
        return 2
    module_name, func_name = SUBCOMMANDS[cmd]
    import importlib

    module = importlib.import_module(module_name)
    func = getattr(module, func_name)
    return int(func(rest) or 0)


if __name__ == "__main__":
    sys.exit(main())
