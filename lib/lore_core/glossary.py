"""The glossary of a repo: the terms its ``CONTEXT.md`` defines.

SessionStart injects the glossary index: the term names only, a few hundred
characters. ``lore glossary <term>`` prints one definition when the agent
needs it. The agent no longer reads the whole file to learn one word.

Two entry shapes count as a definition. Lore's own list items::

    - **Ledger** — the working list of one build run …

and the shape ``grilling`` writes (``CONTEXT-FORMAT.md``)::

    **Order**:
    A request …

An entry runs to the next entry, the next heading or the next blank line.
A bold run inside a sentence is emphasis, not a term. A repo with a
``CONTEXT-MAP.md`` has one glossary per context the map links.

Standard library only.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

CONTEXT_NAME = "CONTEXT.md"
MAP_NAME = "CONTEXT-MAP.md"

#: Cap on the injected index. Terms past it are counted, not listed.
INDEX_BUDGET_CHARS = 1500

_START = re.compile(r"^(?:[-*]\s+)?\*\*(?P<term>[^*\n]+?)\*\*(?P<tail>.*)$")
# What may follow the bold term on its line for the line to start an entry.
_TAIL = re.compile(r"^\s*(?:$|:|—|–|-\s|\()")
_MAP_LINK = re.compile(r"\]\(([^)\s]*CONTEXT\.md)\)")


@dataclass(frozen=True)
class Entry:
    """One definition. ``line`` is 1-based in ``path``."""

    term: str
    text: str
    path: Path
    line: int


def find_repo_root(start: Path) -> Path | None:
    """The folder that holds the first ``.git`` above *start*, without git."""
    try:
        current = Path(start).resolve()
    except OSError:
        return None
    for folder in (current, *current.parents):
        if (folder / ".git").exists():
            return folder
    return None


def context_files(repo_root: Path) -> list[Path]:
    """The glossary files of *repo_root*: the contexts a map links, or ``CONTEXT.md``."""
    map_path = repo_root / MAP_NAME
    if map_path.is_file():
        root = repo_root.resolve()
        found: list[Path] = []
        for link in _MAP_LINK.findall(map_path.read_text(encoding="utf-8", errors="replace")):
            path = (repo_root / link).resolve()
            if path.is_relative_to(root) and path.is_file() and path not in found:
                found.append(path)
        return found
    single = (repo_root / CONTEXT_NAME).resolve()
    return [single] if single.is_file() else []


def _term(raw: str) -> str:
    return raw.strip().rstrip(":").strip()


def parse_entries(text: str, path: Path) -> list[Entry]:
    """Every definition in *text*, in file order."""
    entries: list[Entry] = []
    term: str | None = None
    body: list[str] = []
    start = 0

    def close() -> None:
        nonlocal term
        if term:
            entries.append(Entry(term=term, text="\n".join(body).rstrip(), path=path, line=start))
        term = None

    for number, raw in enumerate(text.splitlines(), start=1):
        match = _START.match(raw.strip())
        if match and _TAIL.match(match["tail"]) and _term(match["term"]):
            close()
            term, body, start = _term(match["term"]), [raw.rstrip()], number
        elif term and raw.strip() and not raw.lstrip().startswith("#"):
            body.append(raw.rstrip())
        else:
            close()
    close()
    return entries


def load(repo_root: Path) -> list[Entry]:
    """Every definition across the glossary files of *repo_root*."""
    entries: list[Entry] = []
    for path in context_files(repo_root):
        try:
            entries.extend(parse_entries(path.read_text(encoding="utf-8", errors="replace"), path))
        except OSError:
            continue
    return entries


def _key(term: str) -> str:
    """Compare terms by case, spacing and a trailing parenthesis alone."""
    return " ".join(re.sub(r"\s*\(.*\)\s*$", "", term).casefold().split())


def lookup(entries: list[Entry], query: str) -> list[Entry]:
    """The entries named *query*, ignoring case. A plural finds its singular."""
    key = _key(query)
    hits = [e for e in entries if _key(e.term) == key]
    if not hits and key.endswith("s"):
        hits = [e for e in entries if _key(e.term) == key[:-1]]
    return hits


def suggest(entries: list[Entry], query: str, limit: int = 5) -> list[str]:
    """Terms close to *query*: those that contain it, then near spellings."""
    key = _key(query)
    by_key = {_key(e.term): e.term for e in entries}
    near = [term for k, term in by_key.items() if key and key in k]
    near += [by_key[k] for k in difflib.get_close_matches(key, list(by_key), n=limit, cutoff=0.6)]
    return list(dict.fromkeys(near))[:limit]


def index_text(
    entries: list[Entry], repo_root: Path, budget: int = INDEX_BUDGET_CHARS
) -> str | None:
    """The SessionStart block that lists every term, or None without a glossary."""
    if not entries:
        return None
    files = sorted({str(e.path.relative_to(repo_root.resolve())) for e in entries})
    head = (
        f"## Glossary ({', '.join(files)})\n"
        "These words have one fixed meaning in this repo. Look a word up before you rely on "
        "its meaning or write it: `lore glossary <term> [<term> …]`.\n"
    )
    terms = list(dict.fromkeys(e.term for e in entries))
    listed: list[str] = []
    used = len(head)
    for term in terms:
        if used + len(term) + 2 > budget:
            break
        listed.append(term)
        used += len(term) + 2
    line = ", ".join(listed)
    rest = len(terms) - len(listed)
    if rest:
        line += f", … {rest} more: `lore glossary`"
    return head + line
