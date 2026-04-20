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
from datetime import UTC, datetime, timedelta
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


# A check returns (ok: bool, message: str). Side-effect-free except
# for the cache-write probe which is reverted immediately.
Check = tuple[bool, str]


def _check_lore_root() -> Check:
    from lore_core.config import get_lore_root

    root = get_lore_root()
    if not root.exists():
        return False, f"LORE_ROOT={root} does not exist (set $LORE_ROOT or run `lore init`)"
    return True, f"LORE_ROOT={root}"


def _check_wikis() -> Check:
    from lore_core.config import get_wiki_root

    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        return False, f"{wiki_root} missing (run `lore init` or `lore new-wiki <name>`)"
    wikis = [p for p in sorted(wiki_root.iterdir()) if p.resolve().is_dir()]
    if not wikis:
        return False, f"no wikis under {wiki_root} (run `lore new-wiki <name>`)"
    return True, f"{len(wikis)} wiki(s): {', '.join(w.name for w in wikis)}"


def _check_cache_writable() -> Check:
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


def _check_hook_runnable(cwd: str | None) -> Check:
    """Run `lore hook session-start --plain` and confirm it produces output."""
    cmd = [sys.executable, "-m", "lore_cli", "hook", "session-start", "--plain"]
    if cwd:
        cmd += ["--cwd", cwd]
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


def _check_mcp_imports() -> Check:
    try:
        import lore_mcp.server  # noqa: F401

        # Confirm tool schema generation works
        schema = lore_mcp.server._tool_schema()  # noqa: SLF001
    except Exception as e:
        return False, f"MCP server import/schema failed: {e}"
    return True, f"MCP server ready ({len(schema)} tools)"


def _check_search_backend() -> Check:
    try:
        from lore_search.fts import FtsBackend

        backend = FtsBackend()
        stats = backend.stats()
    except Exception as e:
        return False, f"FTS backend failed: {e}"
    return True, f"FTS index: {stats.get('total_notes', '?')} notes"


def _check_attach(cwd: str | None) -> Check:
    if not cwd:
        return True, "skip (no --cwd given)"
    from lore_core.session import _walk_up_lore_config

    cfg = _walk_up_lore_config(Path(cwd))
    if cfg is None:
        return True, f"no `## Lore` block in {cwd} ancestors (skipped)"
    path, block = cfg
    return True, f"`## Lore` at {path.parent}: wiki={block.get('wiki')}"


