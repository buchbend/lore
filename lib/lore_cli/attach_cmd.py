"""`lore attach` — read/write the managed `## Lore` section in CLAUDE.md.

Parses and upserts a small key-value block without touching any content
outside the `## Lore` heading. See the concept note
`claude-md-as-scope-anchor` in the private wiki for the contract.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from rich.console import Console

console = Console()

SECTION_HEADING = "## Lore"
MANAGED_COMMENT = (
    "<!-- Managed by /lore:attach. "
    "Safe to edit — changes are preserved on re-run. -->"
)
LORE_KEYS: tuple[str, ...] = ("wiki", "scope", "backend", "issues", "prs")
BULLET_RE = re.compile(r"^- ([A-Za-z][\w-]*): ?(.*)$")
HEADING_RE = re.compile(r"^## (.+?)\s*$")


def _split_lines(text: str) -> tuple[list[str], bool]:
    """Split text into lines, remembering whether it ended with a newline."""
    if not text:
        return [], False
    trailing = text.endswith("\n")
    body = text[:-1] if trailing else text
    return body.split("\n"), trailing


def _join_lines(lines: list[str], trailing: bool) -> str:
    text = "\n".join(lines)
    if trailing and not text.endswith("\n"):
        text += "\n"
    return text


def find_section(lines: list[str]) -> tuple[int, int] | None:
    """Return (start, end) of the `## Lore` section.

    `start` is the line index of the `## Lore` heading.
    `end` is the exclusive line index where the section body ends — either
    the index of the next `## ` heading, or `len(lines)` for EOF.
    Returns None if the section is absent.
    """
    start = None
    for i, line in enumerate(lines):
        if line.strip() == SECTION_HEADING:
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = HEADING_RE.match(lines[j])
        if m and m.group(1).strip() != "Lore":
            end = j
            break
    return start, end


def parse_section_body(body_lines: list[str]) -> dict[str, str]:
    """Extract `- key: value` bullets from a section body as a dict."""
    out: dict[str, str] = {}
    for line in body_lines:
        m = BULLET_RE.match(line)
        if m:
            key, value = m.group(1), m.group(2)
            out[key] = value
    return out


def read_attach(path: Path) -> dict[str, str]:
    """Return the parsed Lore block as a dict, or {} if absent."""
    if not path.exists():
        return {}
    lines, _ = _split_lines(path.read_text())
    bounds = find_section(lines)
    if bounds is None:
        return {}
    start, end = bounds
    return parse_section_body(lines[start + 1 : end])


def _render_fresh_section() -> list[str]:
    return [
        SECTION_HEADING,
        "",
        MANAGED_COMMENT,
        "",
    ]


def _upsert_in_body(body_lines: list[str], updates: dict[str, str]) -> list[str]:
    """Return new body lines with Lore-owned keys upserted.

    Rules:
      - If a Lore-owned key already exists as a bullet, replace the value.
      - If absent, append at the end of the contiguous bullet group (or at
        section end if no bullets exist yet).
      - Non-bullet lines (comments, blank lines, prose) are preserved as-is.
      - User-added bullets (keys not in LORE_KEYS) are preserved.
    """
    new_body = list(body_lines)
    seen: set[str] = set()

    # Pass 1: replace in place.
    for i, line in enumerate(new_body):
        m = BULLET_RE.match(line)
        if not m:
            continue
        key = m.group(1)
        if key in updates and key in LORE_KEYS:
            new_body[i] = f"- {key}: {updates[key]}"
            seen.add(key)

    # Pass 2: append missing Lore keys at the tail of the last bullet run.
    missing = [k for k in LORE_KEYS if k in updates and k not in seen]
    if not missing:
        return new_body

    # Find insertion point: after the last existing bullet, else at end
    # skipping trailing blank lines.
    last_bullet = -1
    for i, line in enumerate(new_body):
        if BULLET_RE.match(line):
            last_bullet = i

    new_bullets = [f"- {k}: {updates[k]}" for k in missing]
    if last_bullet >= 0:
        insert_at = last_bullet + 1
        return new_body[:insert_at] + new_bullets + new_body[insert_at:]

    # No bullets yet — insert before trailing blank lines.
    insert_at = len(new_body)
    while insert_at > 0 and new_body[insert_at - 1].strip() == "":
        insert_at -= 1
    prefix = new_body[:insert_at]
    suffix = new_body[insert_at:]
    # Ensure a blank line separates the comment from the bullets.
    if prefix and prefix[-1].strip() != "":
        prefix = prefix + [""]
    return prefix + new_bullets + suffix


def write_attach(path: Path, updates: dict[str, str]) -> str:
    """Upsert the Lore section. Creates CLAUDE.md if absent. Returns text."""
    # Keep only Lore-owned keys in the update set — policy boundary.
    updates = {k: v for k, v in updates.items() if k in LORE_KEYS and v is not None}

    if not path.exists():
        new_body = _upsert_in_body([], updates)
        lines = _render_fresh_section() + new_body
        text = _join_lines(lines, trailing=True)
        path.write_text(text)
        return text

    lines, trailing = _split_lines(path.read_text())
    bounds = find_section(lines)

    if bounds is None:
        # Append section at end.
        prefix = list(lines)
        if prefix and prefix[-1].strip() != "":
            prefix.append("")
        fresh = _render_fresh_section()
        body = _upsert_in_body([], updates)
        new_lines = prefix + fresh + body
        text = _join_lines(new_lines, trailing=True)
        path.write_text(text)
        return text

    start, end = bounds
    body = lines[start + 1 : end]
    new_body = _upsert_in_body(body, updates)
    new_lines = lines[: start + 1] + new_body + lines[end:]
    text = _join_lines(new_lines, trailing or True)
    path.write_text(text)
    return text


def remove_section(path: Path) -> bool:
    """Remove the Lore section. Returns True if something changed."""
    if not path.exists():
        return False
    lines, trailing = _split_lines(path.read_text())
    bounds = find_section(lines)
    if bounds is None:
        return False
    start, end = bounds

    # Drop the section. Also collapse one blank line before it so we
    # don't leave a double gap where the section used to be.
    cut_start = start
    if cut_start > 0 and lines[cut_start - 1].strip() == "":
        cut_start -= 1
    new_lines = lines[:cut_start] + lines[end:]

    # Trim any trailing whitespace-only tail.
    while new_lines and new_lines[-1].strip() == "":
        new_lines.pop()

    text = _join_lines(new_lines, trailing if new_lines else False)
    path.write_text(text)
    return True


def _resolve_claude_md(path_arg: str) -> Path:
    """Resolve the CLAUDE.md file for a given path argument.

    If `path_arg` points at a directory, append CLAUDE.md. If it points
    directly at a file, use it as-is.
    """
    p = Path(path_arg).expanduser().resolve()
    if p.is_dir() or not p.suffix:
        return p / "CLAUDE.md"
    return p


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lore-attach", description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    p_read = sub.add_parser("read", help="Print parsed Lore block as JSON")
    p_read.add_argument("--path", default=".", help="Folder or CLAUDE.md path")

    p_write = sub.add_parser("write", help="Upsert the Lore block")
    p_write.add_argument("--path", default=".", help="Folder or CLAUDE.md path")
    p_write.add_argument("--wiki", required=True)
    p_write.add_argument("--scope", required=True)
    p_write.add_argument("--backend", default=None, help="github|none (default: inferred)")
    p_write.add_argument("--issues", default=None)
    p_write.add_argument("--prs", default=None)

    args = parser.parse_args(argv)
    target = _resolve_claude_md(args.path)

    if args.action == "read":
        block = read_attach(target)
        envelope = {
            "schema": "lore.attach.read/1",
            "data": {"path": str(target), "block": block},
        }
        print(json.dumps(envelope, indent=2))
        return 0

    if args.action == "write":
        updates: dict[str, str] = {"wiki": args.wiki, "scope": args.scope}
        if args.backend is not None:
            updates["backend"] = args.backend
        if args.issues is not None:
            updates["issues"] = args.issues
        if args.prs is not None:
            updates["prs"] = args.prs
        write_attach(target, updates)
        result = read_attach(target)
        console.print(f"[green]Attached {target}[/green]", file=sys.stderr)
        envelope = {
            "schema": "lore.attach.write/1",
            "data": {"path": str(target), "block": result},
        }
        print(json.dumps(envelope, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
