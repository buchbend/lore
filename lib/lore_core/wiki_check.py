"""Wiki notes: find them and check them (issue #600).

`check_wiki` runs three structure checks and the writing rules:

- `broken_link`: a wikilink without a target in the same wiki.
- `missing_description`: no `description` field, or an empty one.
- `duplicate_filename`: two notes of one wiki share a filename.
- `vale`: each writing-rule finding of Vale, at Vale's level.

It checks no wiki structure: not the size of a note, not its folder, not its
link count. Lore writes no derived file into a wiki.

`discover_notes` is the walk of the search index: the four knowledge folders.
`wiki_notes` is the walk of the check: every folder outside the skip list.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from lore_core.config import get_wiki_root
from lore_core.schema import extract_wikilinks, parse_frontmatter
from lore_core.style import resolve_vale_config_path

KNOWLEDGE_DIRS = ["projects", "concepts", "decisions", "papers"]
SKIP_DIRS = {"templates", "inbox", ".processed", ".obsidian"}
SKIP_FILES = {
    "CLAUDE.md",
    "README.md",
    # Files older Lore versions generated. A wiki may still hold them.
    "_index.txt",
    "_catalog.json",
    "_recent.txt",
    "_concepts.txt",
    "_decisions.txt",
    "_threads.txt",
    "_recent.md",
    "_index.md",
    "llms.txt",
    "threads.md",
}

# `style/` holds the wiki's own writing rules, not notes.
_CHECK_SKIP_DIRS = SKIP_DIRS | {"style"}
# The retired-heading rule guards the issue skeleton. A wiki note has no skeleton.
_VALE_SKIPPED_RULES = {"WritingRules.RetiredHeading"}
VALE_ABSENT = "vale not on PATH: writing rules not checked"

_CODE_RE = re.compile(r"```.*?```|`[^`\n]+`", re.DOTALL)


@dataclass
class Finding:
    severity: str  # error, warning or suggestion
    wiki: str
    file: str  # relative to the wiki root
    check: str
    message: str
    line: int = 0  # 0: the finding names no line


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


def wiki_notes(wiki_path: Path) -> list[Path]:
    """Every `.md` note of a wiki, in every folder outside the skip list."""
    notes: list[Path] = []
    for root, dirs, files in os.walk(wiki_path):
        dirs[:] = sorted(d for d in dirs if d not in _CHECK_SKIP_DIRS and not d.startswith("."))
        notes += [
            Path(root) / name
            for name in sorted(files)
            if name.endswith(".md") and name not in SKIP_FILES
        ]
    return notes


def _aliases(fm: dict) -> list[str]:
    raw = fm.get("aliases")
    if isinstance(raw, str):
        raw = [raw]
    return [str(a).strip() for a in raw] if isinstance(raw, list) else []


def _structure_findings(wiki_path: Path, notes: list[Path]) -> list[Finding]:
    wiki = wiki_path.name
    by_name: dict[str, list[str]] = defaultdict(list)
    targets: set[str] = set()
    parsed: list[tuple[Path, str, dict, str]] = []
    for path in notes:
        text = path.read_text(errors="replace")
        fm = parse_frontmatter(text)
        rel = path.relative_to(wiki_path).as_posix()
        by_name[path.name].append(rel)
        targets.add(path.stem)
        targets.update(_aliases(fm))
        parsed.append((path, rel, fm, text))

    findings: list[Finding] = []
    for path, rel, fm, text in parsed:
        if not str(fm.get("description") or "").strip():
            findings.append(
                Finding("error", wiki, rel, "missing_description", "no `description` field")
            )
        others = [other for other in by_name[path.name] if other != rel]
        if others:
            message = f"filename `{path.name}` is also used by {', '.join(others)}"
            findings.append(Finding("error", wiki, rel, "duplicate_filename", message))
        for link in extract_wikilinks(_CODE_RE.sub("", text)):
            # `[[note#heading]]` and `[[folder/note]]` both name the note `note`.
            target = link.split("#")[0].strip().rsplit("/", 1)[-1]
            if target and target not in targets:
                message = f"[[{link}]] has no target in this wiki"
                findings.append(Finding("error", wiki, rel, "broken_link", message))
    return findings


def _vale_findings(wiki_path: Path, notes: list[Path]) -> tuple[list[Finding], str | None]:
    """Run Vale over `notes`. Returns the findings and a one-line note, if any."""
    if shutil.which("vale") is None:
        return [], VALE_ABSENT
    if not notes:
        return [], None
    rels = [n.relative_to(wiki_path).as_posix() for n in notes]
    config = resolve_vale_config_path(wiki_path)
    result = subprocess.run(
        ["vale", "--output=JSON", "--config", str(config), *rels],
        cwd=wiki_path,
        capture_output=True,
        text=True,
    )
    try:
        if result.returncode not in (0, 1):
            raise ValueError
        alerts = json.loads(result.stdout or "{}")
    except ValueError:
        detail = (result.stderr or result.stdout).strip().splitlines()[:1]
        return [], f"vale could not run: {' '.join(detail) or f'exit {result.returncode}'}"
    return [
        Finding(
            alert["Severity"],
            wiki_path.name,
            rel,
            "vale",
            f"{alert['Check']}: {alert['Message']}",
            alert["Line"],
        )
        for rel, file_alerts in sorted(alerts.items())
        for alert in file_alerts
        if alert["Check"] not in _VALE_SKIPPED_RULES
    ], None


def check_wiki(wiki_path: Path, only: str | None = None) -> tuple[list[Finding], str | None]:
    """Check one wiki, or the one note `only` (a path relative to the wiki root).

    Returns the findings and a one-line note where Vale did not run. One note
    is still checked against the whole wiki: a link target and a filename
    twin sit in other notes.
    """
    notes = wiki_notes(wiki_path)
    findings = _structure_findings(wiki_path, notes)
    if only is not None:
        findings = [f for f in findings if f.file == only]
        notes = [wiki_path / only]
    vale, note = _vale_findings(wiki_path, notes)
    return findings + vale, note
