"""`lore init` — the single guided onboarding wizard.

From "binary installed" to "notes being written" in one idempotent,
resumable command:

    vault -> wiki -> integrations -> optional first attach ->
    automatic `lore doctor` -> handoff panel.

Re-running detects existing state, collapses completed steps to a skip
line, and thereby doubles as a repair path for a partial install. Full
non-interactive flag parity (`--vault`, `--wiki-new/-clone/-link`,
`--attach`, `--yes`, `--plain`) drives every step for CI and scripted
installs; `install.sh` chains into this wizard on first install.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import typer
from lore_core.config import get_lore_root, get_wiki_root, user_config_path
from rich.console import Console
from rich.panel import Panel

from lore_cli._argv_compat import argv_main

console = Console()

# Vault -> Wiki -> Integrations -> Attach -> Doctor -> Handoff.
TOTAL_STEPS = 6

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=False,
    rich_markup_mode="rich",
)


def _plugin_templates_dir() -> Path:
    """Backwards-compatible shim for `lore_core.templates.templates_dir`."""
    from lore_core.templates import templates_dir

    return templates_dir()


def _is_interactive() -> bool:
    return sys.stdin.isatty()


# ---------------------------------------------------------------------------
# Step chrome
# ---------------------------------------------------------------------------


def _step_header(n: int, title: str) -> None:
    console.print(f"\n[bold]Step {n}/{TOTAL_STEPS} · {title}[/bold]")


def _skip(msg: str) -> None:
    """A completed step collapses to one ✓ receipt line."""
    console.print(f"  [green]✓[/green] {msg} [dim](skipped)[/dim]")


def _done(msg: str) -> None:
    console.print(f"  [green]✓[/green] {msg}")


# ---------------------------------------------------------------------------
# Display name onboarding (unchanged; part of the vault step)
# ---------------------------------------------------------------------------


def _maybe_set_display_name(root: Path, display_name: str | None) -> None:
    """Onboard the personal display name used in place of `$USER`.

    A `--display-name` flag wins outright (scripted/CI use). Otherwise,
    prompt only when attached to a real terminal and nothing is
    configured yet — reruns (e.g. `--force`) never overwrite an
    existing choice, and non-interactive runs never block on input.
    """
    from lore_core.root_config import load_root_config, set_field

    if display_name:
        set_field(root, "user.display_name", display_name)
        console.print(f"[green]Display name set to {display_name!r}[/green]")
        return
    if load_root_config(root).user.display_name:
        return
    if not _is_interactive():
        return
    name = typer.prompt(
        "Display name (used in session notes and briefings; blank to skip)",
        default="",
        show_default=False,
    )
    if name:
        set_field(root, "user.display_name", name)
        console.print(f"[green]Display name set to {name!r}[/green]")


# ---------------------------------------------------------------------------
# Vault scaffold (the deterministic primitive the wizard's step 1 wraps)
# ---------------------------------------------------------------------------


def init_vault(root: Path, force: bool = False, display_name: str | None = None) -> None:
    """Create the canonical vault shape at `root`. Idempotent: existing
    CLAUDE.md / templates are left untouched unless `force`."""
    root.mkdir(parents=True, exist_ok=True)
    _maybe_set_display_name(root, display_name)

    for subdir in ("sessions", "inbox", "drafts", "wiki"):
        (root / subdir).mkdir(exist_ok=True)

    templates_src = _plugin_templates_dir()
    templates_dst = root / "templates"
    if templates_dst.exists() and not force:
        console.print(
            "[yellow]templates/ already exists — leaving untouched "
            "(use --force to overwrite).[/yellow]"
        )
    else:
        shutil.copytree(templates_src, templates_dst, dirs_exist_ok=True)
        console.print(f"[green]Copied templates/ from {templates_src}[/green]")

    claude_md = root / "CLAUDE.md"
    if claude_md.exists() and not force:
        console.print(
            "[yellow]CLAUDE.md already exists — leaving untouched "
            "(use --force to overwrite).[/yellow]"
        )
    else:
        claude_md.write_text((templates_src / "root-CLAUDE.md").read_text())
        console.print(f"[green]Wrote {claude_md}[/green]")


def _vault_complete(root: Path) -> bool:
    return (
        (root / "CLAUDE.md").exists() and (root / "templates").is_dir() and (root / "wiki").is_dir()
    )


# ---------------------------------------------------------------------------
# Vault resolution + persistence
# ---------------------------------------------------------------------------


def _prompt_vault(vault_flag: str | None, *, interactive: bool) -> Path:
    """Resolve the vault root the wizard will target.

    Precedence: explicit ``--vault`` flag > existing ``$LORE_ROOT`` /
    config (``get_lore_root`` already honours env then config) > the
    ``~/lore`` default. Only prompts when interactive and no flag.
    """
    if vault_flag:
        return Path(vault_flag).expanduser().resolve()
    default = get_lore_root()
    if interactive:
        raw = input(f"  Vault location [{default}]: ").strip()
        return Path(raw).expanduser().resolve() if raw else default
    return default


def _persist_vault(root: Path) -> None:
    """Commit the chosen vault as the resolved LORE_ROOT for this process
    and future ones.

    Sets ``os.environ['LORE_ROOT']`` so every downstream step (wiki
    scaffold, doctor, attach) resolves to the same vault unambiguously
    within this run, and writes ``~/.config/lore/config.yml`` so future
    ``lore`` invocations find it without an env export.
    """
    import yaml

    os.environ["LORE_ROOT"] = str(root)
    cfg = user_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(yaml.safe_dump({"lore_root": str(root)}, sort_keys=False))


# ---------------------------------------------------------------------------
# Step 1 · Vault
# ---------------------------------------------------------------------------


def _step_vault(root: Path, *, force: bool, display_name: str | None) -> None:
    _step_header(1, "Vault")
    _persist_vault(root)
    if _vault_complete(root) and not force:
        _skip(f"Vault ready at {root}")
        _maybe_set_display_name(root, display_name)
        return
    init_vault(root, force=force, display_name=display_name)
    _done(f"Vault initialised at {root}")


# ---------------------------------------------------------------------------
# Step 2 · Wiki (new personal / clone team remote / link existing dir)
# ---------------------------------------------------------------------------


def _existing_wikis(root: Path) -> list[str]:
    wr = root / "wiki"
    if not wr.is_dir():
        return []
    return sorted(d.name for d in wr.iterdir() if d.is_dir() or d.is_symlink())


def _clone_target_name(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")


def _clone_wiki(url: str) -> None:
    name = _clone_target_name(url)
    target = get_wiki_root() / name
    if target.exists() or target.is_symlink():
        _skip(f"Wiki {name!r} already present")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", url, str(target)], check=True)
    _done(f"Cloned team wiki {name!r}")


def _link_wiki(src_path: str) -> None:
    src = Path(src_path).expanduser().resolve()
    name = src.name
    target = get_wiki_root() / name
    if target.exists() or target.is_symlink():
        _skip(f"Wiki {name!r} already present")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(src)
    _done(f"Linked wiki {name!r} -> {src}")


def _create_personal_wiki(name: str) -> None:
    from lore_cli.wiki_cmd import scaffold_wiki

    scaffold_wiki(name, mode="personal")
    _done(f"Created personal wiki {name!r}")


def _step_wiki(
    root: Path,
    *,
    wiki_new: str | None,
    wiki_clone: str | None,
    wiki_link: str | None,
    interactive: bool,
) -> None:
    _step_header(2, "Wiki")
    existing = _existing_wikis(root)

    if wiki_clone:
        if _clone_target_name(wiki_clone) in existing:
            _skip(f"Wiki {_clone_target_name(wiki_clone)!r} already present")
        else:
            _clone_wiki(wiki_clone)
        return
    if wiki_link:
        _link_wiki(wiki_link)
        return
    if wiki_new:
        if wiki_new in existing:
            _skip(f"Wiki {wiki_new!r} already present")
        else:
            _create_personal_wiki(wiki_new)
        return

    # No explicit target flag.
    if existing:
        _skip(f"Wiki already present ({', '.join(existing)})")
        return

    if not interactive:
        _create_personal_wiki("personal")
        return

    # Interactive: new personal / clone remote / link existing dir.
    console.print("  How would you like to set up your first wiki?")
    console.print("    [n] New personal wiki")
    console.print("    [c] Clone a team wiki from a git remote")
    console.print("    [l] Link an existing directory")
    choice = input("  Choice [n]: ").strip().lower() or "n"
    if choice == "c":
        url = input("  Git remote URL: ").strip()
        if url:
            _clone_wiki(url)
    elif choice == "l":
        folder = input("  Directory path: ").strip()
        if folder:
            _link_wiki(folder)
    else:
        name = input("  Wiki name [personal]: ").strip() or "personal"
        _create_personal_wiki(name)


# ---------------------------------------------------------------------------
# Step 3 · Integrations (reuse the `lore install` plumbing)
# ---------------------------------------------------------------------------


def _step_integrations(*, plain: bool) -> None:
    _step_header(3, "Integrations")
    from lore_core.install import known_integrations

    from lore_cli import install_cmd

    present = [h for h in known_integrations() if install_cmd._integration_present(h)]
    if not present:
        _skip("No Claude Code or Cursor detected on PATH — re-run `lore init` after installing one")
        return
    argv = ["--yes"]
    if plain:
        argv.append("--quiet")
    rc = install_cmd.main(argv)
    if rc != 0:
        console.print(
            "  [yellow]⚠ Integration wiring reported issues — "
            "run [cyan]lore doctor[/cyan] to diagnose.[/yellow]"
        )
    if "claude" in present:
        console.print(
            "  [bold yellow]⚠ Restart Claude Code[/bold yellow] to load the "
            "refreshed plugin (hooks, skills, MCP server)."
        )


# ---------------------------------------------------------------------------
# Step 4 · Optional first attach
# ---------------------------------------------------------------------------


def _step_attach(root: Path, *, attach: bool, interactive: bool, cwd: Path) -> None:
    _step_header(4, "Attach")
    if not (cwd / ".git").is_dir():
        _skip("Current directory is not a git repo — no first attach")
        return

    if interactive:
        import contextlib

        from lore_cli import attach_cmd

        with contextlib.suppress(typer.Exit, KeyboardInterrupt, EOFError):
            attach_cmd._interactive_wizard(cwd, root)
        return

    # Non-interactive: honour --attach by auto-accepting a checked-in
    # offer if one exists; otherwise leave it to an explicit `lore attach`.
    if not attach:
        _skip("Skipping first attach (pass --attach, or run `lore attach` later)")
        return
    from lore_core.offer import find_lore_yml

    found = find_lore_yml(cwd)
    if found is None:
        _skip("No .lore.yml offer to auto-accept — run `lore attach` in this repo")
        return
    try:
        from lore_cli.attach_cmd import _do_accept

        _do_accept(root, cwd.resolve())
        _done(f"Attached {cwd}")
    except Exception as exc:  # noqa: BLE001 — attach is best-effort here
        console.print(f"  [yellow]Attach skipped: {type(exc).__name__}[/yellow]")


# ---------------------------------------------------------------------------
# Step 5 · Doctor (the wizard's exit code mirrors its verdict)
# ---------------------------------------------------------------------------


def _run_doctor(cwd: Path) -> int:
    """Run `lore doctor` in-process and return its exit code (0 ok / 1 fail).

    A thin seam over the doctor command so the wizard reuses the exact
    diagnostics rather than reimplementing checks — and so tests can
    substitute a deterministic verdict.
    """
    from lore_cli import doctor_cmd

    return doctor_cmd.main(["--cwd", str(cwd)])


def _step_doctor(cwd: Path) -> bool:
    _step_header(5, "Doctor")
    return _run_doctor(cwd) == 0


# ---------------------------------------------------------------------------
# Step 6 · Handoff
# ---------------------------------------------------------------------------


def _step_handoff(root: Path, *, doctor_ok: bool, plain: bool) -> None:
    _step_header(6, "Next steps")
    verdict = "[green]✓ passed[/green]" if doctor_ok else "[red]✗ failed — see above[/red]"
    body = "\n".join(
        [
            f"Vault:  {root}",
            f"Doctor: {verdict}",
            "",
            "1. Restart Claude Code to load the Lore plugin.",
            "2. Run [cyan]lore status[/cyan] to see capture liveness.",
            "3. In any git repo, run [cyan]lore attach[/cyan] to capture sessions.",
        ]
    )
    if plain:
        console.print(body)
    else:
        console.print(Panel(body, title="Lore is ready", expand=False))


# ---------------------------------------------------------------------------
# Wizard driver
# ---------------------------------------------------------------------------


def run_wizard(
    *,
    vault: str | None = None,
    wiki_new: str | None = None,
    wiki_clone: str | None = None,
    wiki_link: str | None = None,
    attach: bool = False,
    yes: bool = False,
    plain: bool = False,
    force: bool = False,
    display_name: str | None = None,
    cwd: str | Path | None = None,
) -> int:
    """Run the full onboarding wizard. Returns the doctor exit code."""
    wiki_targets = [t for t in (wiki_new, wiki_clone, wiki_link) if t]
    if len(wiki_targets) > 1:
        raise typer.BadParameter("Pass at most one of --wiki-new / --wiki-clone / --wiki-link.")

    cwd_path = Path(cwd) if cwd else Path(os.getcwd())
    interactive = _is_interactive() and not yes and not plain

    root = _prompt_vault(vault, interactive=interactive)
    _step_vault(root, force=force, display_name=display_name)
    _step_wiki(
        root,
        wiki_new=wiki_new,
        wiki_clone=wiki_clone,
        wiki_link=wiki_link,
        interactive=interactive,
    )
    _step_integrations(plain=plain)
    _step_attach(root, attach=attach, interactive=interactive, cwd=cwd_path)
    doctor_ok = _step_doctor(cwd_path)
    _step_handoff(root, doctor_ok=doctor_ok, plain=plain)
    return 0 if doctor_ok else 1


@app.callback(invoke_without_command=True)
def init(
    vault: str = typer.Option(
        None,
        "--vault",
        "--root",
        help="Vault location (default: $LORE_ROOT or ~/lore).",
    ),
    wiki_new: str = typer.Option(
        None, "--wiki-new", help="Scaffold a new personal wiki with this name."
    ),
    wiki_clone: str = typer.Option(
        None, "--wiki-clone", help="Clone a team wiki from this git remote URL."
    ),
    wiki_link: str = typer.Option(
        None, "--wiki-link", help="Link an existing directory as a wiki."
    ),
    attach: bool = typer.Option(
        False, "--attach", help="Attach the current git repo (auto-accepts a .lore.yml offer)."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Non-interactive; accept defaults for every step."
    ),
    plain: bool = typer.Option(
        False, "--plain", help="Degrade prompts to plain stdin; no Rich panels."
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite existing CLAUDE.md and templates/."
    ),
    display_name: str = typer.Option(
        None,
        "--display-name",
        help="Personal display name for session notes/briefings (skips the prompt).",
    ),
) -> None:
    """Run the unified onboarding wizard (idempotent and resumable)."""
    rc = run_wizard(
        vault=vault,
        wiki_new=wiki_new,
        wiki_clone=wiki_clone,
        wiki_link=wiki_link,
        attach=attach,
        yes=yes,
        plain=plain,
        force=force,
        display_name=display_name,
    )
    if rc != 0:
        raise typer.Exit(code=rc)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
