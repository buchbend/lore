"""`lore doctor` — smoke-test the Lore install.

Onboarding silent-failure is the real adoption killer (per the UX
agent's review). This subcommand walks the most common breakage
points and prints exactly one line per check (✓ or ✗). Exits
non-zero on any failure.

Checks:
  1. LORE_ROOT resolves and exists
  2. wiki/ subdir exists with at least one wiki
  3. cache dir is writable (~/.cache/lore/ or $LORE_CACHE)
  4. SessionStart hook is reachable (`lore hook session-start --plain`)
  5. MCP server module imports (`python -m lore_mcp.server` would start)
  6. lore_search index responds (FtsBackend.stats() succeeds)
  7. Current cwd's `## Lore` block parses (if attached; else skipped)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import typer
from rich.console import Console

from lore_cli._compat import argv_main

console = Console()

app = typer.Typer(
    add_completion=False,
    help="Smoke-test the Lore install.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)


# A check takes the current cwd (most ignore it) and returns
# (ok: bool, message: str). Side-effect-free except for the
# cache-write probe which is reverted immediately.
Check = tuple[bool, str]


def _check_lore_root(cwd: str) -> Check:
    from lore_core.config import get_lore_root

    root = get_lore_root()
    if not root.exists():
        return False, f"LORE_ROOT={root} does not exist (set $LORE_ROOT or run `lore init`)"
    return True, f"LORE_ROOT={root}"


def _check_wikis(cwd: str) -> Check:
    from lore_core.config import get_wiki_root

    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        return False, f"{wiki_root} missing (run `lore init` or `lore new-wiki <name>`)"
    wikis = [p for p in sorted(wiki_root.iterdir()) if p.resolve().is_dir()]
    if not wikis:
        return False, f"no wikis under {wiki_root} (run `lore new-wiki <name>`)"
    return True, f"{len(wikis)} wiki(s): {', '.join(w.name for w in wikis)}"


def _check_cache_writable(cwd: str) -> Check:
    cache_env = os.environ.get("LORE_CACHE")
    cache = Path(cache_env).expanduser() if cache_env else Path.home() / ".cache" / "lore"
    try:
        cache.mkdir(parents=True, exist_ok=True)
        probe = cache / ".doctor-probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        return False, f"cache {cache} not writable: {e}"
    return True, f"cache {cache} writable"


def _check_hook_runnable(cwd: str) -> Check:
    """Run `lore hook session-start --plain --probe` and confirm it produces output.

    `--probe` suppresses side-effects (curator spawns, stamp/lock writes, ledger
    mutations) so the diagnostic doesn't mutate the thing it's diagnosing.
    """
    cmd = [sys.executable, "-m", "lore_cli", "hook", "session-start", "--plain", "--probe",
           "--cwd", cwd]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"hook exec failed: {e}"
    if result.returncode != 0:
        return False, f"hook exited {result.returncode}: {result.stderr.strip()[:200]}"
    if not result.stdout.strip():
        return False, "hook produced empty output (no wiki resolved?)"
    first_line = result.stdout.strip().splitlines()[0][:80]
    return True, f"hook → `{first_line}`"


def _check_mcp_imports(cwd: str) -> Check:
    try:
        import lore_mcp.server  # noqa: F401

        # Confirm tool schema generation works
        schema = lore_mcp.server._tool_schema()  # noqa: SLF001
    except Exception as e:
        return False, f"MCP server import/schema failed: {e}"
    return True, f"MCP server ready ({len(schema)} tools)"


def _check_search_backend(cwd: str) -> Check:
    try:
        from lore_search.fts import FtsBackend

        backend = FtsBackend()
        stats = backend.stats()
    except Exception as e:
        return False, f"FTS backend failed: {e}"
    return True, f"FTS index: {stats.get('total_notes', '?')} notes"


def _check_attach(cwd: str) -> Check:
    from lore_core.session import _resolve_attach_block

    cfg = _resolve_attach_block(Path(cwd))
    if cfg is None:
        return True, f"no attachment covers {cwd} (skipped)"
    path, block = cfg
    return True, f"attached at {path.parent}: wiki={block.get('wiki')}"


def _check_attachments(cwd: str) -> Check:
    """Validate every attachments.json row: path exists, wiki dir exists,
    scope in scopes.json, fingerprint matches current `.lore.yml` if one
    exists at the attachment path.
    """
    from lore_core.config import get_lore_root, get_wiki_root
    from lore_core.offer import offer_fingerprint, parse_lore_yml, FILENAME as LORE_YML
    from lore_core.state.attachments import AttachmentsFile
    from lore_core.state.scopes import ScopesFile

    lore_root = get_lore_root()
    if not (lore_root / ".lore" / "attachments.json").exists():
        return True, "no attachments.json (run `lore attach accept` to register)"

    af = AttachmentsFile(lore_root)
    af.load()
    sf = ScopesFile(lore_root)
    sf.load()
    wiki_root = get_wiki_root()

    issues: list[str] = []
    total = 0
    for a in af.all():
        total += 1
        if not a.path.exists():
            issues.append(f"{a.path}: missing on disk")
            continue
        wiki_dir = wiki_root / a.wiki
        if not wiki_dir.exists():
            issues.append(f"{a.path}: wiki `{a.wiki}` does not exist in {wiki_root}")
        if sf.get(a.scope) is None:
            issues.append(f"{a.path}: scope `{a.scope}` not in scopes.json")
        # Fingerprint check — only when a .lore.yml is present at the attachment root
        if a.offer_fingerprint is not None:
            lore_yml = a.path / LORE_YML
            if lore_yml.exists():
                offer = parse_lore_yml(lore_yml)
                if offer is not None and offer_fingerprint(offer) != a.offer_fingerprint:
                    issues.append(f"{a.path}: .lore.yml fingerprint drift (run `lore attach accept`)")

    if issues:
        issue_summary = issues[0]
        if len(issues) > 1:
            issue_summary += f" (+ {len(issues) - 1} more)"
        return False, f"{total} attachment(s) — {len(issues)} issue(s): {issue_summary}"
    return True, f"{total} attachment(s), all valid"


def _check_scope_tree(cwd: str) -> Check:
    """Scope-tree integrity: every scope's ID-derived parent exists, every
    root has a wiki, and flag scopes whose resolved wiki doesn't match a
    real wiki dir.
    """
    from lore_core.config import get_lore_root, get_wiki_root
    from lore_core.state.scopes import ScopesFile, parent_of

    lore_root = get_lore_root()
    if not (lore_root / ".lore" / "scopes.json").exists():
        return True, "no scopes.json (builds on first attach)"

    sf = ScopesFile(lore_root)
    sf.load()
    ids = sf.all_ids()
    wiki_root = get_wiki_root()

    issues: list[str] = []
    for sid in ids:
        parent = parent_of(sid)
        if parent is not None and sf.get(parent) is None:
            issues.append(f"{sid}: parent `{parent}` missing")
        resolved_wiki = sf.resolve_wiki(sid)
        if resolved_wiki is None:
            issues.append(f"{sid}: no resolved wiki")
        elif not (wiki_root / resolved_wiki).exists():
            issues.append(f"{sid}: resolved wiki `{resolved_wiki}` does not exist")

    if issues:
        issue_summary = issues[0]
        if len(issues) > 1:
            issue_summary += f" (+ {len(issues) - 1} more)"
        return False, f"{len(ids)} scope(s) — {len(issues)} issue(s): {issue_summary}"
    return True, f"{len(ids)} scope(s), tree healthy"


def _check_ledger_buckets(cwd: str) -> Check:
    """Surface the ledger's __orphan__/__unattached__ buckets as
    actionable informational output. Never fails — these are not errors,
    they're surfaces the user may want to act on via
    `lore attachments purge-unattached`.
    """
    from lore_core.config import get_lore_root
    from lore_core.ledger import TranscriptLedger

    lore_root = get_lore_root()
    ledger_path = lore_root / ".lore" / "transcript-ledger.json"
    if not ledger_path.exists():
        return True, "no transcript ledger (capture hasn't fired yet)"

    try:
        buckets = TranscriptLedger(lore_root).pending_by_wiki()
    except Exception as e:
        return False, f"ledger read failed: {e}"

    orphan = len(buckets.get("__orphan__", []))
    unattached = len(buckets.get("__unattached__", []))
    attached_total = sum(
        len(v) for k, v in buckets.items() if not k.startswith("__")
    )

    parts = [f"{attached_total} attached"]
    if orphan:
        parts.append(f"{orphan} orphan")
    if unattached:
        parts.append(f"{unattached} unattached (run `lore attachments purge-unattached`)")
    return True, " · ".join(parts)


# (name, check_fn, fails_run). `fails_run=False` means the check is
# informational — its `ok=False` is rendered as ✗ but does not set the
# overall non-zero exit.
_CHECKS: list[tuple[str, Callable[[str], Check], bool]] = [
    ("LORE_ROOT", _check_lore_root, True),
    ("wikis", _check_wikis, True),
    ("cache", _check_cache_writable, True),
    ("MCP server", _check_mcp_imports, True),
    ("FTS backend", _check_search_backend, True),
    ("attachments", _check_attachments, True),
    ("scope tree", _check_scope_tree, True),
    ("ledger buckets", _check_ledger_buckets, True),
    ("SessionStart hook", _check_hook_runnable, True),
    ("attach", _check_attach, False),
]


@app.callback(invoke_without_command=True)
def doctor(
    cwd: str = typer.Option(
        None,
        "--cwd",
        help="Working directory for hook + attach checks (default: $PWD)",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON envelope on stdout (lore.doctor/1).",
    ),
) -> None:
    """Walk the most common breakage points and print one line per check."""
    cwd = cwd or os.getcwd()

    results: list[dict] = []
    all_ok = True
    for name, check, fails_run in _CHECKS:
        ok, msg = check(cwd)
        results.append({"check": name, "ok": ok, "message": msg})
        if not ok and fails_run:
            all_ok = False

    if json_out:
        print(
            json.dumps(
                {
                    "schema": "lore.doctor/1",
                    "data": {"ok": all_ok, "checks": results},
                },
                indent=2,
            )
        )
    else:
        for r in results:
            mark = "[green]✓[/green]" if r["ok"] else "[red]✗[/red]"
            console.print(f"{mark} [bold]{r['check']:<20}[/bold] {r['message']}")

        # Doctor is install-integrity only; `lore status` is the
        # activity panel — point there so users know where to look.
        if all_ok:
            console.print("\n[green]Install looks good.[/green] For activity: [bold]lore status[/bold]")
        else:
            console.print("\n[red]Some checks failed — see above.[/red] For activity: [bold]lore status[/bold]")

    if not all_ok:
        raise typer.Exit(code=1)


# Backwards-compat shim for tests + the legacy SUBCOMMANDS dispatcher.
main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
