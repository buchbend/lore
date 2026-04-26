"""`lore completions` — print shell completion scripts."""

from __future__ import annotations


import typer

app = typer.Typer(
    add_completion=False,
    help="Print shell completion scripts for lore.",
    no_args_is_help=True,
)


def _emit(shell_name: str) -> None:
    """Print the completion script for *shell_name* to stdout."""
    try:
        from click.shell_completion import get_completion_class
        cls = get_completion_class(shell_name)
        if cls is None:
            typer.echo(f"unsupported shell: {shell_name}", err=True)
            raise typer.Exit(code=1)
        # Import lazily to avoid circular imports at module load time.
        from lore_cli.__main__ import app as _root_app
        complete = cls(typer.main.get_command(_root_app), {}, "lore", "_LORE_COMPLETE")
        typer.echo(complete.source())
    except ImportError:
        # click < 8.1 fallback — emit a minimal bash completion.
        if shell_name == "bash":
            typer.echo(_fallback_bash())
        else:
            typer.echo(
                f"# Shell completion for {shell_name} requires click >= 8.1",
                err=True,
            )
            raise typer.Exit(code=1)


def _fallback_bash() -> str:
    return """\
# lore bash completion (minimal fallback)
_lore_complete() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    COMPREPLY=( $(COMP_WORDS="${COMP_WORDS[*]}" COMP_CWORD=$COMP_CWORD _LORE_COMPLETE=bash_complete lore 2>/dev/null) )
}
complete -o default -F _lore_complete lore
"""


@app.command("bash")
def bash() -> None:
    """Print a bash completion script.

    Install with: source <(lore completions bash)
    Or permanently: lore completions bash >> ~/.bashrc
    """
    _emit("bash")


@app.command("zsh")
def zsh() -> None:
    """Print a zsh completion script.

    Install with: lore completions zsh > ~/.zsh/_lore
    """
    _emit("zsh")


@app.command("fish")
def fish() -> None:
    """Print a fish completion script.

    Install with: lore completions fish > ~/.config/fish/completions/lore.fish
    """
    _emit("fish")
