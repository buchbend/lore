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
from lore_core.topic_files import basename as _basename, strip_boilerplate as _strip_boilerplate


@dataclass(frozen=True)
class NoteRef:
    """Frontmatter-derived view of a session note used for thread linking.

    All fields default to empty so partial frontmatter doesn't trip the
    constructor. ``files_touched`` is a list (mutable) only so that
    test fixtures stay readable; production code never mutates it.
    """

    wikilink: str
    title: str = ""
    summary: str = ""
    files_touched: list[str] = field(default_factory=list)
    created: str = ""
    scope: str = ""
    path: Path | None = None


@dataclass(frozen=True)
class Thread:
    """A connected-component group of related session notes.

    Two label slots:
    - ``label``: deterministic algorithmic fallback (most-shared file
      basename across members). Always populated.
    - ``llm_label``: optional concise topical title produced by a
      simple-tier LLM call over members' titles + summaries. When
      empty, render falls back to ``label``.

    Splitting them this way means the rendered heading degrades
    gracefully: zero LLM budget → file-basename headings; LLM
    available → coherent topic titles like "Curator A day-split +
    topic-aware merge" instead of "curator_a.py".
    """

    label: str
    members: list[NoteRef] = field(default_factory=list)
    shared_files: list[str] = field(default_factory=list)
    llm_label: str = ""


def compute_threads(notes: list[NoteRef]) -> list[Thread]:
    """Group notes into threads by shared (non-boilerplate) files.

    Notes whose boilerplate-stripped file set is empty are ignored —
    they have no signal to participate in linking.

    Singleton components (notes connected to nothing) are dropped: a
    thread is a *connection*, not a list of unrelated solo notes. The
    rendered ``threads.md`` then lists only multi-note threads, which
    is the form a reader actually wants ("which days continue this
    work?").
    """
    if not notes:
        return []

    # Build the working set: notes with a non-empty file set.
    indexed: list[tuple[int, NoteRef, set[str]]] = []
    for i, note in enumerate(notes):
        stripped = _strip_boilerplate(list(note.files_touched))
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
        member_notes.sort(key=lambda n: (n.created, n.wikilink))

        # Label = basename of the FULL PATH appearing in the most members.
        # We count paths (not basenames) so a single note touching N
        # different files that happen to share a basename (e.g. multiple
        # `skills/<name>/SKILL.md` paths) doesn't outvote a real
        # cross-note file. Each (note, path) pair contributes one vote
        # because member_filesets entries are sets. The basename is
        # derived from the winning path purely for display.
        counter: Counter[str] = Counter()
        for files in member_filesets:
            for f in files:
                counter[f] += 1
        label_candidates = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        label_path = label_candidates[0][0] if label_candidates else ""
        label = _basename(label_path) or label_path

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


# ---------------------------------------------------------------------------
# Optional LLM-derived thread labels
# ---------------------------------------------------------------------------

# Hard cap so a 50-note thread can't blow up the prompt. Truncation here
# is fine — the LLM only needs a few representative notes to produce a
# topical label.
_LABEL_MAX_MEMBERS_PER_PROMPT = 12
_LABEL_MAX_TITLE_CHARS = 120
_LABEL_MAX_SUMMARY_CHARS = 300

_LABEL_TOOL_SCHEMA: dict[str, Any] = {
    "name": "label",
    "description": "Emit a concise topical label for a thread of related session notes.",
    "input_schema": {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "description": (
                    "5-10 word topical title for the thread. Be specific — "
                    "'Curator A day-split + topic-aware merge' beats 'code work'. "
                    "Use the language and vocabulary of the source notes."
                ),
            },
        },
        "required": ["label"],
    },
}


def _build_label_prompt(thread: Thread) -> str:
    """Compose a tiny structured prompt from a thread's members.

    The prompt is deterministic and small (a few hundred chars per
    member, capped at 12 members) so even the simple tier handles it
    in under a second.
    """
    members = thread.members[:_LABEL_MAX_MEMBERS_PER_PROMPT]
    lines = [
        "Produce a concise topical label (5-10 words) for the thread of",
        "session notes below. They are connected via shared file edits and",
        "represent continuing work on the same topic.",
        "",
        "Return JSON via the `label` tool. Use the language of the notes.",
        "",
        "--- thread members (oldest → newest) ---",
    ]
    for m in members:
        title = (m.title or m.wikilink)[:_LABEL_MAX_TITLE_CHARS]
        summary = m.summary[:_LABEL_MAX_SUMMARY_CHARS]
        lines.append(f"- {title}")
        if summary:
            lines.append(f"    {summary}")
    if thread.shared_files:
        lines.append("")
        lines.append(f"Files commonly touched: {', '.join(thread.shared_files[:8])}")
    return "\n".join(lines)