_CHECKS = [
    ("LORE_ROOT", _check_lore_root),
    ("wikis", _check_wikis),
    ("cache", _check_cache_writable),
    ("MCP server", _check_mcp_imports),
    ("FTS backend", _check_search_backend),
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
    for name, check in _CHECKS:
        ok, msg = check()
        results.append({"check": name, "ok": ok, "message": msg})
        if not ok:
            all_ok = False

    # Hook + attach checks need cwd
    ok, msg = _check_hook_runnable(cwd)
    results.append({"check": "SessionStart hook", "ok": ok, "message": msg})
    if not ok:
        all_ok = False

    ok, msg = _check_attach(cwd)
    results.append({"check": "## Lore attach", "ok": ok, "message": msg})
    # Attach failures don't fail the run — informational.

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

        # Capture pipeline panel
        from lore_core.config import get_lore_root as _gl

        try:
            lr = _gl()
        except Exception:
            lr = None
        if lr is not None:
            console.print()
            for line in run_capture_panel(lr):
                console.print(line)

        if all_ok:
            console.print("\n[green]All checks passed.[/green]")
        else:
            console.print("\n[red]Some checks failed — see above.[/red]")

    if not all_ok:
        raise typer.Exit(code=1)


def run_capture_panel(lore_root: Path) -> list[str]:
    """Return lines for the Capture pipeline panel of `lore doctor`."""
    lines: list[str] = ["Capture pipeline"]
    any_data = False

    # Last hook
    events_path = lore_root / ".lore" / "hook-events.jsonl"
    if events_path.exists():
        any_data = True
        last = _last_json_line(events_path)
        if last:
            event = last.get("event", "?")
            outcome = last.get("outcome", "?")
            ago = _relative_cap(last.get("ts", ""))
            lines.append(f"  ✓ Last hook fired {ago} ({event}, outcome: {outcome})")

    # Last curator run + last note filed (walk newest→oldest)
    runs_dir = lore_root / ".lore" / "runs"
    if runs_dir.exists():
        files = sorted(
            (p for p in runs_dir.glob("*.jsonl") if not p.name.endswith(".trace.jsonl")),
            key=lambda p: p.name,
            reverse=True,
        )
        if files:
            any_data = True
            latest = files[0]
            try:
                records = [json.loads(l) for l in latest.read_text().splitlines() if l.strip()]
            except (OSError, json.JSONDecodeError):
                records = []
            end = next((r for r in reversed(records) if r.get("type") == "run-end"), None)
            if end:
                ago = _relative_cap(end.get("ts", ""))
                dur = f"{end.get('duration_ms', 0) / 1000:.1f}s"
                t_count = sum(1 for r in records if r.get("type") == "transcript-start")
                errors = end.get("errors", 0)
                lines.append(
                    f"  ✓ Last curator run {ago} ({dur}, {t_count} transcripts, {errors} errors)"
                )
            last_note = None
            for p in files:
                try:
                    for l in p.read_text().splitlines():
                        try:
                            r = json.loads(l)
                        except json.JSONDecodeError:
                            continue
                        if r.get("type") == "session-note" and r.get("action") == "filed":
                            last_note = r
                            break
                except OSError:
                    continue
                if last_note:
                    break
            if last_note:
                lines.append(
                    f"  ✓ Last note filed {_relative_cap(last_note.get('ts', ''))} — "
                    f"{last_note.get('wikilink', '')}"
                )

    # Lock holder / free
    from lore_core.lockfile import read_lock_holder

    lock_dir = lore_root / ".lore" / "curator.lock"
    if lock_dir.exists():
        any_data = True
        holder = read_lock_holder(lore_root)
        try:
            age_s = datetime.now(UTC).timestamp() - lock_dir.stat().st_mtime
        except OSError:
            age_s = 0
        age_str = f"{int(age_s)}s" if age_s < 60 else f"{int(age_s / 60)}m"
        if holder:
            pid = holder.get("pid", "?")
            rid = holder.get("run_id") or "?"
            icon = "✗" if age_s > 3600 else "✓"
            suffix = (
                " — likely stale, remove with `rm -rf $LORE_ROOT/.lore/curator.lock`"
                if age_s > 3600
                else ""
            )
            lines.append(
                f"  {icon} Curator lock held by PID {pid} (run {rid}, {age_str}){suffix}"
            )
        else:
            icon = "✗" if age_s > 3600 else "✓"
            stale_note = " — likely stale" if age_s > 3600 else ""
            lines.append(
                f"  {icon} Curator lock held ({age_str}, no holder metadata){stale_note}"
            )
    else:
        lines.append("  ✓ Curator lock free")

    # Hook errors in last 24h
    hook_err = 0
    if events_path.exists():
        threshold = datetime.now(UTC) - timedelta(hours=24)
        try:
            for l in events_path.read_text().splitlines():
                try:
                    r = json.loads(l)
                except json.JSONDecodeError:
                    continue
                if r.get("outcome") != "error":
                    continue
                ts_str = r.get("ts")
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                if ts >= threshold:
                    hook_err += 1
        except OSError:
            pass
    if hook_err > 0:
        lines.append(
            f"  ✗ {hook_err} hook error{'s' if hook_err > 1 else ''} in last 24h "
            f"— lore runs list --hooks"
        )

    # Observability log-write failures sentinel
    marker = lore_root / ".lore" / "hook-log-failed.marker"
    if marker.exists():
        try:
            mtime = datetime.fromtimestamp(marker.stat().st_mtime, tz=UTC)
            age = datetime.now(UTC) - mtime
            if age < timedelta(days=1):
                lines.append(
                    f"  ✗ Hook log write failed {_relative_cap(mtime.isoformat().replace('+00:00', 'Z'))} "
                    f"— check disk space / permissions on {lore_root / '.lore'}"
                )
        except OSError:
            pass

    if not any_data:
        lines.append("  No capture activity yet")
    return lines


def _last_json_line(path: Path) -> dict | None:
    """Read the last JSON line from a file, skipping decode errors."""
    try:
        for line in reversed(path.read_text().splitlines()):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    except OSError:
        return None
    return None


def _relative_cap(ts_iso: str) -> str:
    """Convert ISO timestamp to relative time (e.g., '5m ago')."""
    if not ts_iso:
        return "?"
    try:
        ts = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    except ValueError:
        return ts_iso
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - ts
    s = delta.total_seconds()
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)}m ago"
    if s < 86400:
        return f"{int(s // 3600)}h ago"
    return f"{int(s // 86400)}d ago"


# Backwards-compat shim for tests + the legacy SUBCOMMANDS dispatcher.
main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
