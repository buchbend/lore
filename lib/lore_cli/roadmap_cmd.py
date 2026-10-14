"""`lore roadmap add` — file an idea under "Not placed yet" (PRD 0018)."""

from __future__ import annotations

import re
from pathlib import Path

import typer
from lore_core.io import atomic_write_text
from lore_core.route import _repo_root

app = typer.Typer(
    add_completion=False,
    help="The roadmap file: add an idea under 'Not placed yet'.",
    no_args_is_help=True,
)

_SKELETON = "# Roadmap\n\nOne paragraph: where the code is heading.\n"
_HEADING = "## Not placed yet"


def _find(cwd: Path) -> tuple[Path, bool]:
    """Nearest ROADMAP.md from cwd up to the repo root; else the root's. Raises ValueError."""
    chain = [cwd, *cwd.parents]
    root = _repo_root(cwd)
    if root is None:
        raise ValueError("not inside a git repository")
    for d in chain[: chain.index(root) + 1]:
        if (d / "ROADMAP.md").is_file():
            return d / "ROADMAP.md", True
    return root / "ROADMAP.md", False


def _insert(text: str, item: str) -> str:
    lines = text.rstrip("\n").split("\n") if text.strip() else []
    start = next((i for i, ln in enumerate(lines) if ln.strip() == _HEADING), None)
    if start is None:
        return "\n".join([*lines, "", _HEADING, "", item]) + "\n"
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"## ", lines[i])), len(lines))
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    if end == start + 1:  # empty section
        lines[end:end] = ["", item]
    else:
        lines.insert(end, item)
    if end < len(lines) - 1 and lines[end + 1].startswith("## "):
        lines.insert(end + 1, "")
    return "\n".join(lines) + "\n"


@app.command("add")
def add_cmd(
    title: str = typer.Argument(..., help="The item title."),
    paragraph: str = typer.Argument("", help="What and why, in one paragraph."),
) -> None:
    """Append `- **Title.** Text` under `## Not placed yet`. Touches the working tree only."""
    try:
        path, exists = _find(Path.cwd().resolve())
    except ValueError as exc:
        typer.echo(f"roadmap add: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    title = title.strip().removesuffix(".")
    if not title:
        typer.echo("roadmap add: the title is empty", err=True)
        raise typer.Exit(code=1)
    item = f"- **{title}.**" + (f" {paragraph}" if paragraph else "")
    text = path.read_text(encoding="utf-8") if exists else _SKELETON
    atomic_write_text(path, _insert(text, item))
    typer.echo(f"roadmap add: wrote {path}")
