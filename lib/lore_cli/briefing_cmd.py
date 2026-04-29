"""`lore briefing` — gather, publish, and ledger-mark briefings.

The default form is the one-shot pipeline:

    lore briefing --wiki <name>

which gathers new sessions, renders a deterministic markdown digest,
publishes via the wiki's configured sink (`.lore-briefing.yml`), and
marks the ledger.

Power-user subcommands (used by `/lore:briefing` and scripted flows):

  lore briefing gather --wiki <name>   read-only: returns new sessions
                                        + sink config + ledger state as
                                        a JSON envelope
  lore briefing publish --sink <uri>   publish stdin/--file via the
                                        named sink (markdown:<path>,
                                        matrix, …)
  lore briefing mark --wiki <name>     update the ledger
    --session <path> [...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from lore_cli._argv_compat import argv_main
from lore_core.briefing import (
    SinkConfigMismatchError,
    UnknownSinkError,
    compose_briefing_prose,
    dispatch,
    gather,
    mark_incorporated,
    registered_sinks,
    render_briefing,
)
from lore_core.config import get_lore_root, get_wiki_root
from lore_core.wiki_config import load_wiki_config

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=False,
    rich_markup_mode="rich",
)


def _emit_json(envelope: dict) -> None:
    print(json.dumps(envelope, indent=2, default=str))


def _load_briefing_yaml(wiki: str) -> dict:
    """Load ``<wiki_root>/<wiki>/.lore-briefing.yml`` or raise typer.Exit."""
    import yaml

    wiki_path = get_wiki_root() / wiki
    if not wiki_path.exists():
        print(f"lore: wiki not found: {wiki}", file=sys.stderr)
        raise typer.Exit(code=1) from None
    cfg_path = wiki_path / ".lore-briefing.yml"
    if not cfg_path.exists():
        print(
            f"lore: no .lore-briefing.yml in wiki {wiki!r} "
            f"(expected at {cfg_path}).",
            file=sys.stderr,
        )
        raise typer.Exit(code=1) from None
    try:
        data = yaml.safe_load(cfg_path.read_text()) or {}
    except yaml.YAMLError as exc:
        print(f"lore: malformed yaml at {cfg_path}: {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from None
    if not isinstance(data, dict):
        print(
            f"lore: {cfg_path} top-level must be a mapping",
            file=sys.stderr,
        )
        raise typer.Exit(code=1) from None
    return data


def _try_compose_prose(
    *, wiki: str, gather_result: dict
) -> tuple[str, str | None]:
    """Try the LLM-prose path; return (text, fallback_reason).

    Returns ("", reason) on any failure so the caller can fall back to
    the deterministic render. Never raises — briefings always publish.
    """
    try:
        from lore_curator.llm_client import make_llm_client
    except Exception as exc:  # pragma: no cover — import error is pathological
        return "", f"llm_client import failed: {exc}"

    try:
        lore_root = get_lore_root()
        client = make_llm_client(lore_root=lore_root)
    except Exception as exc:
        return "", f"no LLM client ({exc})"

    if client is None:
        return "", "no LLM backend configured (auto-detect found nothing)"

    try:
        wiki_path = get_wiki_root() / wiki
        cfg = load_wiki_config(wiki_path)
    except Exception as exc:
        return "", f"wiki config load failed: {exc}"

    def model_resolver(t: str) -> str:
        return {
            "simple": cfg.models.simple,
            "middle": cfg.models.middle,
            "high": cfg.models.high,
        }[t]

    try:
        prose = compose_briefing_prose(
            gather_result=gather_result,
            llm_client=client,
            model_resolver=model_resolver,
        )
    except Exception as exc:
        return "", f"LLM call failed: {exc}"

    if not prose.strip():
        return "", "LLM returned empty output"
    return prose, None


def _run_oneshot(
    *,
    wiki: str,
    since: str | None,
    sink_override: str | None,
    dry_run: bool,
    no_mark: bool,
    no_llm: bool,
) -> int:
    """Gather + compose + publish + mark in one shot. Returns exit code."""
    result = gather(wiki=wiki, since=since, include_body_sections=True)
    if "error" in result:
        print(f"lore: {result.get('error', 'gather failed')}", file=sys.stderr)
        return 1

    sessions = result.get("new_sessions") or []
    if not sessions:
        print(
            f"lore: no new sessions for {wiki!r} since "
            f"{result.get('ledger', {}).get('last_briefing') or 'the start'}.",
            file=sys.stderr,
        )
        return 0

    if no_llm:
        text = render_briefing(result)
        print("lore: composing briefing deterministically (--no-llm)", file=sys.stderr)
    else:
        prose, reason = _try_compose_prose(wiki=wiki, gather_result=result)
        if prose:
            text = prose
            print("lore: composed briefing with LLM", file=sys.stderr)
        else:
            text = render_briefing(result)
            print(
                f"lore: LLM composition skipped ({reason}); "
                "publishing deterministic fallback",
                file=sys.stderr,
            )

    if dry_run:
        sys.stdout.write(text)
        return 0

    config = _load_briefing_yaml(wiki)
    if sink_override:
        uri = sink_override
    else:
        sink_name = config.get("sink")
        if not isinstance(sink_name, str) or not sink_name.strip():
            print(
                f"lore: no sink configured for {wiki!r} "
                "(set `sink:` in .lore-briefing.yml or pass --sink).",
                file=sys.stderr,
            )
            return 1
        uri = sink_name.strip()

    try:
        dispatch(uri, text, config)
    except UnknownSinkError as exc:
        print(
            f"lore: unknown sink '{exc}'. Known: {', '.join(registered_sinks())}",
            file=sys.stderr,
        )
        return 2
    except SinkConfigMismatchError as exc:
        print(f"lore: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — surfaced to user
        print(f"lore: sink failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"lore: published {len(sessions)} session(s) to {uri}",
        file=sys.stderr,
    )

    if no_mark:
        return 0

    session_paths = [s["path"] for s in sessions]
    mark_result = mark_incorporated(wiki=wiki, session_paths=session_paths)
    if "error" in mark_result:
        print(
            f"lore: published, but ledger update failed: "
            f"{mark_result.get('error')}",
            file=sys.stderr,
        )
        return 1
    return 0


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    wiki: str = typer.Option(
        None,
        "--wiki",
        help="Wiki name (required for the one-shot pipeline).",
    ),
    since: str = typer.Option(
        None,
        "--since",
        help="ISO date floor (YYYY-MM-DD). Defaults to ledger state.",
    ),
    sink: str = typer.Option(
        None,
        "--sink",
        help=(
            "Override sink URI. By default reads `sink:` from "
            ".lore-briefing.yml."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Render and print to stdout; do not publish or mark.",
    ),
    no_mark: bool = typer.Option(
        False,
        "--no-mark",
        help="Publish without updating the ledger.",
    ),
    no_llm: bool = typer.Option(
        False,
        "--no-llm",
        help=(
            "Skip the LLM composer and publish the deterministic "
            "bullet-list briefing."
        ),
    ),
) -> None:
    """One-shot: gather + compose + publish + mark for ``--wiki``."""
    if ctx.invoked_subcommand is not None:
        return
    if not wiki:
        print(ctx.get_help())
        raise typer.Exit(code=0)
    raise typer.Exit(
        code=_run_oneshot(
            wiki=wiki,
            since=since,
            sink_override=sink,
            dry_run=dry_run,
            no_mark=no_mark,
            no_llm=no_llm,
        )
    )


@app.command("gather")
def cmd_gather(
    wiki: str = typer.Option(..., "--wiki"),
    since: str = typer.Option(None, "--since", help="ISO date floor (YYYY-MM-DD)."),
    no_sections: bool = typer.Option(
        False,
        "--no-sections",
        help="Skip extracting body H2 sections (smaller payload).",
    ),
) -> None:
    """Read new sessions since the last briefing (JSON envelope)."""
    result = gather(wiki=wiki, since=since, include_body_sections=not no_sections)
    _emit_json({"schema": "lore.briefing.gather/1", "data": result})
    if "error" in result:
        raise typer.Exit(code=1)


@app.command("publish")
def cmd_publish(
    sink: str = typer.Option(
        ...,
        "--sink",
        help=(
            "Sink URI (scheme[:target]). Registered schemes: "
            f"{', '.join(registered_sinks())}. "
            "Examples: 'markdown:/tmp/briefing-YYYY-MM-DD.md', 'matrix'."
        ),
    ),
    wiki: str = typer.Option(
        None,
        "--wiki",
        help=(
            "Load <wiki>/.lore-briefing.yml and pass it to the sink. "
            "Without this flag, sinks fall back to env-var-only resolution."
        ),
    ),
    file: str = typer.Option(
        None, "--file", help="Briefing markdown (default: stdin)."
    ),
    out: str = typer.Option(
        None,
        "--out",
        help=(
            "Compatibility shim — appended to --sink as ':<out>'. "
            "Prefer the URI form."
        ),
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON envelope."),
) -> None:
    """Publish pre-composed markdown via the registered sink dispatcher."""
    if file:
        text = Path(file).read_text()
    elif sys.stdin.isatty():
        print(
            "lore: no input. Pipe markdown on stdin or pass --file. "
            "For one-shot publish, use `lore briefing --wiki <name>`.",
            file=sys.stderr,
        )
        raise typer.Exit(code=1)
    else:
        text = sys.stdin.read()
    if not text.strip():
        print("lore: nothing to publish (empty input)", file=sys.stderr)
        raise typer.Exit(code=1)

    uri = sink
    if out and ":" not in sink:
        uri = f"{sink}:{out}"

    config: dict | None = _load_briefing_yaml(wiki) if wiki else None

    try:
        dispatch(uri, text, config)
    except UnknownSinkError as exc:
        print(
            f"lore: unknown sink '{exc}'. Known: {', '.join(registered_sinks())}",
            file=sys.stderr,
        )
        raise typer.Exit(code=2) from None
    except SinkConfigMismatchError as exc:
        print(f"lore: {exc}", file=sys.stderr)
        raise typer.Exit(code=2) from None
    except Exception as exc:  # noqa: BLE001 — surfaced to user
        print(f"lore: sink failed: {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from None

    if json_out:
        _emit_json({"schema": "lore.briefing.publish/1", "data": {"sink": uri}})


@app.command("mark")
def cmd_mark(
    wiki: str = typer.Option(..., "--wiki"),
    session: list[str] = typer.Option(
        None,
        "--session",
        help="Session path or filename (repeatable).",
    ),
) -> None:
    """Append session(s) to the briefing ledger."""
    result = mark_incorporated(wiki=wiki, session_paths=session or [])
    _emit_json({"schema": "lore.briefing.mark/1", "data": result})
    if "error" in result:
        raise typer.Exit(code=1)


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
