"""`lore new-wiki <name>` — scaffold a new wiki under $LORE_ROOT/wiki/."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from lore_core.config import get_wiki_root
from rich.console import Console

console = Console()

SUBDIRS = ("projects", "concepts", "decisions", "sessions", "inbox")


def _plugin_templates_dir() -> Path:
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent.parent / "templates",
        here.parent.parent / "templates",
    ]
    for c in candidates:
        if (c / "wiki-CLAUDE.md").exists():
            return c
    raise FileNotFoundError("Could not locate plugin templates/.")


def scaffold_wiki(
    name: str,
    *,
    mode: str = "personal",
    remote: str | None = None,
    force: bool = False,
) -> Path:
    wiki_root = get_wiki_root()
    wiki_root.mkdir(parents=True, exist_ok=True)
    target = wiki_root / name

    if target.exists() and not force:
        raise FileExistsError(
            f"wiki/{name} already exists — pick another name or --force."
        )

    target.mkdir(exist_ok=True)
    for sub in SUBDIRS:
        (target / sub).mkdir(exist_ok=True)

    templates_src = _plugin_templates_dir()
    # Copy wiki CLAUDE.md
    claude_md = target / "CLAUDE.md"
    claude_md.write_text((templates_src / "wiki-CLAUDE.md").read_text())
    # Copy the session template so /lore:session can find it
    (target / "templates").mkdir(exist_ok=True)
    shutil.copy(templates_src / "session.md", target / "templates" / "session.md")

    # Seed _index.md so SessionStart doesn't warn on missing catalog
    (target / "_index.md").write_text(
        f"# {name.upper()} Knowledge Index\n\n"
        f"(Newly created wiki — run `lore lint --wiki {name}` to populate.)\n"
    )

    if mode == "team":
        subprocess.run(["git", "init"], cwd=str(target), check=False)
        if remote:
            subprocess.run(
                ["git", "remote", "add", "origin", remote],
                cwd=str(target),
                check=False,
            )
        subprocess.run(["git", "add", "-A"], cwd=str(target), check=False)
        subprocess.run(
            ["git", "commit", "-m", "lore: initial wiki scaffold"],
            cwd=str(target),
            check=False,
        )

    console.print(f"[green]Created {target}[/green]")
    console.print(f"Next: run [cyan]lore lint --wiki {name}[/cyan] to regenerate catalogs.")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lore-new-wiki")
    parser.add_argument("name", help="Wiki name (kebab-case)")
    parser.add_argument(
        "--mode",
        choices=["personal", "team"],
        default="personal",
        help="`team` mode adds git init + optional remote",
    )
    parser.add_argument(
        "--remote", help="Git remote URL (team mode)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing wiki directory",
    )
    args = parser.parse_args(argv)
    try:
        scaffold_wiki(args.name, mode=args.mode, remote=args.remote, force=args.force)
    except FileExistsError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
