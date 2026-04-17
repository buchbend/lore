"""`lore resume` — load working context from the vault.

Modes (one per invocation):
  - no args              recent sessions across all wikis
  - --wiki <name>        recent sessions in one wiki
  - --keyword <term>     ranked keyword search via FTS5
  - --scope <prefix>     aggregate gh issues + PRs + session notes for a
                         scope subtree (e.g. `ccat:data-center`)

Used by:
  - the `/lore:resume` skill (display-layer wrapper over MCP `lore_resume`)
  - any user wanting a shell-side context dump or pre-warmed launcher
"""

from __future__ import annotations

import argparse
import json
import sys

from lore_cli.launcher import launch, list_hosts
from lore_core.resume import (
    DEFAULT_DAYS,
    DEFAULT_ISSUES_FILTER,
    DEFAULT_KEYWORD_K,
    DEFAULT_PRS_FILTER,
    format_markdown,
    gather,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lore-resume", description=__doc__)
    parser.add_argument(
        "--scope",
        default=None,
        help="Scope prefix to aggregate (e.g. ccat:data-center)",
    )
    parser.add_argument(
        "--keyword",
        default=None,
        help="Keyword for ranked FTS5 search across the vault",
    )
    parser.add_argument(
        "--wiki",
        default=None,
        help="Restrict to one wiki (default: all wikis for recent mode, "
        "auto-detect for scope mode)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Recency window for sessions (default {DEFAULT_DAYS}d, "
        "recent mode only)",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=DEFAULT_KEYWORD_K,
        help=f"Top-k results for keyword search (default {DEFAULT_KEYWORD_K})",
    )
    parser.add_argument(
        "--issues",
        default=DEFAULT_ISSUES_FILTER,
        help=f"gh issue list filter flags (scope mode; default: "
        f"{DEFAULT_ISSUES_FILTER!r})",
    )
    parser.add_argument(
        "--prs",
        default=DEFAULT_PRS_FILTER,
        help=f"gh pr list filter flags (scope mode; default: "
        f"{DEFAULT_PRS_FILTER!r})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON envelope on stdout instead of markdown",
    )
    parser.add_argument(
        "--launch",
        default=None,
        metavar="HOST",
        help=(
            f"Gather context, then exec the named agent host "
            f"pre-warmed (known: {', '.join(list_hosts()) or 'none'})"
        ),
    )
    parser.add_argument(
        "--launch-dry-run",
        action="store_true",
        help="With --launch: print the would-be invocation, do not exec",
    )
    parser.add_argument(
        "--launch-message",
        default=None,
        help="Initial user message to pass to the launched host (optional)",
    )
    args = parser.parse_args(argv)

    result = gather(
        scope=args.scope,
        wiki=args.wiki,
        keyword=args.keyword,
        days=args.days,
        k=args.k,
        issues_filter=args.issues,
        prs_filter=args.prs,
    )

    if args.launch:
        if "error" in result and not result.get("mode"):
            print(f"lore: {result['error']}", file=sys.stderr)
            return 1
        context_text = format_markdown(result)
        return launch(
            args.launch,
            context_text=context_text,
            user_message=args.launch_message,
            dry_run=args.launch_dry_run,
        )

    if args.json:
        print(json.dumps({"schema": "lore.resume/1", "data": result}, indent=2))
    else:
        print(format_markdown(result))

    if "error" in result and not result.get("mode"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
