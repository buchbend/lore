"""The index of a repo: what it holds, one line per item (PRD 0020).

One group each for glossary terms, ADR titles, current PRDs, roadmap headings,
neighbours and the newest lab entries. Each group ends with the command that pulls the detail. A
group without content prints nothing. Pure filesystem reads: no model call,
no network call.

SessionStart injects the index and ``lore index`` prints it. No file holds a
copy (ADR 0020): the index reads its inputs at call time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lore_core import glossary, repo_docs

#: The index of a repo stays under this many characters.
BUDGET_CHARS = 2000


@dataclass
class Group:
    """One group of the index. *items* run oldest first, or newest first with *newest_first*.

    A cut drops the oldest."""

    heading: str
    items: list[str]
    detail: str
    joiner: str = "\n"
    newest_first: bool = False


def _number(path: str) -> str:
    return Path(path).stem.split("-", 1)[0]


def _title(entry: dict) -> str:
    """The title without a leading ``ADR 0012:`` or ``0012 —``: the line adds the number.

    Another leading number, as in ``3-tier routing``, stays."""
    num = _number(entry["path"])
    return re.sub(
        r"^(?:(?:ADR|PRD)\s*)?(\d+)\s*[:—–-]\s*",
        lambda m: "" if num.isdigit() and int(m[1]) == int(num) else m[0],
        str(entry["title"]),
    )


def _adr_line(entry: dict) -> str:
    return f"- ADR {_number(entry['path'])}: {_title(entry)}"


def _prd_line(entry: dict) -> str:
    status = f" ({entry['status']})" if entry.get("status") else ""
    return f"- PRD {_number(entry['path'])}: {_title(entry)}{status}"


def _roadmap_headings(repo_root: Path) -> list[str]:
    try:
        text = (repo_root / "ROADMAP.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [f"- {m.group(1).strip()}" for m in re.finditer(r"^## (.+)$", text, re.MULTILINE)]


#: How many lab entries the index lists.
LAB_ENTRIES = 3


def _lab_lines(repo_root: Path) -> list[str]:
    from lore_workflow import lab

    newest = lab.entries(repo_root, limit=LAB_ENTRIES)
    return [f"- {str(s.get('date', ''))[:10]}: {lab.question(b)}" for _, s, b in newest]


def groups(
    repo_root: Path, neighbours: list[str] | None = None, *, lab: bool = False
) -> list[Group]:
    """Every group of the index of *repo_root*, empty ones left out.

    *neighbours* are the edge lines of the system map; ``None`` leaves the
    group out. *lab* adds the newest lab entries (PRD 0020).
    """
    entries = glossary.load(repo_root)
    terms = list(dict.fromkeys(e.term for e in entries))
    files = sorted({str(e.path.relative_to(repo_root.resolve())) for e in entries})
    docs = {
        kind: [e for e in repo_docs.list_docs(repo_root, kind) if not e["is_index"]]
        for kind in ("adr", "prd")
    }
    out = [
        Group(
            f"### Terms ({', '.join(files)}), one fixed meaning each",
            terms,
            "Detail: `lore glossary <term>`",
            joiner=", ",
        ),
        Group("### ADRs", [_adr_line(e) for e in docs["adr"]], "Detail: `docs/adr/<number>-*.md`"),
        Group(
            "### Current PRDs",
            [_prd_line(e) for e in docs["prd"] if not repo_docs.is_shipped(e)],
            "Detail: `docs/prd/<number>-*.md`",
        ),
        Group("### Roadmap", _roadmap_headings(repo_root), "Detail: `ROADMAP.md`"),
        Group("### Neighbours (system map)", neighbours or [], "Detail: `lore map <repo>`"),
        Group(
            "### Lab entries",
            _lab_lines(repo_root) if lab else [],
            "Detail: `lore lab show`",
            newest_first=True,
        ),
    ]
    return [g for g in out if g.items]


def _render(gs: list[Group], keep: list[int]) -> str:
    parts = ["## Index (`lore index`)"]
    for g, k in zip(gs, keep, strict=True):
        parts.append(g.heading)
        if k:
            kept = g.items[:k] if g.newest_first else g.items[len(g.items) - k :]
            parts.append(g.joiner.join(kept))
        if k < len(g.items):
            parts.append(f"… {len(g.items) - k} more")
        parts.append(g.detail)
    return "\n".join(parts)


def index_text(gs: list[Group], budget: int = BUDGET_CHARS) -> str | None:
    """The index, or None without a group. Over *budget*, each group keeps its newest items."""
    if not gs:
        return None
    keep = [len(g.items) for g in gs]
    text = _render(gs, keep)
    # ponytail: drops one item per pass from the fullest group, O(n²) on a few hundred lines.
    while len(text) > budget and any(keep):
        keep[keep.index(max(keep))] -= 1
        text = _render(gs, keep)
    return text
