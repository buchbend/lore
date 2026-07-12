"""Legacy v1 → v2 session-note migration for the `## Open items` section.

Rewrites old session notes whose body carries a `## Open items` heading
into the v2 shape (`## Issues touched` + `## Loose ends`) and bumps
`schema_version` to 2. Exposed through `lore migrate open-items`.

Pure rewriting logic (`extract_open_items`, `migrate_open_items`) is
independent of any curator run loop; only `run_open_items_migration`
touches the terminal.
"""

from __future__ import annotations

import re

from rich.console import Console

console = Console()

_OPEN_ITEMS_HEADING = "## Open items"
_SECTION_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")


def extract_open_items(text: str) -> list[str]:
    """Return bullet items (without `- ` prefix) under `## Open items`.

    Returns [] if the heading is absent or the section body has no bullets.
    `- None` / `- _None_` placeholders are treated as empty.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == _OPEN_ITEMS_HEADING:
            start = i
            break
    if start is None:
        return []
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = _SECTION_HEADING_RE.match(lines[j])
        if m and m.group(1).strip() != "Open items":
            end = j
            break
    out: list[str] = []
    for line in lines[start + 1 : end]:
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        body = stripped[2:].strip()
        if body.lower() in ("none", "_none_"):
            continue
        out.append(body)
    return out


def _bump_schema_version_to_2(fm_block: str) -> str:
    """Return fm_block with schema_version bumped (or added) to 2."""
    lines = fm_block.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("schema_version:"):
            lines[i] = "schema_version: 2"
            return "\n".join(lines)
    return "schema_version: 2\n" + fm_block


def _split_body_by_open_items(body: str) -> tuple[str, str, str]:
    """Return (before, open_items_block, after).

    `before` ends right before the `## Open items` heading.
    `open_items_block` is the full `## Open items` section including heading.
    `after` is everything from the next `## ` heading onwards.
    If `## Open items` is absent, returns (body, "", "").
    """
    lines = body.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.strip() == _OPEN_ITEMS_HEADING:
            start = i
            break
    if start is None:
        return body, "", ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped.startswith("## ") and stripped != _OPEN_ITEMS_HEADING:
            end = j
            break
    before = "".join(lines[:start])
    section = "".join(lines[start:end])
    after = "".join(lines[end:])
    return before, section, after


def migrate_open_items(
    text: str,
    decisions: list[tuple[str, str | None]],
) -> str:
    """Rewrite a v1 session note to v2.

    - Bumps `schema_version` to 2 in the frontmatter.
    - Replaces `## Open items` with `## Issues touched` + `## Loose ends`.
    - `decisions[i]` is applied to the i-th bullet returned by
      `extract_open_items`. Each decision is `(choice, issue_number)`:
        * `("issue", "#47")`    → `## Issues touched` as `- #47 <text>`
        * `("issue", None)`     → `## Issues touched` as `- <text> (needs issue)`
        * `("loose_end", _)`    → `## Loose ends` as `- <text>`
        * `("resolved", _)`     → dropped
    - Idempotent: re-running produces the same output (no `## Open items`
      left to extract the second time).

    Bullets without a matching decision default to `("loose_end", None)`.
    """
    items = extract_open_items(text)

    # Pad decisions to match items length.
    padded = list(decisions) + [("loose_end", None)] * (len(items) - len(decisions))

    issues_touched: list[str] = []
    loose_ends: list[str] = []
    for item, (choice, issue_ref) in zip(items, padded, strict=False):
        if choice == "issue":
            if issue_ref:
                issues_touched.append(f"- {issue_ref} {item}")
            else:
                issues_touched.append(f"- {item} (needs issue)")
        elif choice == "loose_end":
            loose_ends.append(f"- {item}")
        elif choice == "resolved":
            continue
        else:
            loose_ends.append(f"- {item}")

    issues_block_lines = ["## Issues touched", ""]
    issues_block_lines.extend(issues_touched or ["- _None_"])
    issues_block_lines.append("")
    loose_block_lines = ["## Loose ends", ""]
    loose_block_lines.extend(loose_ends or ["- _None_"])
    loose_block_lines.append("")
    replacement = "\n".join(issues_block_lines + loose_block_lines)

    # Split frontmatter.
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    fm_block = text[4:end]
    body = text[end + 4 :].lstrip("\n")

    fm_block = _bump_schema_version_to_2(fm_block)

    before, old_section, after = _split_body_by_open_items(body)
    if old_section:
        new_body = before + replacement
        if after:
            if not new_body.endswith("\n"):
                new_body += "\n"
            new_body += after
    else:
        new_body = body

    return f"---\n{fm_block}\n---\n\n{new_body.lstrip()}"


def run_open_items_migration(
    wiki_filter: str | None = None,
    dry_run: bool = True,
) -> int:
    """Interactive v1 → v2 migration for `## Open items` session sections.

    Walks each v1 session note with a non-empty `## Open items` section
    and prompts per-bullet: issue / loose end / resolved / skip note.
    Pure rewriting logic lives in `migrate_open_items`; this is the TTY.

    Returns the count of notes migrated.
    """
    from lore_core.io import atomic_write_text
    from lore_core.lint import discover_wikis
    from lore_core.schema import parse_frontmatter
    from rich.prompt import Prompt

    wikis = discover_wikis(wiki_filter)
    migrated = 0
    for wiki_path in wikis:
        sessions_dir = wiki_path / "sessions"
        if not sessions_dir.exists():
            continue
        for session in sorted(sessions_dir.rglob("*.md")):
            text = session.read_text(errors="replace")
            fm = parse_frontmatter(text)
            if fm.get("schema_version") != 1:
                continue
            items = extract_open_items(text)
            if not items:
                continue

            rel = session.relative_to(wiki_path)
            console.print(f"\n[bold cyan]{wiki_path.name}/{rel}[/bold cyan]")
            decisions: list[tuple[str, str | None]] | None = []
            for item in items:
                console.print(f"  • {item}")
                choice = Prompt.ask(
                    "    → (i)ssue / (l)oose end / (r)esolved / (s)kip note",
                    choices=["i", "l", "r", "s"],
                    default="l",
                )
                if choice == "s":
                    decisions = None
                    break
                if choice == "i":
                    ref = Prompt.ask(
                        "      issue ref (e.g. #47), blank for 'needs issue'",
                        default="",
                    )
                    decisions.append(("issue", ref.strip() or None))
                elif choice == "l":
                    decisions.append(("loose_end", None))
                elif choice == "r":
                    decisions.append(("resolved", None))

            if decisions is None:
                console.print("  [yellow]skipped (left as v1)[/yellow]")
                continue

            new_text = migrate_open_items(text, decisions)
            if dry_run:
                console.print("  [dim]would rewrite to v2 (use --apply to commit)[/dim]")
            else:
                atomic_write_text(session, new_text)
                console.print("  [green]migrated to v2[/green]")
            migrated += 1

    verb = "would migrate" if dry_run else "migrated"
    console.print()
    console.print(f"[bold]{verb} {migrated} session note(s)[/bold]")
    return migrated
