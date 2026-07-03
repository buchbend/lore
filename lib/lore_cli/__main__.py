"""`lore` command — top-level typer dispatcher.

Each subcommand is implemented as its own typer app in a sibling
module; this file mounts them under a single root so `lore --help`
renders the full subcommand tree with Rich-styled boxes and each
`lore <verb> --help` works uniformly.

The full mount happens lazily inside `_build_app()` and is cached
module-globally on first access. The `lore hook <event>` fast path
in `main()` skips it entirely — hooks only need `lore_cli.hooks`,
not the ~30 sibling cmd modules whose eager import would cost
~240ms of cold-start time per hook fire.
"""

from __future__ import annotations

import sys

import click
import typer

_HOOK_TYPER_EVENT = {
    "session-start": "SessionStart",
    "pre-compact": "PreCompact",
    "stop": "Stop",
    "user-prompt-submit": "UserPromptSubmit",
    "session-end": "SessionEnd",
    "capture": "Capture",
}


def _detect_hook_event(argv: list[str]) -> str | None:
    """Return a Claude Code hook event label if argv is a hook invocation.

    Matches ``lore hook <event>``; ignores everything else. Used by main()
    to decide whether to emit a JSON envelope (for hook callers) or a
    plain stderr line (for human callers) when a top-level exception
    escapes.
    """
    if len(argv) >= 2 and argv[0] == "hook":
        return _HOOK_TYPER_EVENT.get(argv[1])
    return None


_app: typer.Typer | None = None


def _build_app() -> typer.Typer:
    """Construct the full lore CLI dispatcher.

    Cached via the module-level ``_app`` singleton: tests that do
    ``from lore_cli.__main__ import app`` go through ``__getattr__``
    which calls this once and reuses the result.
    """
    # Subcommand apps — every one of these is a typer.Typer instance with
    # its own commands / callback. Registering them via add_typer gives a
    # unified `lore --help` listing.
    from lore_cli import (
        attach_cmd,
        attachments_cmd,
        backfill_cmd,
        briefing_cmd,
        completions_cmd,
        config_cmd,
        curator_cmd,
        detach_cmd,
        doctor_cmd,
        drain_cmd,
        drill_cmd,
        hooks,
        inbox_cmd,
        ingest_cmd,
        init_cmd,
        journal_cmd,
        install_cmd,
        lint_cmd,
        log_cmd,
        mcp_cmd,
        migrate_cmd,
        news_cmd,
        off_cmd,
        on_cmd,
        proc_cmd,
        project_cmd,
        quarantine_cmd,
        registry_cmd,
        resume_cmd,
        runs_cmd,
        scopes_cmd,
        search_cmd,
        session_cmd,
        status_cmd,
        surface_cmd,
        transcripts_cmd,
        wiki_cmd,
    )

    app = typer.Typer(
        add_completion=False,
        help="lore — knowledge-graph tooling for AI-coding teams.",
        no_args_is_help=True,
        rich_markup_mode="rich",
    )

    # Mount subcommands grouped by audience. `rich_help_panel` controls the
    # section header in `lore --help`.
    _GS = "Getting Started"
    _KN = "Knowledge"
    _ADV = "Advanced"

    app.add_typer(install_cmd.app, name="install", rich_help_panel=_GS)
    app.add_typer(init_cmd.app, name="init", rich_help_panel=_GS)
    app.add_typer(attach_cmd.app, name="attach", rich_help_panel=_GS)
    app.add_typer(status_cmd.app, name="status", rich_help_panel=_GS)
    app.add_typer(doctor_cmd.app, name="doctor", rich_help_panel=_GS)
    app.add_typer(config_cmd.app, name="config", rich_help_panel=_GS)

    app.add_typer(search_cmd.app, name="search", rich_help_panel=_KN)
    app.add_typer(drill_cmd.app, name="drill", rich_help_panel=_KN)
    app.add_typer(session_cmd.app, name="session", rich_help_panel=_KN)
    app.add_typer(project_cmd.app, name="project", rich_help_panel=_KN)
    app.add_typer(surface_cmd.app, name="surface", rich_help_panel=_KN)
    app.add_typer(wiki_cmd.app, name="wiki", rich_help_panel=_KN)
    app.add_typer(news_cmd.app, name="news", rich_help_panel=_KN)
    app.add_typer(resume_cmd.app, name="resume", rich_help_panel=_KN)
    app.add_typer(lint_cmd.app, name="lint", rich_help_panel=_KN)
    app.add_typer(curator_cmd.app, name="curator", rich_help_panel=_KN)
    app.add_typer(on_cmd.app, name="on", rich_help_panel=_KN)
    app.add_typer(off_cmd.app, name="off", rich_help_panel=_KN)

    app.add_typer(backfill_cmd.app, name="backfill", rich_help_panel=_ADV)
    app.add_typer(attachments_cmd.app, name="attachments", rich_help_panel=_ADV)
    app.add_typer(briefing_cmd.app, name="briefing", rich_help_panel=_ADV)
    app.add_typer(completions_cmd.app, name="completions", rich_help_panel=_ADV)
    app.add_typer(detach_cmd.app, name="detach", rich_help_panel=_ADV)
    app.add_typer(drain_cmd.app, name="drain", rich_help_panel=_ADV)
    app.add_typer(hooks.hook_app, name="hook", rich_help_panel=_ADV)
    app.add_typer(inbox_cmd.app, name="inbox", rich_help_panel=_ADV)
    app.add_typer(journal_cmd.app, name="journal", rich_help_panel=_KN)
    app.add_typer(ingest_cmd.app, name="ingest", rich_help_panel=_ADV)
    app.add_typer(log_cmd.app, name="log", rich_help_panel=_ADV)
    app.add_typer(mcp_cmd.app, name="mcp", rich_help_panel=_ADV)
    app.add_typer(migrate_cmd.app, name="migrate", rich_help_panel=_ADV)
    app.add_typer(proc_cmd.app, name="proc", rich_help_panel=_ADV)
    app.add_typer(quarantine_cmd.app, name="quarantine", rich_help_panel=_ADV)
    app.add_typer(registry_cmd.app, name="registry", rich_help_panel=_ADV)
    app.add_typer(runs_cmd.app, name="runs", rich_help_panel=_ADV)
    app.add_typer(scopes_cmd.app, name="scopes", rich_help_panel=_ADV)
    app.add_typer(transcripts_cmd.app, name="transcripts", rich_help_panel=_ADV)

    app.command(
        "uninstall",
        help="Symmetric semantic remove (alias for `install uninstall`).",
    )(install_cmd.build_install_command(
        "uninstall",
        "Top-level `lore uninstall` — same flags as `lore install uninstall`.",
    ))

    return app


