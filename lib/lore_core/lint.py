"""Lore linter — scan all wikis, check health, regenerate catalogs.

Generates per-wiki:
  - _catalog.json  (machine-readable: note metadata, links, hierarchy)
  - _index.md      (LLM- and human-scannable knowledge index)
  - llms.txt       (alias of _index.md, for forward compatibility with
                    the emerging llms.txt convention)

Invoke programmatically via `run_lint()` or from the CLI:
    python -m lore_core.lint [--wiki NAME] [--check-only] [--json]
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from rich.console import Console

from lore_core.config import get_wiki_root
from lore_core.io import atomic_write_text
from lore_core.schema import (
    REQUIRED_FIELDS,
    compute_lifecycle,
    extract_wikilinks,
    parse_frontmatter,
)

# ---------------------------------------------------------------------------
# Tuning knobs
# ---------------------------------------------------------------------------

# status-vocabulary-minimalism: canonical notes are flagged stale at 180d.
STALENESS_DAYS = 180
OVERSIZED_LINES = 150
INDEX_MAX_LINES = 80
TODAY = date.today()

KNOWLEDGE_DIRS = ["projects", "concepts", "decisions", "papers"]
SKIP_DIRS = {"templates", "inbox", ".processed", ".obsidian"}
SKIP_FILES = {"CLAUDE.md", "README.md", "_index.md", "_catalog.json", "llms.txt", "_recent.md"}

console = Console()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Issue:
    severity: str  # ERROR, WARNING, INFO
    wiki: str
    file: str
    check: str
    message: str


@dataclass
class NoteInfo:
    path: str  # relative to wiki root
    filename: str  # stem without .md
    wiki: str
    note_type: str | None = None
    status: str | None = None  # legacy — superseded by `lifecycle`
    lifecycle: str = "canonical"  # canonical | draft | superseded
    superseded_by: str | list[str] | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    created: str | None = None
    last_reviewed: str | None = None
    lines: int = 0
    links_out: list[str] = field(default_factory=list)
    links_in: list[str] = field(default_factory=list)
    parent_folder: str | None = None
    is_index: bool = False
    children: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_wikis(wiki_filter: str | None = None) -> list[Path]:
    """Find all wiki directories (resolving symlinks)."""
    wiki_root = get_wiki_root()
    wikis: list[Path] = []
    if not wiki_root.exists():
        return wikis
    for entry in sorted(wiki_root.iterdir()):
        resolved = entry.resolve()
        if resolved.is_dir() and entry.name not in SKIP_DIRS:
            if wiki_filter and entry.name != wiki_filter:
                continue
            wikis.append(entry)
    return wikis


def discover_notes(wiki_path: Path) -> list[Path]:
    """Find all .md note files in knowledge directories and sessions/."""
    notes: list[Path] = []
    for kdir in KNOWLEDGE_DIRS:
        base = wiki_path / kdir
        if not base.exists():
            continue
        for md in sorted(base.rglob("*.md")):
            if md.name in SKIP_FILES:
                continue
            if any(part in SKIP_DIRS for part in md.parts):
                continue
            notes.append(md)
    sessions_dir = wiki_path / "sessions"
    if sessions_dir.exists():
        # In solo mode sessions live flat: sessions/*.md
        # In team mode they're sharded: sessions/<handle>/*.md
        # rglob covers both without extra branching.
        for md in sorted(sessions_dir.rglob("*.md")):
            if md.name in SKIP_FILES:
                continue
            if any(part in SKIP_DIRS for part in md.parts):
                continue
            notes.append(md)
    return notes


def count_lines(text: str) -> int:
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_frontmatter(note: NoteInfo, fm: dict, wiki_name: str) -> list[Issue]:
    """Check that required frontmatter fields are present and non-empty."""
    issues: list[Issue] = []
    note_type = fm.get("type", "")
    required = REQUIRED_FIELDS.get(note_type, REQUIRED_FIELDS["concept"])

    for fld in required:
        val = fm.get(fld)
        if val is None:
            issues.append(
                Issue(
                    severity="ERROR",
                    wiki=wiki_name,
                    file=note.path,
                    check="frontmatter",
                    message=f"missing `{fld}`",
                )
            )
        elif fld == "description" and str(val).strip() in ("", "''", '""'):
            pass  # reported separately by check_description
        elif isinstance(val, str) and not val.strip():
            issues.append(
                Issue(
                    severity="ERROR",
                    wiki=wiki_name,
                    file=note.path,
                    check="frontmatter",
                    message=f"empty `{fld}`",
                )
            )
    return issues


def check_staleness(note: NoteInfo, fm: dict, wiki_name: str) -> list[Issue]:
    """Flag canonical notes whose `last_reviewed` is too old.

    Drafts and superseded notes are skipped — staleness is only
    meaningful for notes that claim to be in force.
    """
    issues: list[Issue] = []
    if note.note_type == "session":
        return issues  # sessions are historical snapshots
    if compute_lifecycle(fm) != "canonical":
        return issues
    lr = fm.get("last_reviewed", "")
    if not lr:
        return issues
    try:
        lr_date = date.fromisoformat(str(lr))
    except (ValueError, TypeError):
        return issues
    days_old = (TODAY - lr_date).days
    if days_old > STALENESS_DAYS:
        issues.append(
            Issue(
                severity="WARNING",
                wiki=wiki_name,
                file=note.path,
                check="stale",
                message=f"last_reviewed {lr}, {days_old} days ago",
            )
        )
    return issues


def check_description(note: NoteInfo, fm: dict, wiki_name: str) -> list[Issue]:
    """Warn on empty description — fast-triage feature breaks without it."""
    issues: list[Issue] = []
    desc = fm.get("description", "")
    if desc is not None and str(desc).strip() in ("", "''", '""'):
        issues.append(
            Issue(
                severity="WARNING",
                wiki=wiki_name,
                file=note.path,
                check="empty_description",
                message="description is empty",
            )
        )
    return issues


def check_hierarchy(
    notes_by_wiki: dict[str, list[NoteInfo]],
    wiki_name: str,
    wiki_path: Path,
) -> list[Issue]:
    """Check hierarchy quality: missing indexes, oversized flat notes, unlinked sub-notes."""
    issues: list[Issue] = []
    notes = notes_by_wiki.get(wiki_name, [])

    for kdir in KNOWLEDGE_DIRS:
        base = wiki_path / kdir
        if not base.exists():
            continue
        for subfolder in sorted(base.iterdir()):
            if not subfolder.is_dir() or subfolder.name in SKIP_DIRS:
                continue
            folder_name = subfolder.name
            folder_notes = [n for n in notes if n.parent_folder == folder_name]
            index_candidates = [n for n in folder_notes if n.filename == folder_name]
            if not index_candidates:
                index_candidates = [
                    n for n in folder_notes if n.filename.startswith(folder_name + "-")
                ]
                if len(index_candidates) > 1:
                    sibling_names = {n.filename for n in folder_notes}
                    index_candidates.sort(
                        key=lambda n: sum(1 for link in n.links_out if link in sibling_names),
                        reverse=True,
                    )
            if not index_candidates:
                issues.append(
                    Issue(
                        severity="WARNING",
                        wiki=wiki_name,
                        file=f"{kdir}/{folder_name}/",
                        check="missing_index",
                        message=f"subfolder has no index note (expected {folder_name}.md)",
                    )
                )
                idx_filename = folder_name
            else:
                idx = index_candidates[0]
                idx.is_index = True
                idx.children = [n.filename for n in folder_notes if n.filename != idx.filename]
                if idx.lines > INDEX_MAX_LINES:
                    issues.append(
                        Issue(
                            severity="WARNING",
                            wiki=wiki_name,
                            file=idx.path,
                            check="index_too_large",
                            message=f"index note is {idx.lines} lines (target: <{INDEX_MAX_LINES})",
                        )
                    )
                idx_filename = idx.filename

            sub_notes = [n for n in folder_notes if n.filename != idx_filename]
            for sn in sub_notes:
                if idx_filename not in sn.links_out:
                    issues.append(
                        Issue(
                            severity="WARNING",
                            wiki=wiki_name,
                            file=sn.path,
                            check="unlinked_subnote",
                            message=f"no link back to parent index [[{idx_filename}]]",
                        )
                    )

    for n in notes:
        if n.note_type == "session":
            continue
        if n.parent_folder is None and n.lines > OVERSIZED_LINES:
            issues.append(
                Issue(
                    severity="WARNING",
                    wiki=wiki_name,
                    file=n.path,
                    check="oversized",
                    message=f"{n.lines} lines, no subfolder (split candidate)",
                )
            )

    return issues


def check_wikilinks(
    all_notes: dict[str, NoteInfo],
    scoped_wikis: set[str] | None = None,
) -> list[Issue]:
    """Check for broken wikilinks and orphan notes."""
    issues: list[Issue] = []
    known_names = set(all_notes.keys())

    for note in all_notes.values():
        if scoped_wikis and note.wiki not in scoped_wikis:
            continue
        for link in note.links_out:
            if link not in known_names:
                issues.append(
                    Issue(
                        severity="WARNING",
                        wiki=note.wiki,
                        file=note.path,
                        check="broken_link",
                        message=f"[[{link}]] target does not exist",
                    )
                )

    for note in all_notes.values():
        if scoped_wikis and note.wiki not in scoped_wikis:
            continue
        if note.note_type == "session":
            continue
        if not note.links_out and not note.links_in:
            issues.append(
                Issue(
                    severity="INFO",
                    wiki=note.wiki,
                    file=note.path,
                    check="orphan",
                    message="no incoming or outgoing wikilinks",
                )
            )

    return issues


# ---------------------------------------------------------------------------
# Catalog / index generation
# ---------------------------------------------------------------------------


def build_catalog(wiki_name: str, notes: list[NoteInfo], issues: list[Issue]) -> dict:
    """Build the per-wiki catalog for RAG navigation."""
    wiki_issues = [i for i in issues if i.wiki == wiki_name]

    sections: dict[str, list] = defaultdict(list)
    for n in notes:
        top_dir = n.path.split("/")[0] if "/" in n.path else "root"
        entry = {
            "path": n.path,
            "name": n.filename,
            "type": n.note_type,
            "status": n.status,  # legacy — retained during deprecation
            "lifecycle": n.lifecycle,  # canonical | draft | superseded
            "description": n.description,
            "tags": n.tags,
            "lines": n.lines,
            "links_out": n.links_out,
            "links_in": n.links_in,
        }
        if n.superseded_by:
            entry["superseded_by"] = n.superseded_by
        if n.is_index:
            entry["is_index"] = True
            entry["children"] = n.children
        if n.parent_folder:
            entry["parent_folder"] = n.parent_folder
        sections[top_dir].append(entry)

    return {
        "wiki": wiki_name,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "schema_version": 1,
        "stats": {
            "total_notes": len(notes),
            "errors": sum(1 for i in wiki_issues if i.severity == "ERROR"),
            "warnings": sum(1 for i in wiki_issues if i.severity == "WARNING"),
            "infos": sum(1 for i in wiki_issues if i.severity == "INFO"),
        },
        "sections": dict(sections),
        "issues": [
            {"severity": i.severity, "file": i.file, "check": i.check, "message": i.message}
            for i in wiki_issues
        ],
    }


def generate_index_md(wiki_name: str, notes: list[NoteInfo]) -> str:
    """Generate a human/LLM-readable _index.md for a wiki."""
    lines = [
        f"# {wiki_name.upper()} Knowledge Index",
        "",
        f"Auto-generated by lore_core on {TODAY.isoformat()}.",
        "Use this index to find notes without loading every file.",
        "",
    ]

    sections: dict[str, dict[str | None, list[NoteInfo]]] = defaultdict(lambda: defaultdict(list))
    for n in notes:
        if n.note_type == "session":
            continue
        parts = n.path.split("/")
        top_dir = parts[0] if parts else "root"
        sections[top_dir][n.parent_folder].append(n)

    def _badge(n: NoteInfo) -> str:
        if n.lifecycle == "draft":
            return " `DRAFT`"
        if n.lifecycle == "superseded":
            sb = n.superseded_by
            if isinstance(sb, list) and sb:
                targets = ", ".join(f"[[{s}]]" for s in sb)
            elif isinstance(sb, str) and sb:
                # Strip wrapping [[...]] if already present
                inner = sb.strip()
                if inner.startswith("[[") and inner.endswith("]]"):
                    targets = inner
                else:
                    targets = f"[[{inner}]]"
            else:
                targets = ""
            return f" `SUPERSEDED → {targets}`" if targets else " `SUPERSEDED`"
        return ""

    for section_name in ["projects", "concepts", "decisions", "papers"]:
        if section_name not in sections:
            continue
        folders = sections[section_name]
        lines.append(f"## {section_name.title()}")
        lines.append("")

        flat = folders.get(None, [])
        for n in sorted(flat, key=lambda x: x.filename):
            desc = n.description or "(no description)"
            tags_str = f" `{', '.join(n.tags)}`" if n.tags else ""
            lines.append(f"- **[[{n.filename}]]** — {desc}{_badge(n)}{tags_str}")

        for folder_name, folder_notes in sorted(
            ((k, v) for k, v in folders.items() if k is not None),
            key=lambda x: x[0],
        ):
            lines.append("")
            lines.append(f"### {folder_name}/")
            idx_notes = [n for n in folder_notes if n.is_index]
            sub_notes = [n for n in folder_notes if not n.is_index]
            for n in idx_notes:
                desc = n.description or "(no description)"
                lines.append(f"- **[[{n.filename}]]** (index) — {desc}{_badge(n)}")
            for n in sorted(sub_notes, key=lambda x: x.filename):
                desc = n.description or "(no description)"
                lines.append(f"  - [[{n.filename}]] — {desc}{_badge(n)}")

        lines.append("")

    session_count = sum(1 for n in notes if n.note_type == "session")
    if session_count:
        lines.append("## Sessions")
        lines.append("")
        lines.append(f"{session_count} session notes in `sessions/` (not indexed here).")
        lines.append("")

    return "\n".join(lines)


def generate_recent_md(wiki_path: Path, max_entries: int = 20) -> str | None:
    """Generate a _recent.md listing the most recent session notes as wikilinks.

    Returns the file content, or None if the wiki has no sessions/ directory.
    """
    sessions_dir = wiki_path / "sessions"
    if not sessions_dir.is_dir():
        return None

    # Collect all .md files under sessions/, excluding generated indexes
    session_files: list[Path] = []
    for md in sessions_dir.rglob("*.md"):
        if md.name in SKIP_FILES:
            continue
        session_files.append(md)

    if not session_files:
        return None

    # Sort newest-first: parent dir gives YYYY/MM, filename starts with DD-
    # Reverse-sorting the relative path (e.g. "2026/04/23-slug.md") yields
    # newest first because YYYY/MM/DD are all zero-padded.
    session_files.sort(
        key=lambda p: str(p.relative_to(sessions_dir)),
        reverse=True,
    )

    recent = session_files[:max_entries]

    lines = ["# Recent Sessions", ""]
    for sf in recent:
        lines.append(f"- [[{sf.stem}]]")
    lines.append("")  # trailing newline
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_lint(
    wiki_filter: str | None = None,
    check_only: bool = False,
    json_output: bool = False,
) -> dict:
    """Run full lint + catalog generation. Returns the report dict."""
    wikis = discover_wikis(wiki_filter)
    if not wikis:
        console.print(f"[red]No wikis found in {get_wiki_root()}[/red]")
        return {"error": "no wikis found"}

    all_wikis = discover_wikis(None)
    all_notes: dict[str, NoteInfo] = {}
    notes_by_wiki: dict[str, list[NoteInfo]] = defaultdict(list)

    # Phase 1: discover and parse every note across every wiki
    for wiki_path in all_wikis:
        wiki_name = wiki_path.name
        for fpath in discover_notes(wiki_path):
            text = fpath.read_text(errors="replace")
            fm = parse_frontmatter(text)
            links = extract_wikilinks(text)
            rel_path = str(fpath.relative_to(wiki_path))

            parts = fpath.relative_to(wiki_path).parts
            parent_folder: str | None = None
            if len(parts) >= 3 and parts[0] in KNOWLEDGE_DIRS:
                parent_folder = parts[1]

            note = NoteInfo(
                path=rel_path,
                filename=fpath.stem,
                wiki=wiki_name,
                note_type=fm.get("type"),
                status=fm.get("status"),
                lifecycle=compute_lifecycle(fm),
                superseded_by=fm.get("superseded_by"),
                description=fm.get("description"),
                tags=fm.get("tags", []) or [],
                created=str(fm["created"]) if fm.get("created") else None,
                last_reviewed=str(fm["last_reviewed"]) if fm.get("last_reviewed") else None,
                lines=count_lines(text),
                links_out=links,
                parent_folder=parent_folder,
            )

            if parent_folder and (
                fpath.stem == parent_folder or fpath.stem.startswith(parent_folder + "-")
            ):
                note.is_index = True

            all_notes[fpath.stem] = note
            notes_by_wiki[wiki_name].append(note)

    # Phase 2: link graph
    for name, note in all_notes.items():
        for link in note.links_out:
            if link in all_notes:
                all_notes[link].links_in.append(name)

    for note in all_notes.values():
        if note.is_index and note.parent_folder:
            note.children = [
                n.filename
                for n in notes_by_wiki[note.wiki]
                if n.parent_folder == note.parent_folder and n.filename != note.filename
            ]

    # Phase 3: checks
    all_issues: list[Issue] = []
    for wiki_path in wikis:
        wiki_name = wiki_path.name
        for note in notes_by_wiki[wiki_name]:
            text = (wiki_path / note.path).read_text(errors="replace")
            fm = parse_frontmatter(text)
            note_issues: list[Issue] = []
            note_issues.extend(check_frontmatter(note, fm, wiki_name))
            note_issues.extend(check_staleness(note, fm, wiki_name))
            note_issues.extend(check_description(note, fm, wiki_name))
            all_issues.extend(note_issues)
            note.issues = [f"{i.check}: {i.message}" for i in note_issues]
        all_issues.extend(check_hierarchy(notes_by_wiki, wiki_name, wiki_path))

    scoped_wiki_names = {w.name for w in wikis}
    all_issues.extend(check_wikilinks(all_notes, scoped_wiki_names))

    # Phase 4: regenerate catalogs + indexes (atomic writes, + llms.txt alias)
    if not check_only:
        for wiki_path in wikis:
            wiki_name = wiki_path.name
            notes = notes_by_wiki[wiki_name]

            catalog = build_catalog(wiki_name, notes, all_issues)
            atomic_write_text(
                wiki_path / "_catalog.json",
                json.dumps(catalog, indent=2, default=str),
            )

            index_md = generate_index_md(wiki_name, notes)
            atomic_write_text(wiki_path / "_index.md", index_md)
            # llms.txt alias — same content, canonical filename for forward
            # compatibility with the emerging llms.txt convention
            atomic_write_text(wiki_path / "llms.txt", index_md)

            # _recent.md — last 20 session notes as wikilinks
            recent_md = generate_recent_md(wiki_path)
            if recent_md is not None:
                atomic_write_text(wiki_path / "sessions" / "_recent.md", recent_md)

    # Phase 5: build report
    report = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "schema_version": 1,
        "wikis_scanned": [w.name for w in wikis],
        "total_notes": len(all_notes),
        "total_issues": len(all_issues),
        "by_severity": {
            "errors": sum(1 for i in all_issues if i.severity == "ERROR"),
            "warnings": sum(1 for i in all_issues if i.severity == "WARNING"),
            "infos": sum(1 for i in all_issues if i.severity == "INFO"),
        },
        "issues": [asdict(i) for i in all_issues],
    }

    if json_output:
        print(
            json.dumps(
                {"schema": "lore.lint/1", "data": report},
                indent=2,
                default=str,
            )
        )
    else:
        _print_report(report, wikis, notes_by_wiki, check_only)

    return report


def _print_report(
    report: dict,
    wikis: list[Path],
    notes_by_wiki: dict[str, list[NoteInfo]],
    check_only: bool,
) -> None:
    """Print a rich-formatted report to the terminal."""
    console.print()
    console.print("[bold]Lore Health Report[/bold]")
    console.print(f"Scanned: {', '.join(report['wikis_scanned'])}")
    console.print()

    issues = report["issues"]
    for wiki_path in wikis:
        wn = wiki_path.name
        wiki_issues = [i for i in issues if i["wiki"] == wn]
        note_count = len(notes_by_wiki[wn])
        console.print(f"[bold cyan]wiki/{wn}/[/bold cyan] ({note_count} notes)")
        if not wiki_issues:
            console.print("  [green]All clear[/green]")
            console.print()
            continue
        for sev, color in [("ERROR", "red"), ("WARNING", "yellow"), ("INFO", "dim")]:
            sev_issues = [i for i in wiki_issues if i["severity"] == sev]
            if not sev_issues:
                continue
            console.print(f"  [{color}]{sev}[/{color}]")
            for i in sev_issues:
                from rich.markup import escape as _esc
                console.print(f"    {_esc(i['file'])} — {_esc(i['message'])}")
        console.print()

    s = report["by_severity"]
    total = report["total_notes"]
    console.print(
        f"[bold]Summary[/bold]: {total} notes, "
        f"[red]{s['errors']} errors[/red], "
        f"[yellow]{s['warnings']} warnings[/yellow], "
        f"[dim]{s['infos']} info[/dim]"
    )
    if not check_only:
        console.print()
        console.print("[dim]Catalogs written: _catalog.json + _index.md + llms.txt per wiki[/dim]")


import typer  # noqa: E402

from lore_runtime.argv import argv_main  # noqa: E402

app = typer.Typer(
    add_completion=False,
    help=__doc__.splitlines()[0] if __doc__ else None,
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def lint(
    wiki: str = typer.Option(None, "--wiki", "-w", help="Scope to a single wiki."),
    check_only: bool = typer.Option(
        False, "--check-only", help="Lint only, skip catalog writes."
    ),
    json_out: bool = typer.Option(False, "--json", help="Output report as JSON."),
) -> None:
    """Lint the vault and (re)generate catalogs."""
    report = run_lint(
        wiki_filter=wiki,
        check_only=check_only,
        json_output=json_out,
    )
    if report.get("by_severity", {}).get("errors", 0) > 0:
        raise typer.Exit(code=1)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
