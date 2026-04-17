"""`lore session` — scaffold/write/commit session notes from the shell.

Two subcommands:

  lore session new    — write a new session note from a scaffold + body
  lore session commit — git-add + commit a session note in its wiki repo

These are the side-effecting (write) half of the session pipeline. The
read-only counterpart is the MCP tool `lore_session_scaffold`. The
`lore-session-writer` subagent calls scaffold via MCP first (silent,
fast), composes the prose body, then shells out here to make the writes
visible/auditable to the user.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lore_core.session import commit_note, scaffold, write_note


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def _emit_json(envelope: dict) -> None:
    print(json.dumps(envelope, indent=2))


# ---------------------------------------------------------------------------
# `lore session new`
# ---------------------------------------------------------------------------


def _cmd_new(args: argparse.Namespace) -> int:
    """Scaffold + write a new session note. Body comes from --body file or stdin."""
    body_text: str
    if args.body == "-":
        body_text = sys.stdin.read()
    elif args.body:
        body_text = Path(args.body).read_text()
    else:
        body_text = ""  # subagent may have only the scaffold's body_template

    result = scaffold(
        cwd=args.cwd,
        slug=args.slug,
        description=args.description,
        title=args.title,
        target_wiki=args.target_wiki,
        extra_repos=_split_csv(args.repos),
        tags=_split_csv(args.tags),
        implements=_split_csv(args.implements),
        loose_ends=args.loose_end or None,
        project=args.project,
    )

    if "error" in result:
        if args.json:
            _emit_json({"schema": "lore.session.new/1", "data": result})
        else:
            print(f"lore: {result['error']}", file=sys.stderr)
        return 1

    note_path = Path(result["note_path"])
    body_to_write = body_text or result["body_template"]
    if args.dry_run:
        if args.json:
            _emit_json({"schema": "lore.session.new/1", "data": {**result, "dry_run": True}})
        else:
            print(f"would write: {note_path}", file=sys.stderr)
        return 0

    written = write_note(
        note_path=note_path,
        frontmatter_yaml=result["frontmatter_yaml"],
        body=body_to_write,
    )

    if args.json:
        _emit_json(
            {
                "schema": "lore.session.new/1",
                "data": {
                    **{k: v for k, v in result.items() if k != "body_template"},
                    "written": True,
                },
            }
        )
    else:
        print(str(written))
    return 0


# ---------------------------------------------------------------------------
# `lore session commit`
# ---------------------------------------------------------------------------


def _cmd_commit(args: argparse.Namespace) -> int:
    """git add + commit one session note in its wiki repo."""
    note_path = Path(args.path).resolve()
    if not note_path.exists():
        print(f"lore: not found: {note_path}", file=sys.stderr)
        return 1

    # Wiki root is the nearest ancestor matching $LORE_ROOT/wiki/<name>/
    from lore_core.config import get_wiki_root

    wiki_root = get_wiki_root().resolve()
    wiki_path: Path | None = None
    for parent in note_path.parents:
        if parent.parent == wiki_root:
            wiki_path = parent
            break
    if wiki_path is None:
        print(
            f"lore: {note_path} is not inside any wiki under {wiki_root}",
            file=sys.stderr,
        )
        return 1

    ok, sha_or_err = commit_note(
        wiki_path=wiki_path,
        note_path=note_path,
        message=args.message,
    )
    if args.json:
        _emit_json(
            {
                "schema": "lore.session.commit/1",
                "data": {
                    "ok": ok,
                    "sha": sha_or_err if ok else "",
                    "error": sha_or_err if not ok else None,
                    "wiki": wiki_path.name,
                    "path": str(note_path.relative_to(wiki_path)),
                },
            }
        )
    else:
        if ok:
            print(sha_or_err or "(nothing to commit — already committed)")
        else:
            print(f"lore: commit failed: {sha_or_err}", file=sys.stderr)
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lore-session", description=__doc__)
    subs = parser.add_subparsers(dest="cmd", required=True)

    p_new = subs.add_parser("new", help="Scaffold + write a new session note")
    p_new.add_argument("--cwd", required=True, help="Working directory the session ran in")
    p_new.add_argument("--slug", required=True, help="Short kebab-case topic")
    p_new.add_argument("--description", required=True, help="One-sentence summary")
    p_new.add_argument("--title", default=None, help="Note H1 title (default: slug)")
    p_new.add_argument(
        "--target-wiki",
        default=None,
        help="Wiki name (default: from `## Lore` block or only-wiki)",
    )
    p_new.add_argument("--repos", default=None, help="Comma-separated extra repos")
    p_new.add_argument("--tags", default=None, help="Comma-separated tags")
    p_new.add_argument(
        "--implements",
        default=None,
        help="Comma-separated proposal slugs that landed",
    )
    p_new.add_argument(
        "--loose-end",
        action="append",
        help="Repeatable — long-form loose-end strings",
    )
    p_new.add_argument("--project", default=None)
    p_new.add_argument(
        "--body",
        default=None,
        help="Path to body markdown, or `-` for stdin (default: scaffold's "
        "stub template)",
    )
    p_new.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the path + frontmatter; do not write",
    )
    p_new.add_argument("--json", action="store_true", help="Emit JSON envelope")
    p_new.set_defaults(func=_cmd_new)

    p_commit = subs.add_parser("commit", help="git add + commit a session note")
    p_commit.add_argument("path", help="Path to the session note inside a wiki")
    p_commit.add_argument(
        "--message",
        "-m",
        default=None,
        help="Override commit message (default: `lore: session <slug>`)",
    )
    p_commit.add_argument("--json", action="store_true", help="Emit JSON envelope")
    p_commit.set_defaults(func=_cmd_commit)

    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
