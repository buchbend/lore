"""Threads — passive continuation-linking surface.

A *thread* is a connected component of session notes that share at least
one non-boilerplate file in their ``files_touched`` frontmatter. The
algorithm runs entirely on the wiki's own metadata — no LLM, no
back-patching of older notes — and the entire output is a single
regenerated ``threads.md`` at the wiki root.

Why this shape (per Phase D architect review):
- Continuation linking computed at *write* time means every new note
  has to back-patch its predecessor, which is a write-side concurrency
  hazard and also bakes wrong links permanently.
- Computing at *read* time produces a derived view that evolves as the
  graph grows. If a future note bridges two previously-disjoint
  threads, the next regeneration unifies them.
- No LLM call: this is graph + heuristics over deterministic
  ``files_touched`` data already produced by Phase C.

Boilerplate handling matches the Phase C merge rule
(:data:`lore_core.session_writer._TOPIC_BOILERPLATE_FILES`) so a
note that only touches CLAUDE.md / pyproject.toml / lockfiles doesn't
become a thread bridge.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from lore_core.schema import parse_frontmatter
from lore_core.session_writer import _strip_boilerplate


@dataclass(frozen=True)
class Thread:
    """A connected-component group of related session notes."""

    label: str                          # short, deterministic — e.g. "auth.py"
    members: list[dict[str, Any]] = field(default_factory=list)
    shared_files: list[str] = field(default_factory=list)


def compute_threads(notes: list[dict[str, Any]]) -> list[Thread]:
    """Group notes into threads by shared (non-boilerplate) files.

    Each input dict must carry at least ``wikilink``, ``files_touched``,
    and ``created``. Notes whose boilerplate-stripped file set is empty
    are ignored — they have no signal to participate in linking.

    Singleton components (notes connected to nothing) are dropped: a
    thread is a *connection*, not a list of unrelated solo notes. The
    rendered ``threads.md`` then lists only multi-note threads, which
    is the form a reader actually wants ("which days continue this
    work?").
    """
    if not notes:
        return []

    # Build the working set: notes with a non-empty file set.
    indexed: list[tuple[int, dict[str, Any], set[str]]] = []
    for i, note in enumerate(notes):
        files = note.get("files_touched") or []
        stripped = _strip_boilerplate(files if isinstance(files, list) else [])
        if stripped:
            indexed.append((i, note, stripped))

    if not indexed:
        return []

    # Union-find over note indices, keyed by intersecting file set.
    parent = list(range(len(indexed)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(len(indexed)):
        for j in range(i + 1, len(indexed)):
            if indexed[i][2] & indexed[j][2]:
                union(i, j)

    # Group by component root.
    components: dict[int, list[int]] = {}
    for i in range(len(indexed)):
        root = find(i)
        components.setdefault(root, []).append(i)

    threads: list[Thread] = []
    for member_idxs in components.values():
        if len(member_idxs) < 2:
            continue  # solo note — not a thread
        member_notes = [indexed[i][1] for i in member_idxs]
        member_filesets = [indexed[i][2] for i in member_idxs]

        # Sort temporally; ties break on wikilink for determinism.
        member_notes.sort(key=lambda n: (str(n.get("created", "")), str(n.get("wikilink", ""))))

        # Label = file appearing in the most members; ties break on name.
        counter: Counter[str] = Counter()
        for files in member_filesets:
            for f in files:
                counter[f] += 1
        # Counter.most_common is unstable on ties → enforce alphabetical tie-break.
        label_candidates = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        label = label_candidates[0][0] if label_candidates else ""

        # shared_files: appear in every member.
        shared = set.intersection(*member_filesets) if member_filesets else set()

        threads.append(Thread(
            label=label,
            members=member_notes,
            shared_files=sorted(shared),
        ))

    # Stable order across runs: by label.
    threads.sort(key=lambda t: t.label)
    return threads


def render_threads_markdown(
    threads: list[Thread],
    *,
    generated_at: datetime,
) -> str:
    """Render a single Markdown index of all threads.

    The file is fully regenerated on each run — no stable IDs, no
    handwritten content preserved. Treat ``threads.md`` as a derived
    view, like ``_recent.md``.
    """
    lines: list[str] = [
        "# Threads",
        "",
        f"<!-- generated by Curator B at {generated_at.isoformat()} — do not edit by hand -->",
        "",
    ]
    if not threads:
        lines.append("_No threads yet — multi-day work that touches the same files will surface here._")
        lines.append("")
        return "\n".join(lines)

    for thread in threads:
        lines.append(f"## {thread.label}")
        lines.append("")
        for member in thread.members:
            wl = member.get("wikilink", "")
            created = member.get("created", "")
            if created:
                lines.append(f"- {wl}  _{created}_")
            else:
                lines.append(f"- {wl}")
        lines.append("")
    return "\n".join(lines)


def scan_session_notes(wiki_root: Path) -> list[dict[str, Any]]:
    """Walk a wiki's session notes and produce NoteRef-shaped dicts.

    Reads frontmatter only — the body is irrelevant to thread linking.
    Notes without parseable frontmatter or without a ``created`` field
    are silently skipped (treated as malformed).
    """
    sessions_dir = wiki_root / "sessions"
    if not sessions_dir.exists():
        return []

    out: list[dict[str, Any]] = []
    for md_path in sessions_dir.rglob("*.md"):
        if md_path.name.startswith("_"):
            continue  # skip _recent.md and the like
        # Defensive belt-and-suspenders: the function MUST tolerate any
        # corrupt note (bad UTF-8, surprise YAML edge case, race with a
        # concurrent writer) without aborting the scan. One malformed
        # file shouldn't suppress threads for the whole wiki.
        try:
            text = md_path.read_text()
        except OSError:
            continue
        try:
            fm = parse_frontmatter(text)
        except Exception:
            continue
        if not isinstance(fm, dict) or fm.get("type") != "session":
            continue
        created = fm.get("created")
        if not created:
            continue
        files = fm.get("files_touched") or []
        if not isinstance(files, list):
            files = []
        out.append({
            "wikilink": f"[[{md_path.stem}]]",
            "title": fm.get("description") or md_path.stem,
            "files_touched": [f for f in files if isinstance(f, str)],
            "created": str(created),
            "scope": fm.get("scope") or "",
            "path": md_path,
        })
    return out