def label_threads_with_llm(
    threads: list[Thread],
    *,
    llm_client: Any,
    model_resolver: Any,
) -> list[Thread]:
    """Enrich each thread with a simple-tier LLM-derived ``llm_label``.

    Best-effort: any failure (no client, missing simple-tier model,
    rate-limit, bad gateway) preserves the algorithmic ``label`` and
    leaves ``llm_label=""``. Threads.md regen must never fail because
    a label call did. One LLM call per thread, simple tier.
    """
    if llm_client is None or not threads:
        return threads

    try:
        model = model_resolver("simple")
    except Exception:
        return threads
    if not model:
        return threads

    enriched: list[Thread] = []
    for thread in threads:
        label = ""
        try:
            prompt = _build_label_prompt(thread)
            resp = llm_client.messages.create(
                model=model,
                max_tokens=64,
                tools=[_LABEL_TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": "label"},
                messages=[{"role": "user", "content": prompt}],
            )
            for block in getattr(resp, "content", []):
                btype = getattr(block, "type", None)
                if btype == "tool_use":
                    inp = getattr(block, "input", None)
                    if isinstance(inp, dict):
                        candidate = inp.get("label")
                        if isinstance(candidate, str) and candidate.strip():
                            label = candidate.strip()
                            break
        except Exception:
            label = ""
        # Frozen dataclass — rebuild with the new field.
        enriched.append(Thread(
            label=thread.label,
            members=thread.members,
            shared_files=thread.shared_files,
            llm_label=label,
        ))
    return enriched


def render_threads_markdown(
    threads: list[Thread],
    *,
    generated_at: datetime,
    notes_scanned: int | None = None,
) -> str:
    """Render a single Markdown index of all threads.

    The file is fully regenerated on each run — no stable IDs, no
    handwritten content preserved. Treat ``threads.md`` as a derived
    view, like ``_recent.md``.

    ``notes_scanned`` is optional; when given, the empty-state copy
    surfaces it so the user can tell "no notes yet" apart from
    "notes exist but none share files yet".
    """
    lines: list[str] = [
        "# Threads",
        "",
        f"<!-- generated by Curator B at {generated_at.isoformat()} — do not edit by hand -->",
        "",
    ]
    if not threads:
        if notes_scanned and notes_scanned > 0:
            lines.append(
                f"_{notes_scanned} session note(s) scanned; none share "
                "non-boilerplate files yet — multi-day work on overlapping "
                "files will surface here as it accumulates._"
            )
        else:
            lines.append(
                "_No threads yet — multi-day work that touches the same "
                "files will surface here._"
            )
        lines.append("")
        return "\n".join(lines)

    for thread in threads:
        heading = thread.llm_label.strip() or thread.label
        lines.append(f"## {heading}")
        # When the LLM-derived heading shadows the algorithmic label,
        # surface the file-basename label as a small annotation so the
        # reader still knows the structural grouping signal.
        if thread.llm_label.strip() and thread.label and thread.llm_label.strip() != thread.label:
            lines.append("")
            lines.append(f"_files: {thread.label} · {len(thread.members)} notes_")
        lines.append("")
        for member in thread.members:
            if member.created:
                lines.append(f"- {member.wikilink}  _{member.created}_")
            else:
                lines.append(f"- {member.wikilink}")
        lines.append("")
    return "\n".join(lines)


def scan_session_notes(wiki_root: Path) -> list[NoteRef]:
    """Walk a wiki's session notes and produce :class:`NoteRef` objects.

    Reads frontmatter only — the body is irrelevant to thread linking.
    Notes without parseable frontmatter or without a ``created`` field
    are silently skipped (treated as malformed).
    """
    sessions_dir = wiki_root / "sessions"
    if not sessions_dir.exists():
        return []

    out: list[NoteRef] = []
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
        out.append(NoteRef(
            wikilink=f"[[{md_path.stem}]]",
            title=str(fm.get("description") or md_path.stem),
            summary=str(fm.get("summary") or ""),
            files_touched=[f for f in files if isinstance(f, str)],
            created=str(created),
            scope=str(fm.get("scope") or ""),
            path=md_path,
        ))
    return out
