"""Backwards-compat shim for the typer migration.

Existing tests and the `__main__.py` dispatcher both call
`<cmd>.main(argv: list[str] | None) -> int`. Typer apps don't have
that signature natively — they expose a `__call__` that exits the
process. This helper wraps a Typer app into the legacy `main(argv)`
contract so the migration can land file-by-file without rewriting
hundreds of test calls.

Usage in each migrated `<cmd>.py`:

    import typer
    from lore_runtime.argv import argv_main

    app = typer.Typer(...)

    @app.command()
    def something(...): ...

    main = argv_main(app)   # legacy entry point for tests + dispatcher

Tests keep calling `cmd.main(["sub", "--flag"])`; typer handles the
parsing, the wrapper translates exceptions back to int exit codes.
"""

from __future__ import annotations

from collections.abc import Callable

import click
import typer


def argv_main(app: typer.Typer) -> Callable[[list[str] | None], int]:
    """Return a legacy `main(argv) -> int` wrapper around a Typer app.

    Catches the SystemExit / typer.Exit raised by typer when
    `standalone_mode=True` (the default) and translates back to an
    int exit code. argparse compatibility — tests don't have to know
    a typer app is underneath.
    """

    def _main(argv: list[str] | None = None) -> int:
        try:
            # standalone_mode=False makes click/typer RETURN the exit
            # code instead of calling sys.exit(). typer.Exit raised
            # inside a command becomes the returned int. SystemExit
            # is still raised for argparse-style errors (--help,
            # unknown arg).
            result = app(args=argv, standalone_mode=False)
            if isinstance(result, int):
                return result
            return 0
        except click.exceptions.ClickException as e:
            e.show()
            return e.exit_code
        except click.exceptions.Abort:
            import sys as _sys

            print("Aborted.", file=_sys.stderr)
            return 130
        except typer.Exit as e:
            return int(e.exit_code or 0)
        except SystemExit as e:
            code = e.code
            if code is None:
                return 0
            if isinstance(code, int):
                return code
            # Python convention: str code → print to stderr, exit 1
            if isinstance(code, str):
                import sys as _sys

                print(code, file=_sys.stderr)
                return 1
            return 1

    return _main
