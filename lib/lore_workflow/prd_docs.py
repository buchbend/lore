#!/usr/bin/env python3
"""PRD-file mechanics for the to-epic skill (sub-issue #4).

The `to-epic` skill body is LLM-driven prose, but the part that turns the
condensed PRD into a *file* under `docs/prd/` and wires it into the docs toctree
must be deterministic so it can be tested and so two runs agree. That mechanic
lives here as a small, self-contained helper: no third-party dependencies
(stdlib only, so it runs in CI without an install step) and no GitHub I/O — the
skill supplies the epic URL and involved-repo list it already knows.

What it guarantees, matching CONVENTIONS.md and the scaffold layout
(`scripts/ccat_workflow_init.py`):

  - the PRD is a file at `docs/prd/NNNN-kebab.md` (zero-padded sequence per
    directory), MyST Markdown with YAML front-matter that links its epic and
    lists every involved repo — the PRD is the source of truth, the GitHub epic
    merely links it;
  - the PRD is wired into `docs/prd/index.md`'s first `{toctree}` block,
    idempotently, using the single-brace MyST directive form the scaffold uses;
  - an absent `docs/prd/index.md` is created with the same toctree stub the
    scaffold writes, so a repo that never ran the scaffold is still wired.

The toctree-wiring approach mirrors `scripts/ccat_workflow_init.py` (single
source of truth for the convention) but is reimplemented locally so this helper
stays self-contained and the scaffold script is never imported or modified.
"""

from __future__ import annotations

import re
from pathlib import Path

# Single-brace MyST toctree stub, matching scripts/ccat_workflow_init.py so a
# PRD index this helper creates is indistinguishable from a scaffolded one.
_PRD_INDEX_STUB = """\
# Product Requirements Documents

PRDs are the source of truth for decisions. Each PRD lives at
`docs/prd/NNNN-kebab.md` (MyST Markdown) and is linked from its GitHub epic.

```{toctree}
:maxdepth: 1

```
"""

# Matches a PRD's leading zero-padded sequence: 0001-foo(.md) → 1.
_SEQ_RE = re.compile(r"(\d{4})-")


def next_sequence(prd_dir: Path) -> int:
    """Return the next zero-padded PRD number for *prd_dir* (1 if empty).

    Counts both PRD files on disk and `NNNN-` toctree entries already wired into
    `index.md`, so the next number never collides with a PRD that is referenced
    in the index but whose file has not yet been written.
    """
    highest = 0
    if prd_dir.is_dir():
        for child in prd_dir.glob("[0-9][0-9][0-9][0-9]-*.md"):
            match = _SEQ_RE.match(child.name)
            if match:
                highest = max(highest, int(match.group(1)))
    index_path = prd_dir / "index.md"
    if index_path.exists():
        for match in _SEQ_RE.finditer(index_path.read_text(encoding="utf-8")):
            highest = max(highest, int(match.group(1)))
    return highest + 1


def _frontmatter(title: str, epic_url: str, repos: list[str]) -> str:
    """Build the PRD's YAML front-matter block.

    Carries the epic link (the back-reference that makes the PRD reachable from
    its tracker) and the involved-repos list (a cross-repo epic touches more
    than one), so the PRD front-matter alone documents what the epic spans.
    """
    repo_lines = "\n".join(f"  - {repo}" for repo in repos)
    return (
        "---\n"
        f"title: {title}\n"
        "status: draft\n"
        f"epic: {epic_url}\n"
        "repos:\n"
        f"{repo_lines}\n"
        "---\n"
    )


def _body(seq: int, title: str, epic_url: str) -> str:
    """Build the PRD body skeleton (the condensed-PRD sections to-epic fills)."""
    return (
        f"# PRD {seq:04d}: {title}\n"
        "\n"
        f"> Source of truth for this epic. Tracker: [epic issue]({epic_url}).\n"
        "> The epic links here; this file is not embedded in the issue body.\n"
        "\n"
        "## Problem\n"
        "The problem, from the user's perspective.\n"
        "\n"
        "## Solution\n"
        "The solution, from the user's perspective.\n"
        "\n"
        "## Implementation decisions\n"
        "Modules to build/modify and their interfaces, schema/API contracts,\n"
        "architectural decisions.\n"
        "\n"
        "## Testing decisions\n"
        "What makes a good test here, which modules are tested, prior art.\n"
        "\n"
        "## Out of scope\n"
        "What this epic deliberately does not cover.\n"
    )


def wire_toctree(index_path: Path, entry: str) -> None:
    """Add *entry* into *index_path*'s first toctree block, idempotently.

    Creates the index from the stub when absent, then inserts the entry before
    the closing fence of the first `{toctree}` block. Does nothing if the entry
    is already present anywhere in the file. Mirrors the wiring contract in
    scripts/ccat_workflow_init.py (single-brace MyST directive).
    """
    if not index_path.exists():
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(_PRD_INDEX_STUB, encoding="utf-8")

    text = index_path.read_text(encoding="utf-8")
    if entry in text:
        return  # Already present; idempotent.

    lines = text.splitlines(keepends=True)
    in_toctree = False
    insert_at = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not in_toctree and (
            "```{toctree}" in stripped or "``` {toctree}" in stripped
        ):
            in_toctree = True
            continue
        if in_toctree and stripped == "```":
            insert_at = i  # Closing fence: insert entry before it.
            break

    if insert_at is not None:
        lines.insert(insert_at, entry + "\n")
        index_path.write_text("".join(lines), encoding="utf-8")


def create_prd(
    target: Path,
    *,
    slug: str,
    title: str,
    epic_url: str,
    repos: list[str],
) -> Path:
    """Create docs/prd/NNNN-<slug>.md and wire it into docs/prd/index.md.

    *target* is the repo root. *slug* is the kebab-case PRD slug; the helper
    prefixes the next zero-padded sequence number for that repo. *epic_url* and
    *repos* go into the PRD front-matter (epic back-link + involved repos).

    Returns the path to the created PRD file. Never overwrites an existing PRD
    at the resolved path — the sequence number guarantees a fresh name.
    """
    prd_dir = Path(target) / "docs" / "prd"
    prd_dir.mkdir(parents=True, exist_ok=True)

    seq = next_sequence(prd_dir)
    name = f"{seq:04d}-{slug}"
    prd_path = prd_dir / f"{name}.md"

    content = _frontmatter(title, epic_url, repos) + "\n" + _body(seq, title, epic_url)
    prd_path.write_text(content, encoding="utf-8")

    wire_toctree(prd_dir / "index.md", name)
    return prd_path
