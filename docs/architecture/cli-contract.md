# Lore CLI Contract

**Audience:** contributors adding a new `lore <verb>` command, or
moving existing CLI plumbing.

The `lore` CLI is a typer dispatcher (`lore_cli/__main__.py`) that
mounts one subapp per verb. Every verb lives in its own file and
follows the same shape, so the dispatcher stays a pure mounter and the
subpackages (`lore_core`, `lore_curator`, `lore_mcp`, `lore_search`)
stay free of CLI plumbing.

## The shape

Every CLI verb lives in `lib/lore_cli/<verb>_cmd.py` with this
skeleton:

```python
"""`lore <verb>` — one-line summary."""

from __future__ import annotations

import sys

import typer

from lore_cli._argv_compat import argv_main
from lore_<package>.<module> import <business_logic_function>

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def <verb>(
    foo: str = typer.Option(None, "--foo", help="…"),
) -> None:
    """One-paragraph help shown by `lore <verb> --help`."""
    result = <business_logic_function>(foo=foo)
    if result.errors:
        raise typer.Exit(code=1)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
```

Mount it in `lore_cli/__main__.py`:

```python
from lore_cli import (
    …,
    <verb>_cmd,
    …,
)

app.add_typer(<verb>_cmd.app, name="<verb>", rich_help_panel=_KN)
```

That's it. No new entry points in `pyproject.toml`, no new package.

## The rules

These are enforced by `tests/test_layering.py` and
`tests/test_cli_contract.py` — the build fails fast if any of them
break.

**1. Lower layers don't import from `lore_cli`.**
`lore_core`, `lore_curator`, `lore_mcp`, `lore_search`, and
`lore_adapters` must not import anything from `lore_cli` —
unconditionally, lazily, or under `TYPE_CHECKING`. They contain only
business logic. Their public functions are what the CLI verbs call.

**2. Every `<verb>_cmd.py` exposes a module-level `app: typer.Typer`.**
Verbs are addressable by the dispatcher and the test harness through
this single attribute name. Define it at module top level, not inside
a function.

**3. `lore_cli/__main__.py` only mounts.**
No `@app.command(...)` or `@app.callback(...)` decorators in
`__main__.py`. Verbs live in their own files; `__main__.py` only ties
them together with `app.add_typer(...)`. The single grandfathered
exception is the `cmd_uninstall_alias` function — a documented
symmetric alias for `lore install uninstall`.

**4. Import business logic, don't reimplement it.**
The verb file contains only argument parsing, output formatting, and
exit-code translation. Anything algorithmic or stateful belongs in a
lower-layer module. If you find yourself writing a real function in
`<verb>_cmd.py`, that function should live in `lore_<package>` and be
imported.

**5. Only the root app enables shell completion.**
The `<verb>_cmd.py` skeleton above sets `add_completion=False`, and every
sub-app keeps it that way — a sub-app that offered `--install-completion`
would install a competing script for the same `lore` binary. The root
`typer.Typer` in `__main__.py` sets `add_completion=True`, and that single
flag is the entire shell-completion surface: it completes the whole command
tree, sub-verbs included. Flipping it off silently removes completion for
users with no error anywhere — do not copy the sub-app template's value onto
the root.

## The seam helpers

Two small helpers smooth the boundary between typer and the test
harness:

- **`lore_cli._argv_compat.argv_main(app)`** — wraps a `typer.Typer`
  into the legacy `main(argv: list[str] | None) -> int` contract that
  tests expect. Translates `typer.Exit` / `SystemExit` /
  `click.exceptions.*` back to integer exit codes.

- **`lore_core.run_render`** — pure renderers (no I/O) for run-log
  records. Used by `curator_cmd` for the live trail during a curator
  run.

Both are import-time-cheap; they exist so individual verbs don't have
to repeat plumbing.

## Deprecating a verb

The pattern behind `lore log` / `lore news` / `lore runs` / `lore proc`
(#195 — absorbed by `lore trace` / `lore status`, see
`docs/architecture/observability.md`): keep the verb's `<verb>_cmd.py`
and its `app` exactly as they are — don't delete or rewrite the
behavior — and add one line at the top of the callback (or an
`@app.callback()` for a multi-command app that doesn't already have
one) that prints a pointer to the replacement on **stderr**:

```python
err_console = Console(stderr=True)  # separate from the stdout `console`

@app.callback(invoke_without_command=True)
def <verb>(...) -> None:
    err_console.print(
        "[yellow]lore <verb> is deprecated — use `lore <replacement>` "
        "instead. This alias will be removed in a future release.[/yellow]",
        highlight=False,
    )
    ...  # original behavior, unchanged
```

Stderr (never stdout) keeps `--json`/piped output script-safe. Record
the introduction and the planned removal version in `CHANGELOG.md`'s
`### Deprecated` section — that's the one place the exact version number
lives; the runtime message stays version-agnostic so it can't go stale.
A verb with no replacement to point at (`lore drain prune`, once its
one job — orphan-row pruning — was already running automatically inside
the retention janitor) is removed outright instead of aliased; its
CLI-owned business logic, if any is still needed by a lower layer,
moves into `lore_core` rather than surviving as a phantom `app`.

## When to break the rules

**Adding a new file that isn't `<verb>_cmd.py`.** `hooks.py` is the
single grandfathered exception — it pairs SessionStart-hook callbacks
with a small `hook_app` typer subapp. If you have a strong reason to
deviate from the convention (the CLI verb is one tiny aspect of a
broader module), document the exception in
`tests/test_cli_contract.py` next to `ALLOWED_INLINE_HANDLERS`.

**Putting helpers next to the verb.** Pure CLI helpers (output
formatting, console wrappers, `_print_summary`) can live in the same
`<verb>_cmd.py` file. If a helper is only ever called from one verb
and isn't business logic, keep it close. If a test imports it
directly, move it back to a lower layer.

## History

- **Substrate trim** — the `lore log` / `lore news` / `lore runs` /
  `lore proc` deprecation aliases below were removed outright once their
  one-release grace window closed; `lore trace` / `lore status` are now
  the only entry points for that debugging role.
- **#195** — `lore log` / `lore news` / `lore runs` / `lore proc` became
  deprecated thin aliases for `lore trace` / `lore status` (see
  "Deprecating a verb" above); `lore drain` (and its sole subcommand,
  `prune`) was removed outright — the janitor already ran it
  automatically, so the CLI surface was redundant, not deprecated.
- **v0.13.0** — typer apps lifted out of `lore_core/lint`,
  `lore_core/migrate`, `lore_curator/defrag_curator`, `lore_mcp/server`,
  `lore_search/cli`. The `lore_runtime` package was deleted; its
  `argv_main` helper moved to `lore_cli/_argv_compat`, and the
  `run_render` module moved to `lore_core/run_render`.
- **Pre-0.13.0** — typer apps lived in lower layers and were mounted
  via cross-package import (`from lore_core import lint as lint_cmd`).
  `lore_runtime` existed solely to give those apps somewhere to import
  argv-translation helpers from without inverting the dependency graph.
