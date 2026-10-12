"""Internal compat shim — wraps a `typer.Typer` app into the legacy
`main(argv: list[str] | None) -> int` contract.

Tests and the `__main__.py` dispatcher both call
`<cmd>.main(argv: list[str] | None) -> int`. Typer apps don't have
that signature natively — they expose a `__call__` that exits the
process. This helper translates exceptions back to int exit codes so
existing call sites don't have to know a typer app is underneath.

Usage in each `lore_cli/<verb>_cmd.py`:

    import typer
    from lore_cli._argv_compat import argv_main

    app = typer.Typer(...)

    @app.command()
    def something(...): ...

    main = argv_main(app)   # legacy entry point for tests + dispatcher
"""

from __future__ import annotations

from collections.abc import Callable

import click
import typer

try:  # typer >= 0.27 raises its own copy of click's exceptions
    from typer._click.exceptions import ClickException as _TyperClickException
except ImportError:
    _TyperClickException = click.exceptions.ClickException

# Catch these, not the bare click classes: a usage error from either copy
# must print its message and exit 2, never reach the crash backstop.
CLICK_ERRORS = (click.exceptions.ClickException, _TyperClickException)
ABORTS = (click.exceptions.Abort, typer.Abort)


def argv_main(app: typer.Typer) -> Callable[[list[str] | None], int]:
    """Return a legacy `main(argv) -> int` wrapper around a Typer app.

    Catches the SystemExit / typer.Exit raised by typer when
    `standalone_mode=True` (the default) and translates back to an
    int exit code.
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
        except CLICK_ERRORS as e:
            e.show()
            return e.exit_code
        except ABORTS:
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
