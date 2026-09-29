"""`lore workflow` — deterministic epic-workflow substrate (PRD 0003).

Thin Typer wrapper over `lore_workflow`: skills that used to embed this
mechanic as prose now call these subcommands and gate on their exit code.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from lore_workflow.board_parser import BoardParseError, parse_board
from lore_workflow.epic_policy import resolve_epic_policy
from lore_workflow.ledger import (
    KINDS,
    LedgerEntry,
    LedgerParseError,
    append_entry,
    archive_ledger,
    default_ledger_path,
    format_entry,
    open_entries,
    parse_board_ledger,
    parse_ledger_source,
    set_board_outcome,
    set_outcome,
    utc_now,
)
from lore_workflow.prd_docs import PrdShipError, create_prd, ship_prd
from lore_workflow.risk import (
    RiskInputError,
    assess,
    diff_for_pr,
    diff_for_range,
    parse_unified_diff,
)
from lore_workflow.roadmap_validator import roadmap_counts, validate_roadmap
from lore_workflow.seed_epic import compose_seed_lift
from rich.console import Console

from lore_cli._argv_compat import argv_main

console = Console()

app = typer.Typer(
    add_completion=False,
    help="Deterministic epic-workflow gates: roadmap validation, PRD scaffolding.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.command("validate-roadmap")
def validate_roadmap_cmd(
    path: str = typer.Argument(
        "-", help="Path to the epic body Markdown, or '-' to read stdin."
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine output: {ok, rows, repos, edges, problems}. "
        "Exit code is unchanged (0 valid, 1 invalid).",
    ),
) -> None:
    """Validate an epic's roadmap table: required columns, fully-qualified
    `owner/repo#n` issue refs, resolvable blocked-by edges, acyclic DAG.
    """
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    result = validate_roadmap(text)
    if as_json:
        counts = roadmap_counts(result)
        # stdout, not rich console: keep it parse-clean (no markup/wrapping).
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "rows": counts.rows,
                    "repos": counts.repos,
                    "edges": counts.edges,
                    "problems": [
                        {"kind": p.kind, "message": p.message} for p in result.problems
                    ],
                }
            )
        )
        if not result.ok:
            raise typer.Exit(code=1)
        return
    if result.ok:
        console.print(
            f"[green]roadmap OK[/green]: {len(result.rows)} feature(s), "
            "dependency DAG is acyclic"
        )
        return
    console.print("[red]roadmap INVALID[/red]:")
    for problem in result.problems:
        console.print(f"  - {problem.kind}: {problem.message}")
    raise typer.Exit(code=1)


@app.command("create-prd")
def create_prd_cmd(
    slug: str = typer.Option(..., "--slug", help="Kebab-case PRD slug."),
    title: str = typer.Option(..., "--title", help="PRD title."),
    epic_url: str = typer.Option(..., "--epic-url", help="URL of the tracking epic."),
    repo: list[str] = typer.Option(
        None, "--repo", help="Involved repo (owner/repo). Repeat for multiple."
    ),
    target: Path | None = typer.Option(
        None, "--target", help="Repo root under which docs/prd/ is created (default: cwd)."
    ),
) -> None:
    """Write `docs/prd/NNNN-<slug>.md` and wire it into `docs/prd/index.md`."""
    path = create_prd(
        target or Path("."), slug=slug, title=title, epic_url=epic_url, repos=repo or []
    )
    console.print(f"[green]wrote[/green] {path}")


@app.command("prd-ship")
def prd_ship_cmd(
    path: Annotated[Path, typer.Argument(help="Path to the PRD file.")],
    adr: Annotated[
        list[str] | None,
        typer.Option("--adr", help="A current ADR number, e.g. 0014. Repeat for each ADR."),
    ] = None,
) -> None:
    """Mark a PRD shipped once its epic merged (ADR 0015).

    Sets `status: shipped` and adds `> Historical. Current decisions: ADR ...`
    under the title. Retrieval then ranks the PRD below ADRs. Idempotent.
    """
    try:
        ship_prd(path, adr or [])
    except (PrdShipError, OSError) as exc:
        console.print(f"[red]prd-ship failed[/red]: {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]shipped[/green] {path}")


@app.command("epic-policy")
def epic_policy_cmd(
    repo_root: str = typer.Argument(
        ".", help="Repo root to resolve policy for (default: cwd)."
    ),
) -> None:
    """Emit a repo's epic-merge policy as JSON: {target_branch, deploy_gate}.

    `target_branch` is `develop` if that branch exists on `origin`, else
    `main`. `deploy_gate` is true iff `AGENTS.md` declares
    `epic-merge-policy: confirm` under its `## Epic merge policy` section.
    """
    policy = resolve_epic_policy(Path(repo_root))
    # stdout, not rich console: keep it parse-clean for machine consumers.
    print(
        json.dumps(
            {"target_branch": policy.target_branch, "deploy_gate": policy.deploy_gate}
        )
    )


@app.command("seed-lift")
def seed_lift_cmd(
    note_path: Path = typer.Argument(..., help="Path to the session note to lift from."),
    wiki_root: Path | None = typer.Option(
        None, "--wiki-root", help="Wiki root; when given, source_note is recorded wiki-relative."
    ),
) -> None:
    """Lift Origin/Findings for a seed issue from a session note.

    Prints `{origin, findings, source_note}` as JSON on success. Exits 1
    when the note is missing or too thin to say anything a freehand pass
    wouldn't already have — the skill's signal to fall back to freehand.
    """
    lift = compose_seed_lift(note_path, wiki_root=wiki_root)
    if lift is None:
        console.print("[yellow]no usable note[/yellow]: fall back to freehand Origin/Findings")
        raise typer.Exit(code=1)
    # Plain (uncolored) JSON: this is machine output for the skill to parse,
    # not a human-facing message like the other subcommands' console.print.
    typer.echo(
        json.dumps(
            {"origin": lift.origin, "findings": lift.findings, "source_note": lift.source_note}
        )
    )


@app.command("parse-board")
def parse_board_cmd(
    path: str = typer.Argument(
        "-", help="Path to the board comment body, or '-' to read stdin."
    ),
) -> None:
    """Parse a build epic-mode supervision-board comment into JSON rows.

    Emits {rows: [{feature, issue, tier, batch, state, pr}, ...],
    ledger: [{kind, outcome, timestamp, text}, ...]}. `ledger` holds the
    lines of the `## Ledger` section, empty when the board has none. A
    missing marker, missing columns, a malformed row or a malformed ledger
    line exits 1 with a clear error on stderr — never a silent misread.
    """
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    try:
        rows = parse_board(text)
        ledger = parse_board_ledger(text)
    except (BoardParseError, LedgerParseError) as exc:
        print(f"board parse error: {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from exc
    # stdout, not rich console: keep it parse-clean for machine consumers.
    print(
        json.dumps({"rows": [asdict(row) for row in rows], "ledger": [e.to_dict() for e in ledger]})
    )


def _ledger_path(path: str | None) -> Path:
    """Resolve `--path`, or the git-dir ledger of the cwd."""
    if path:
        return Path(path)
    resolved = default_ledger_path(Path.cwd())
    if resolved is None:
        print("not inside a git repository; pass --path", file=sys.stderr)
        raise typer.Exit(code=1)
    return resolved


@app.command("ledger-add")
def ledger_add_cmd(
    kind: str = typer.Option(..., "--kind", help=f"One of: {', '.join(KINDS)}."),
    text: str = typer.Option(..., "--text", help="The line's text. For a resume line: the state."),
    outcome: str = typer.Option(
        "open",
        "--outcome",
        help="open | approved | dropped | 'filed <owner/repo#n>'. Ignored for resume.",
    ),
    next_ask: str | None = typer.Option(
        None, "--next", help="Resume lines only: the next ask, appended as '; next: <ask>'."
    ),
    path: str | None = typer.Option(
        None, "--path", help="Ledger file (default: <git-dir>/lore-ledger.md of the cwd)."
    ),
) -> None:
    """Append one line to a loop/issue ledger file and print it.

    An epic keeps its ledger in the board comment; print the line with
    `--path -` and paste it into the `## Ledger` section.
    """
    if kind == "resume":
        body = f"{text}; next: {next_ask}" if next_ask else text
        entry = LedgerEntry(kind=kind, text=body, timestamp=utc_now())
    else:
        entry = LedgerEntry(kind=kind, text=text, outcome=outcome)
    try:
        if path == "-":
            print(format_entry(entry))
            return
        line = append_entry(_ledger_path(path), entry)
    except ValueError as exc:
        print(f"ledger-add: {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from exc
    print(line)


@app.command("ledger-set")
def ledger_set_cmd(
    selector: str = typer.Argument(
        ..., help="1-based line index as ledger-check lists it, or text that matches one line."
    ),
    outcome: str = typer.Option(
        ..., "--outcome", help="approved | dropped | 'filed <owner/repo#n>' | open."
    ),
    path: str | None = typer.Option(
        None, "--path", help="Ledger file (default: <git-dir>/lore-ledger.md of the cwd)."
    ),
    board: str | None = typer.Option(
        None,
        "--board",
        help="'-': read an epic board comment on stdin, print the whole updated comment.",
    ),
) -> None:
    """Set the outcome of one line in a ledger file, in place.

    With `--board -`, rewrite the line inside the `## Ledger` section of a
    board comment read from stdin, and print the whole updated comment. Edit
    the comment on GitHub with that output.
    """
    if board is not None:
        if board != "-":
            print("ledger-set: use --board - and pipe the board comment in", file=sys.stderr)
            raise typer.Exit(code=1)
        try:
            updated, _ = set_board_outcome(sys.stdin.read(), selector, outcome)
        except ValueError as exc:
            print(f"ledger-set: {exc}", file=sys.stderr)
            raise typer.Exit(code=1) from exc
        sys.stdout.write(updated)
        return
    try:
        line = set_outcome(_ledger_path(path), selector, outcome)
    except (OSError, ValueError) as exc:
        print(f"ledger-set: {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from exc
    print(line)


@app.command("ledger-check")
def ledger_check_cmd(
    path: str | None = typer.Argument(
        None,
        help="Ledger file, or '-' to read a ledger or a board comment from stdin "
        "(default: <git-dir>/lore-ledger.md of the cwd).",
    ),
) -> None:
    """Gate a finish point: exit 1 while any non-resume ledger line is `open`.

    Names each open line with its index for `ledger-set`. A malformed ledger
    line also exits 1. A missing ledger has nothing to check and exits 0. Empty stdin exits 1.
    """
    if path == "-":
        text = sys.stdin.read()
        source = "stdin"
        if not text.strip():
            # A failed `gh api … --jq .body` pipes nothing; the gate fails closed.
            print("ledger-check: no input on stdin", file=sys.stderr)
            raise typer.Exit(code=1)
    else:
        ledger = _ledger_path(path)
        source = str(ledger)
        if not ledger.exists():
            print(f"no ledger at {ledger}: nothing to check")
            return
        text = ledger.read_text(encoding="utf-8")
    try:
        entries = parse_ledger_source(text)
    except LedgerParseError as exc:
        print(f"ledger-check: {source}: {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from exc
    still_open = open_entries(entries)
    if not still_open:
        print(f"ledger OK: {len(entries)} line(s), none open ({source})")
        return
    # Plain print, not rich: `[adr]` would read as rich markup.
    print(f"ledger has {len(still_open)} open line(s) ({source}):")
    for index, entry in still_open:
        print(f"  {index}. [{entry.kind}] {entry.text}")
    print("Set each outcome: approved, dropped or filed <owner/repo#n>.")
    raise typer.Exit(code=1)


@app.command("ledger-archive")
def ledger_archive_cmd(
    path: str | None = typer.Option(
        None, "--path", help="Ledger file (default: <git-dir>/lore-ledger.md of the cwd)."
    ),
) -> None:
    """Rename a finished ledger to `lore-ledger.<UTC-date>.done.md`.

    Run it at a loop or issue finish point, after `ledger-check` passes. The
    SessionStart resume offer then stops. Exits 1 while a line is open. A
    missing ledger has nothing to archive and exits 0.
    """
    ledger = _ledger_path(path)
    if not ledger.exists():
        print(f"no ledger at {ledger}: nothing to archive")
        return
    try:
        target = archive_ledger(ledger)
    except (OSError, ValueError) as exc:
        print(f"ledger-archive: {ledger}: {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from exc
    print(f"ledger archived: {target}")


def _risk_config():
    """`workflow.risk` from the root config; defaults when it cannot load."""
    from lore_core.config import get_lore_root
    from lore_core.root_config import RiskConfig, load_root_config

    try:
        return load_root_config(get_lore_root()).workflow.risk
    except Exception:  # noqa: BLE001 - a broken config falls back to defaults
        return RiskConfig()


@app.command("risk")
def risk_cmd(
    pr_arg: int | None = typer.Argument(None, metavar="[PR]", help="Pull request number."),
    pr: int | None = typer.Option(None, "--pr", help="Pull request number (`gh pr diff`)."),
    rev_range: str | None = typer.Option(
        None, "--range", help="Revision range such as main..HEAD (`git diff`)."
    ),
    diff: str | None = typer.Option(
        None, "--diff", help="A unified diff file, or '-' to read stdin."
    ),
    repo: str | None = typer.Option(None, "--repo", help="owner/repo for --pr."),
    as_json: bool = typer.Option(
        False, "--json", help="Emit {level, reasons, notes, files, lines}."
    ),
) -> None:
    """Print the risk level of a diff: `low` or `high`, then the reasons.

    Thresholds and sensitive paths come from `workflow.risk` in the root
    config. Exits 0 for either level; exits 1 when the diff cannot be read.
    """
    pr = pr if pr is not None else pr_arg
    chosen = [x for x in (pr, rev_range, diff) if x is not None]
    if len(chosen) != 1:
        print("risk: pass exactly one of --pr, --range or --diff", file=sys.stderr)
        raise typer.Exit(code=1)
    cwd = Path.cwd()
    try:
        if pr is not None:
            text = diff_for_pr(pr, repo=repo, cwd=cwd)
        elif rev_range is not None:
            text = diff_for_range(rev_range, cwd=cwd)
        else:
            text = sys.stdin.read() if diff == "-" else Path(diff).read_text(encoding="utf-8")
    except (RiskInputError, OSError) as exc:
        print(f"risk: {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from exc

    root = next((p for p in (cwd, *cwd.parents) if (p / ".git").exists()), cwd)
    result = assess(parse_unified_diff(text), _risk_config(), root=root)
    if as_json:
        print(json.dumps(result.to_dict()))
        return
    print(result.level)
    for reason in result.reasons:
        print(f"- {reason}")
    for note in result.notes:
        print(f"note: {note}")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
