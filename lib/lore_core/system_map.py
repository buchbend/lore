"""The system map: the repos of one system and the edges between them.

A system map is a wiki note at ``<wiki>/systems/<name>.md`` whose
frontmatter ``type`` is ``system-map`` (PRD 0019, the system map)::

    type: system-map
    repos: [ccatobs/ops-db, ccatobs/data-center]
    edges:
      - consumer: ccatobs/data-center
        producer: ccatobs/ops-db
        interface: "SQLAlchemy models"
        pin: sha            # sha | tag | image | package | none
        breaks_when: "a migration changes a table data-center reads"

Agents read the edges; the body is prose for humans. SessionStart shows the
current repo's neighbours, and ``lore map`` prints the edges. A repo can
appear in more than one map. ``problems`` checks the format only, not that
an edge is true.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from lore_core.config import get_lore_root, list_wikis
from lore_core.git import current_repo
from lore_core.schema import parse_frontmatter

MAP_TYPE = "system-map"
SYSTEMS_DIR = "systems"
PINS = ("sha", "tag", "image", "package", "none")
REQUIRED = ("consumer", "producer", "interface")

#: Cap on the injected neighbours block. Edges past it are counted, not listed.
NEIGHBOURS_BUDGET_CHARS = 600


@dataclass(frozen=True)
class Edge:
    """One edge: *consumer* depends on *producer* through *interface*."""

    consumer: str
    producer: str
    interface: str
    system: str
    note: Path
    pin: str | None = None
    breaks_when: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "system": self.system,
            "note": str(self.note),
            "consumer": self.consumer,
            "producer": self.producer,
            "interface": self.interface,
            "pin": self.pin,
            "breaks_when": self.breaks_when,
        }

    def text(self) -> str:
        line = f"{self.consumer} <- {self.producer}: {self.interface}"
        if self.pin:
            line += f"; pin: {self.pin}"
        if self.breaks_when:
            line += f"; breaks when: {self.breaks_when}"
        return line


@dataclass(frozen=True)
class SystemMap:
    """One map note. ``raw_edges`` keeps the edges as written, for ``problems``."""

    name: str
    path: Path
    repos: list[str]
    edges: list[Edge]
    raw_edges: list[object] = field(default_factory=list)


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_map(path: Path) -> SystemMap | None:
    """The map in *path*, or None when the note is no system map."""
    try:
        fm = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    if not isinstance(fm, dict) or fm.get("type") != MAP_TYPE:
        return None
    repos = fm.get("repos") or []
    raw = fm.get("edges") or []
    if not isinstance(repos, list):
        repos = []
    if not isinstance(raw, list):
        raw = [raw]
    edges: list[Edge] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        values = {key: _text(item.get(key)) for key in REQUIRED}
        if not all(values.values()):
            continue
        edges.append(
            Edge(
                consumer=values["consumer"] or "",
                producer=values["producer"] or "",
                interface=values["interface"] or "",
                system=path.stem,
                note=path,
                pin=_text(item.get("pin")),
                breaks_when=_text(item.get("breaks_when")),
            )
        )
    return SystemMap(
        name=path.stem, path=path, repos=[str(r) for r in repos], edges=edges, raw_edges=raw
    )


def load_maps(wiki_dirs: Iterable[Path]) -> list[SystemMap]:
    """Every map under ``<wiki>/systems/`` of each wiki, by wiki then note name."""
    maps: list[SystemMap] = []
    for wiki in wiki_dirs:
        folder = Path(wiki) / SYSTEMS_DIR
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            found = parse_map(path)
            if found is not None:
                maps.append(found)
    return maps


def wiki_dirs(wiki: str | None = None) -> list[Path]:
    """The wiki *wiki* names, or every wiki under the Lore root."""
    root = get_lore_root()
    if wiki:
        return [root / "wiki" / wiki]
    return list_wikis(root)


def edges_for(maps: Iterable[SystemMap], repo: str) -> list[Edge]:
    """The edges that name *repo* as consumer or producer, across every map."""
    key = repo.casefold()
    return [
        edge
        for m in maps
        for edge in m.edges
        if key in (edge.consumer.casefold(), edge.producer.casefold())
    ]


def problems(system: SystemMap) -> list[str]:
    """Format errors of one map, each naming the note and the edge."""
    found: list[str] = []
    listed = {r.casefold() for r in system.repos}
    for number, item in enumerate(system.raw_edges, start=1):
        where = f"{system.path}: edge {number}"
        if not isinstance(item, dict):
            found.append(f"{where} is no mapping")
            continue
        missing = [key for key in REQUIRED if not _text(item.get(key))]
        if missing:
            found.extend(f"{where} lacks {key}" for key in missing)
            continue
        label = f"{where} ({_text(item['consumer'])} <- {_text(item['producer'])})"
        pin = _text(item.get("pin"))
        if pin is not None and pin not in PINS:
            found.append(f"{label}: unknown pin {pin!r}")
        for key in ("consumer", "producer"):
            repo = _text(item[key]) or ""
            if repo.casefold() not in listed:
                found.append(f"{label}: repo {repo} is not in repos")
    return found


def neighbours_block(
    edges: list[Edge], repo: str, budget: int = NEIGHBOURS_BUDGET_CHARS
) -> str | None:
    """The SessionStart block: one line per edge of *repo*, or None without an edge."""
    if not edges:
        return None
    key = repo.casefold()
    head = "## Neighbours (system map)\n"
    lines: list[str] = []
    for edge in edges:
        if edge.consumer.casefold() == key:
            line = f"- uses {edge.producer}: {edge.interface}"
            if edge.pin:
                line += f"; pin: {edge.pin}"
            if edge.breaks_when:
                line += f"; breaks when: {edge.breaks_when}"
        else:
            line = f"- used by {edge.consumer}: {edge.interface}"
        lines.append(line)
    listed = lines
    if len(head + "\n".join(lines)) > budget:
        # Keep room for the count line at its widest.
        used = len(head) + len(f"\n… {len(lines)} more: `lore map {repo}`")
        listed = []
        for line in lines:
            if used + len(line) + 1 > budget:
                break
            listed.append(line)
            used += len(line) + 1
    rest = len(lines) - len(listed)
    text = (head + "\n".join(listed)).rstrip("\n")
    if rest:
        text += f"\n… {rest} more: `lore map {repo}`"
    return text


def session_start_block(cwd: Path, wiki: str | None) -> str | None:
    """The neighbours of the repo at *cwd*, read from *wiki* or every wiki."""
    maps = load_maps(wiki_dirs(wiki))
    repo = current_repo(cwd) if maps else None
    if not repo:
        return None
    return neighbours_block(edges_for(maps, repo), repo)
