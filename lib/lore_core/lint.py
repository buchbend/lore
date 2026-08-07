"""Lore linter — scan all wikis, check health, regenerate catalogs.

Generates per-wiki:
  - _catalog.json  (machine-readable: note metadata, links, hierarchy)
  - _index.txt     (LLM- and human-scannable knowledge index; .txt
                    extension keeps it out of Obsidian's graph view)

Invoke programmatically via `run_lint()` or from the CLI:
    lore lint [--wiki NAME] [--check-only] [--json]
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from rich.console import Console

from lore_core.config import get_wiki_root
from lore_core.errors import NO_WIKIS, mcp_error
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
SKIP_FILES = {
    "CLAUDE.md",
    "README.md",
    "_index.txt",
    "_catalog.json",
    "_recent.txt",
    "_concepts.txt",
    "_decisions.txt",
    "_threads.txt",
    # Legacy filenames — kept in skip-list so lint still ignores them
    # on vaults that haven't been re-linted yet.
    "_recent.md",
    "_index.md",
    "llms.txt",
    "threads.md",
}

# Knowledge dirs whose notes are long by nature (each note is one paper) and
# therefore exempt from the ``oversized`` split-candidate warning.
OVERSIZED_EXEMPT_DIRS = {"papers"}

# Index detection: a prefix-matched note (e.g. ``lore-thesis.md`` in
# ``lore/``) is only treated as the folder's index when it actually links
# to at least this share of its siblings — otherwise it's just a long
# topical note that happens to share a name prefix with the folder, and
# promoting it would generate spurious ``index_too_large`` /
# ``unlinked_subnote`` warnings against children that have no obligation
# to backlink.
INDEX_PREFIX_LINK_RATIO = 0.7

# Wikilink targets that look like file paths, PR/issue refs, URLs, env
# vars, or version strings are not vault-note candidates and should not
# trigger ``broken_link`` warnings. The session-template wikilink
# discipline forbids these forms going forward; this predicate keeps the
# linter's signal clean for the historical session notes that still
# carry them.
_FILE_EXT_RE = re.compile(
    r"\.(py|md|yml|yaml|toml|json|sh|txt|rst|conf|cfg|ini|ts|tsx|js|jsx|"
    r"css|html|xml|csv|sql|env|lock|pyc|pyo)$",
    re.IGNORECASE,
)
_PR_ISSUE_RE = re.compile(
    r"^(?:PR\s*#?\d+|issue\s*#?\d+|#\d+|[\w./-]+#\d+)\b",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(r"^v?\d+\.\d+(?:\.\d+)?(?:[-+][\w.]+)?$")
_ENV_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}(?:\s*[/=].*)?$")


def _is_non_note_link_target(target: str) -> bool:
    """True when a wikilink target cannot reasonably resolve to a vault note.

    Used by :func:`check_wikilinks` to suppress ``broken_link`` warnings
    for targets that the session-note wikilink discipline already
    forbids: file/dir paths, PR/issue refs, URLs, env vars, version
    strings. Concept-style names (``Curator B``, ``CCAT Data Center``)
    still flag — those are real candidates either to be promoted into
    notes or to be removed by the author.
    """
    t = target.strip()
    if not t:
        return False
    # URLs and absolute paths.
    if "://" in t or t.startswith(("/", "~", "$")):
        return True
    # Paths (anything containing a slash) — also covers ``org/repo``,
    # ``feature/branch``, etc.
    if "/" in t:
        return True
    # File-extension suffix (``hooks.py``, ``CHANGELOG.md``, etc.).
    if _FILE_EXT_RE.search(t):
        return True
    # PR / issue references.
    if _PR_ISSUE_RE.match(t):
        return True
    # Version strings (``v0.13.1``, ``1.100.0``).
    if _VERSION_RE.match(t):
        return True
    # ENV-style identifiers (``CLAUDE_SESSION_ID``, ``LORE_OPENAI_MODEL``).
    if _ENV_VAR_RE.match(t):
        return True
    return False

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
    status: str | None = None  # legacy — superseded by `lifecycle`; scheduled for 1.0 removal
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
    """Find all .md note files in knowledge directories."""
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
            # Filter on both ``parent_folder`` and the kdir prefix —
            # ``parent_folder`` only stores the basename, so without the
            # path-prefix guard a folder like ``concepts/lore/`` and
            # ``decisions/lore/`` would pool their notes together and
            # generate spurious cross-folder index/subnote warnings.
            folder_path_prefix = f"{kdir}/{folder_name}/"
            folder_notes = [
                n for n in notes
                if n.parent_folder == folder_name and n.path.startswith(folder_path_prefix)
            ]
            index_candidates = [n for n in folder_notes if n.filename == folder_name]
            if not index_candidates:
                # Prefix-match fallback: a note named ``<folder>-something.md``
                # only counts as the folder's index when it actually behaves
                # like one — i.e. it links to at least
                # INDEX_PREFIX_LINK_RATIO of its siblings. Otherwise it's a
                # long topical note that happens to share a prefix with the
                # folder name and shouldn't drag spurious index/subnote
                # warnings onto its neighbours.
                sibling_names = {n.filename for n in folder_notes}
                prefix_matches = [
                    n for n in folder_notes if n.filename.startswith(folder_name + "-")
                ]
                siblings_count = max(len(folder_notes) - 1, 1)
                threshold = max(1, int(siblings_count * INDEX_PREFIX_LINK_RATIO))
                navigational = [
                    n for n in prefix_matches
                    if sum(1 for link in n.links_out if link in sibling_names) >= threshold
                ]
                if navigational:
                    navigational.sort(
                        key=lambda n: sum(1 for link in n.links_out if link in sibling_names),
                        reverse=True,
                    )
                    index_candidates = navigational
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
                # No real index → don't fire ``unlinked_subnote`` against
                # every sibling demanding a backlink to a phantom target.
                # The single ``missing_index`` line is the actionable signal.
                continue

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
            # Notes in dirs that are long by nature (one paper per note)
            # are exempt from the split-candidate suggestion.
            top_dir = n.path.split("/", 1)[0] if "/" in n.path else ""
            if top_dir in OVERSIZED_EXEMPT_DIRS:
                continue
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


def _repo_by_project_slug(wiki_name: str) -> dict[str, Path]:
    """Map each project slug in this wiki to the repo path attached to it.

    Empty when nothing is attached, or when the attachment machinery isn't
    importable — a drift check is advisory and never fails the lint.
    """
    try:
        from lore_core.config import resolve_lore_root
        from lore_core.state.attachments import AttachmentsFile
    except ImportError:
        return {}

    lore_root = resolve_lore_root()
    if lore_root is None or not lore_root.exists():
        return {}
    af = AttachmentsFile(lore_root)
    af.load()

    repos: dict[str, Path] = {}
    for a in af.all():
        if a.wiki != wiki_name or not a.scope:
            continue
        slug = a.scope.rsplit(":", 1)[-1]
        if slug:
            repos[slug] = a.path
    return repos


def _orientation_notes(projects_dir: Path) -> list[tuple[Path, str]]:
    """Every project orientation note in a wiki, as (path, slug) pairs.

    Covers both layouts: the current ``projects/<slug>/<slug>.md`` folder
    and the legacy flat ``projects/<slug>.md``.
    """
    found: list[tuple[Path, str]] = []
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        slug = project_dir.name
        orientation = project_dir / f"{slug}.md"
        if orientation.is_file():
            found.append((orientation, slug))
    for legacy in sorted(projects_dir.glob("*.md")):
        if not legacy.name.startswith("_"):
            found.append((legacy, legacy.stem))
    return found


def check_agent_guidance_sync(wiki_path: Path, wiki_name: str) -> list[Issue]:
    """Surface drift between a project's orientation note and its repo.

    For each project orientation note carrying a ``## Agent guidance``
    H2 section, locate the attached repo via ``attachments.json`` and
    compare normalised content against its AGENTS.md / CLAUDE.md.

    Best-effort: a missing attachments file, missing repo, or malformed
    note skips silently rather than failing the lint. The check targets
    the user-visible action ("here's a drift, run sync"), not consistency
    enforcement.
    """
    try:
        from lore_core.projects.agent_sync import compute_sync_status
    except ImportError:
        return []

    projects_dir = wiki_path / "projects"
    if not projects_dir.is_dir():
        return []

    repo_by_slug = _repo_by_project_slug(wiki_name)
    issues: list[Issue] = []
    for orientation, slug in _orientation_notes(projects_dir):
        repo_root = repo_by_slug.get(slug)
        if repo_root is None or not repo_root.exists():
            continue
        try:
            status = compute_sync_status(orientation, repo_root)
        except Exception:  # noqa: BLE001 - never fail lint on sync drift
            continue
        if not (status.orientation_has_section and not status.in_sync):
            continue
        issues.append(
            Issue(
                severity="WARNING",
                wiki=wiki_name,
                file=str(orientation.relative_to(wiki_path)),
                check="agent_guidance_drift",
                message=(
                    f"`## Agent guidance` in orientation differs from "
                    f"repo's AGENTS.md/CLAUDE.md. Run "
                    f"`lore project sync {slug} --to-repo` "
                    f"or `--from-repo` to reconcile."
                ),
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
            if link in known_names:
                continue
            # Suppress targets that the wikilink discipline forbids — file
            # paths, PR/issue refs, version strings, env vars, URLs. These
            # are historical noise from session notes written before the
            # discipline was tightened; flagging them buries real signal.
            if _is_non_note_link_target(link):
                continue
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
    """Build the per-wiki catalog for RAG navigation.

    Includes a top-level ``slug_index: {slug: relpath}`` for O(1) slug
    resolution by the MCP server (replaces the per-call section
    iteration). Duplicate stems within the wiki produce a
    ``duplicate_stem`` lint warning; the first occurrence (by sorted
    note order) wins in ``slug_index``.
    """
    slug_index, dup_issues = _build_slug_index(wiki_name, notes)
    issues.extend(dup_issues)

    wiki_issues = [i for i in issues if i.wiki == wiki_name]

    sections: dict[str, list] = defaultdict(list)
    for n in notes:
        top_dir = n.path.split("/")[0] if "/" in n.path else "root"
        entry = {
            "path": n.path,
            "name": n.filename,
            "type": n.note_type,
            "status": n.status,  # legacy — retained until 1.0; new code reads `lifecycle`
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
        "slug_index": slug_index,
        "issues": [
            {"severity": i.severity, "file": i.file, "check": i.check, "message": i.message}
            for i in wiki_issues
        ],
    }


def _build_slug_index(
    wiki_name: str, notes: list[NoteInfo]
) -> tuple[dict[str, str], list[Issue]]:
    """Return ``(slug_index, duplicate_issues)`` for the catalog.

    Iterates notes in sorted-by-path order so the winner on collision is
    deterministic across runs. Each duplicate stem produces ONE
    ``duplicate_stem`` WARNING listing all colliding paths so the user
    can rename one — ambiguous slugs break wikilink resolution.
    """
    by_stem: dict[str, list[str]] = defaultdict(list)
    for n in sorted(notes, key=lambda x: x.path):
        by_stem[n.filename].append(n.path)

    slug_index: dict[str, str] = {}
    dup_issues: list[Issue] = []
    for stem, paths in sorted(by_stem.items()):
        slug_index[stem] = paths[0]
        if len(paths) > 1:
            dup_issues.append(
                Issue(
                    severity="WARNING",
                    wiki=wiki_name,
                    file=paths[0],
                    check="duplicate_stem",
                    message=(
                        f"slug {stem!r} is shared by {len(paths)} notes: "
                        f"{', '.join(paths)}. Wikilinks will resolve to "
                        f"{paths[0]} (first by sort order); rename the others."
                    ),
                )
            )
    return slug_index, dup_issues


def generate_index_txt(wiki_name: str, notes: list[NoteInfo]) -> str:
    """Generate a human/LLM-readable _index.txt for a wiki.

    Markdown-formatted body in a .txt file: dense bullet lists with
    ``[[wikilinks]]`` for LLM navigation. The .txt extension keeps the
    file out of Obsidian's graph view (which only ingests .md), so the
    index doesn't become a god-object node tied to every note.
    """
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

    return "\n".join(lines)


def generate_type_collection_txt(
    wiki_name: str,
    notes: list["NoteInfo"],
    note_type: str,
    title: str,
) -> str | None:
    """Generate a flat ``_<type>.txt`` collection of all notes of a given
    frontmatter ``type:``.

    Returns the file content, or None if there are no notes of that type
    in the wiki. Format: a Markdown-formatted body in a ``.txt`` file —
    dense bullet list of wikilinks with descriptions. The ``.txt``
    extension keeps it out of the wikilink graph (``wikilinks.py``
    globs ``.md`` only), so the collection doesn't become a god-node.

    Notes are sorted by filename for stable output across runs. Draft
    notes get a ``DRAFT`` badge; superseded notes get ``SUPERSEDED → [[target]]``.
    """
    matching = [n for n in notes if n.note_type == note_type]
    if not matching:
        return None

    matching.sort(key=lambda n: n.filename)

    lines = [
        f"# {wiki_name.upper()} — {title}",
        "",
        f"Auto-generated by lore_core on {TODAY.isoformat()}.",
        f"All {note_type} notes in this wiki, regardless of folder location.",
        "",
    ]

    def _badge(n: "NoteInfo") -> str:
        if n.lifecycle == "draft":
            return " `DRAFT`"
        if n.lifecycle == "superseded":
            sb = n.superseded_by
            if isinstance(sb, list) and sb:
                targets = ", ".join(f"[[{s}]]" for s in sb)
            elif isinstance(sb, str) and sb:
                inner = sb.strip()
                if inner.startswith("[[") and inner.endswith("]]"):
                    targets = inner
                else:
                    targets = f"[[{inner}]]"
            else:
                targets = ""
            return f" `SUPERSEDED → {targets}`" if targets else " `SUPERSEDED`"
        return ""

    for n in matching:
        desc = n.description or "(no description)"
        lines.append(f"- [[{n.filename}]] — {desc}{_badge(n)}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _parse_note(fpath: Path, wiki_path: Path, wiki_name: str) -> NoteInfo:
    """Read one note file into its catalog record."""
    text = fpath.read_text(errors="replace")
    fm = parse_frontmatter(text)

    parts = fpath.relative_to(wiki_path).parts
    parent_folder: str | None = None
    if len(parts) >= 3 and parts[0] in KNOWLEDGE_DIRS:
        parent_folder = parts[1]

    note = NoteInfo(
        path=str(fpath.relative_to(wiki_path)),
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
        links_out=extract_wikilinks(text),
        parent_folder=parent_folder,
    )
    # A folder's index note is the one named after the folder (or prefixed
    # with it), e.g. concepts/lore/lore.md or concepts/lore/lore-mcp.md.
    if parent_folder and (
        fpath.stem == parent_folder or fpath.stem.startswith(parent_folder + "-")
    ):
        note.is_index = True
    return note


def _scan_notes(
    wikis: list[Path],
) -> tuple[dict[str, NoteInfo], dict[str, list[NoteInfo]]]:
    """Parse every note in every wiki, keyed by slug and grouped by wiki.

    Always scans *all* wikis even when the lint is scoped to one, because
    wikilink targets resolve across the whole vault.
    """
    all_notes: dict[str, NoteInfo] = {}
    notes_by_wiki: dict[str, list[NoteInfo]] = defaultdict(list)
    for wiki_path in wikis:
        wiki_name = wiki_path.name
        for fpath in discover_notes(wiki_path):
            note = _parse_note(fpath, wiki_path, wiki_name)
            all_notes[fpath.stem] = note
            notes_by_wiki[wiki_name].append(note)
    return all_notes, notes_by_wiki


def _build_link_graph(
    all_notes: dict[str, NoteInfo],
    notes_by_wiki: dict[str, list[NoteInfo]],
) -> None:
    """Fill in backlinks and index children from the parsed link lists."""
    for name, note in all_notes.items():
        for link in note.links_out:
            if link in all_notes:
                all_notes[link].links_in.append(name)

    for note in all_notes.values():
        if not (note.is_index and note.parent_folder):
            continue
        # parent_folder is just the basename; require sibling notes to
        # share the index's full directory prefix so that, e.g.,
        # ``concepts/lore/`` and ``decisions/lore/`` don't bleed
        # children into each other's indexes.
        note_dir = note.path.rsplit("/", 1)[0] + "/" if "/" in note.path else ""
        note.children = [
            n.filename
            for n in notes_by_wiki[note.wiki]
            if n.parent_folder == note.parent_folder
            and n.filename != note.filename
            and n.path.startswith(note_dir)
        ]


def _collect_issues(
    wikis: list[Path],
    all_notes: dict[str, NoteInfo],
    notes_by_wiki: dict[str, list[NoteInfo]],
) -> list[Issue]:
    """Run every check over the scoped wikis; also records issues on each note."""
    all_issues: list[Issue] = []
    for wiki_path in wikis:
        wiki_name = wiki_path.name
        for note in notes_by_wiki[wiki_name]:
            fm = parse_frontmatter((wiki_path / note.path).read_text(errors="replace"))
            note_issues = [
                *check_frontmatter(note, fm, wiki_name),
                *check_staleness(note, fm, wiki_name),
                *check_description(note, fm, wiki_name),
            ]
            all_issues.extend(note_issues)
            note.issues = [f"{i.check}: {i.message}" for i in note_issues]
        all_issues.extend(check_hierarchy(notes_by_wiki, wiki_name, wiki_path))
        all_issues.extend(check_agent_guidance_sync(wiki_path, wiki_name))

    all_issues.extend(check_wikilinks(all_notes, {w.name for w in wikis}))
    return all_issues


def _orphan_paths(wiki_path: Path) -> list[str]:
    """Wiki-relative paths of notes holding orphan links.

    Cached into the catalog so the read-time freshness check is an O(1)
    membership test instead of a whole-wiki walk on every retrieval.
    """
    try:
        from lore_core.wikilinks import find_orphan_links

        return sorted(
            {str(p.relative_to(wiki_path)) for p, _slug, _off in find_orphan_links(wiki_path)}
        )
    except Exception:
        # An orphan-scan failure must not break lint.
        return []


def _drop_legacy_artifacts(wiki_path: Path) -> None:
    """Remove generated files older lore versions wrote, so vaults self-heal.

    ``_index.md`` (a god-object in Obsidian's graph) and ``llms.txt`` (a
    markdown-in-txt mirror) are superseded by ``_index.txt``; ``threads.md``
    by ``_threads.txt``; ``sessions/_recent.md`` by its ``.txt`` sibling.
    The ``.md`` collections used to be wikilink-graph nodes — the ``.txt``
    versions are not.
    """
    for legacy in ("_index.md", "llms.txt", "threads.md", "sessions/_recent.md"):
        stale = wiki_path / legacy
        if stale.exists():
            stale.unlink()


def _regenerate_wiki(
    wiki_path: Path, notes: list[NoteInfo], all_issues: list[Issue]
) -> None:
    """Rewrite one wiki's catalog, index and collection files."""
    wiki_name = wiki_path.name

    catalog = build_catalog(wiki_name, notes, all_issues)
    catalog["orphan_set"] = _orphan_paths(wiki_path)
    atomic_write_text(
        wiki_path / "_catalog.json", json.dumps(catalog, indent=2, default=str)
    )

    atomic_write_text(wiki_path / "_index.txt", generate_index_txt(wiki_name, notes))
    _drop_legacy_artifacts(wiki_path)

    # _concepts.txt, _decisions.txt — flat per-type collections at wiki root.
    # The ``.txt`` extension excludes them from the wikilink graph (which
    # globs ``.md`` only), so they don't become god-nodes.
    for note_type, title, filename in (
        ("concept", "Concepts", "_concepts.txt"),
        ("decision", "Decisions", "_decisions.txt"),
    ):
        body = generate_type_collection_txt(
            wiki_name, notes, note_type=note_type, title=title
        )
        if body is not None:
            atomic_write_text(wiki_path / filename, body)


def _build_report(
    wikis: list[Path], all_notes: dict[str, NoteInfo], all_issues: list[Issue]
) -> dict:
    """Assemble the machine-readable lint report."""
    return {
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


def run_lint(
    wiki_filter: str | None = None,
    check_only: bool = False,
    json_output: bool = False,
) -> dict:
    """Run full lint + catalog generation. Returns the report dict."""
    wikis = discover_wikis(wiki_filter)
    if not wikis:
        console.print(f"[red]No wikis found in {get_wiki_root()}[/red]")
        return mcp_error(
            NO_WIKIS,
            "no wikis found",
            next_=f"create a wiki under {get_wiki_root()} or run `lore init`",
        )

    all_notes, notes_by_wiki = _scan_notes(discover_wikis(None))
    _build_link_graph(all_notes, notes_by_wiki)
    all_issues = _collect_issues(wikis, all_notes, notes_by_wiki)

    if not check_only:
        for wiki_path in wikis:
            _regenerate_wiki(wiki_path, notes_by_wiki[wiki_path.name], all_issues)

    report = _build_report(wikis, all_notes, all_issues)

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
        console.print("[dim]Catalogs written: _catalog.json + _index.txt per wiki[/dim]")