def __getattr__(name: str):
    """Lazy module-level ``app`` for ``from lore_cli.__main__ import app``.

    Tests and ``lore_cli.completions_cmd`` reach for ``app`` directly;
    serving it through PEP 562 ``__getattr__`` lets the ``lore hook ...``
    fast path in :func:`main` skip ``_build_app()`` (and the eager
    cmd-module import that comes with it) entirely.
    """
    if name == "app":
        global _app
        if _app is None:
            _app = _build_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main(argv: list[str] | None = None) -> int:
    """Entry point — `lore` and `python -m lore_cli`."""
    if argv is None:
        argv = sys.argv[1:]
    is_hook = len(argv) >= 1 and argv[0] == "hook"
    try:
        if is_hook:
            # Fast path — skip _build_app() and its ~30 cmd-module imports.
            # The hook subapp is self-contained inside lore_cli.hooks.
            from lore_cli import hooks as _hooks_mod
            result = _hooks_mod.hook_app(args=argv[1:], standalone_mode=False)
        else:
            global _app
            if _app is None:
                _app = _build_app()
            result = _app(args=argv, standalone_mode=False)
        if isinstance(result, int):
            return result
        return 0
    except click.exceptions.ClickException as e:
        e.show()
        return e.exit_code
    except click.exceptions.Abort:
        print("Aborted.", file=sys.stderr)
        return 130
    except typer.Exit as e:
        return int(e.exit_code or 0)
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        if isinstance(code, str):
            print(code, file=sys.stderr)
            return 1
        return 1
    except Exception as exc:  # noqa: BLE001 — top-level crash backstop
        # Backstop for failures that escape the per-hook shield: import
        # errors, typer parameter resolution, or anything raised before
        # cmd_session_start's body runs. Always persists the traceback
        # to disk so doctor can surface it; on hook calls, also emits
        # the friendly JSON envelope so Claude Code shows an actionable
        # banner instead of a Rich-rendered traceback.
        from lore_cli._crash_log import write_crash

        hook_event = _detect_hook_event(argv)
        log_path = write_crash(hook_event or "main", exc)
        if hook_event is not None:
            try:
                from lore_cli.hooks import _emit, _hook_failure_banner
                banner = _hook_failure_banner(hook_event, exc, log_path=log_path)
                plain = "--plain" in argv
                _emit(hook_event, banner, plain=plain)
            except Exception:  # noqa: BLE001 — last-ditch fallback
                sys.stderr.write(
                    f"lore {hook_event} hook crashed: {type(exc).__name__}: {exc}\n"
                )
            # Exit 0 so Claude Code doesn't render the error panel.
            return 0
        # Non-hook callers (humans at a terminal) get a one-liner +
        # the log path. The full traceback would be the more useful
        # thing here, but we already have the file — keep stderr terse.
        sys.stderr.write(f"lore: unexpected error: {type(exc).__name__}: {exc}\n")
        if log_path is not None:
            sys.stderr.write(f"lore: full traceback written to {log_path}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
