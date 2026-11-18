"""`lore brief` — the orient homework in one call (PRD 0024, code mode).

One object of four parts for an intent: the ranked context pack, the first
section of each ranked doc, a code-map excerpt of about 1000 tokens, and the
glossary entries of the domain words in the intent. No clock, no randomness:
the same intent on the same tree gives the same output.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from lore_core import glossary
from lore_core.codemap import query as codemap_query
from lore_core.context_pack import gather, intent_words
from lore_core.git import git_repo_root

#: About 1000 tokens at four characters a token.
CODEMAP_BUDGET_CHARS = 4000


def first_section(text: str) -> str:
    """The first H2 section of a Markdown doc, heading included; "" without one."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("## ")]
    if not starts:
        return ""
    end = starts[1] if len(starts) > 1 else len(lines)
    return "\n".join(lines[starts[0] : end]).strip()


def _codemap_lines(repo_root: Path, words: set[str]) -> list[str]:
    """Symbols that name an intent word first, then the most-referenced ones."""
    hits = []
    if words:
        pattern = "(?i)" + "|".join(sorted(re.escape(w) for w in words))
        hits = codemap_query.query_symbols(repo_root, pattern, limit=200)["symbols"]
    hits += codemap_query.query_top(repo_root, limit=200)["symbols"]
    lines: list[str] = []
    used = 0
    for s in hits:
        line = f"{s['path']}:{s['line']} {s['qualname']}"
        if line in lines:
            continue
        if used + len(line) + 1 > CODEMAP_BUDGET_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    return lines


def _glossary(repo_root: Path, intent: str) -> list[dict[str, Any]]:
    """Entries whose term (or its plural) appears in the intent as whole words."""
    text = intent.casefold()
    out = []
    for entry in glossary.load(repo_root):
        term = re.sub(r"\s*\(.*\)\s*$", "", entry.term).casefold()
        if term and re.search(rf"\b{re.escape(term)}s?\b", text):
            out.append({"term": entry.term, "text": entry.text})
    return out


def brief(intent: str, cwd: Path | str | None = None) -> dict[str, Any]:
    """The four-part brief for *intent*; a cold start returns empty parts."""
    cwd_path = Path(cwd).expanduser().resolve() if cwd else Path.cwd()
    repo_root = git_repo_root(cwd_path)
    pack = gather(cwd=cwd_path, repo_path=str(repo_root) if repo_root else None, intent=intent)
    sections: dict[str, str] = {}
    codemap: list[str] = []
    terms: list[dict[str, Any]] = []
    if repo_root is not None:
        for path in pack["ranked"]:
            sections[path] = first_section((repo_root / path).read_text(errors="replace"))
        codemap = _codemap_lines(repo_root, intent_words(intent))
        terms = _glossary(repo_root, intent)
    return {
        "schema": "lore.brief/1",
        "intent": intent,
        "pack": pack,
        "sections": sections,
        "codemap": codemap,
        "glossary": terms,
    }
