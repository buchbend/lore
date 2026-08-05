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
  5. MCP server module imports (`lore mcp` would start it)
  6. lore_search index responds (FtsBackend.stats() succeeds)
  7. Current cwd's `## Lore` block parses (if attached; else skipped)
  8. Vale is on PATH (advisory — absence degrades the writing-rules lint
     to instruction-only, per ADR 0006, and never fails this run)

`--fix` additionally repairs: rebuilds scopes.json from attachments.json,
re-stamps drifted offer fingerprints, and migrates attachment path
prefixes. Every repair prints what it will change and is individually
declinable (prompt per repair, `--yes` to skip). Plain `doctor` (no
`--fix`) never writes state — see docs/architecture/state.md.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import typer
from rich.console import Console

from lore_cli._argv_compat import argv_main

# emoji=False: scope IDs are colon-separated (`lore:a:b`) and Rich's emoji
# shortcode parser reads `:a:` as the letter-A emoji, silently corrupting
# any scope chain with a single-character segment when printed. Markup
# ([green]/[bold]/etc.) still works — only :shortcode: substitution is off.
console = Console(emoji=False)

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


def _issue_summary(label: str, issues: list[str], ok_msg: str) -> Check:
    """Format a check result from an item label and a list of issue strings."""
    if issues:
        summary = issues[0]
        if len(issues) > 1:
            summary += f" (+ {len(issues) - 1} more)"
        return False, f"{label} — {len(issues)} issue(s): {summary}"
    return True, ok_msg


def _check_lore_root(cwd: str) -> Check:
    from lore_core.config import get_lore_root, lore_root_source

    root = get_lore_root()
    label = {
        "env": "$LORE_ROOT",
        "config": "config-file",
        "default": "unconfigured (fallback)",
    }[lore_root_source()]
    if not root.exists():
        return (
            False,
            f"LORE_ROOT={root} [{label}] — does not exist "
            "(set $LORE_ROOT, write ~/.config/lore/config.yml, or run `lore init`)",
        )
    return True, f"LORE_ROOT={root} [{label}]"


def _check_wikis(cwd: str) -> Check:
    from lore_core.config import get_wiki_root

    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        return False, f"{wiki_root} missing (run `lore init` or `lore wiki new <name>`)"
    wikis = [p for p in sorted(wiki_root.iterdir()) if p.resolve().is_dir()]
    if not wikis:
        return False, f"no wikis under {wiki_root} (run `lore wiki new <name>`)"
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


def _check_lore_version_drift(cwd: str) -> Check:
    """Compare the installed `lore` Python package version against the
    on-disk source tree (auto-detected by walking up from this file).

    See issue #28 — the Claude Code plugin and the Python CLI binary
    are on separate update channels (`claude plugin update` vs
    `pipx install`); drift between them causes SessionStart's status
    line to silently show the *binary's* old version even after a
    successful plugin update.
    """
    from lore_core.source_root import check_lore_version_match

    # Find the source repo root by walking up from this file looking
    # for a pyproject.toml. Returns None gracefully if running from a
    # PyPI install (no source tree adjacent).
    here = Path(__file__).resolve()
    repo: Path | None = None
    for ancestor in [here, *here.parents]:
        if (ancestor / "pyproject.toml").is_file() and (ancestor / "lib").is_dir():
            repo = ancestor
            break

    return check_lore_version_match(repo)


def _check_plugin_manifest_sync(cwd: str) -> Check:
    """Compare the source-tree plugin manifest version to the installed pip version.

    Catches the headline footgun documented in CHANGELOG: ``claude
    plugin update lore@lore`` is a separate channel from ``pipx
    install --upgrade lore``. If the user upgrades pip but doesn't
    refresh the plugin cache, new hooks (PostToolUse:ExitPlanMode in
    v0.14.0) silently don't fire.

    Limitation: lore can't directly interrogate Claude Code's plugin
    cache from outside the harness. The best we can do is compare
    the source-tree plugin.json version to the installed pip version
    (which test_version_sync.py already enforces in CI for
    consistency) AND surface an informational reminder.
    """
    import json as _json
    from importlib.metadata import PackageNotFoundError, version

    here = Path(__file__).resolve()
    repo: Path | None = None
    for ancestor in [here, *here.parents]:
        if (ancestor / "pyproject.toml").is_file() and (ancestor / "lib").is_dir():
            repo = ancestor
            break
    if repo is None:
        return True, (
            "skipped: no on-disk source tree to compare plugin.json — "
            "remember to run `/plugin update lore` after each pip upgrade"
        )

    manifest_path = repo / ".claude-plugin" / "plugin.json"
    if not manifest_path.exists():
        return True, (
            f"skipped: {manifest_path} not found — "
            "remember to run `/plugin update lore` after each pip upgrade"
        )

    try:
        manifest = _json.loads(manifest_path.read_text())
    except (OSError, ValueError) as e:
        return False, f"plugin.json unreadable: {e}"

    plugin_version = str(manifest.get("version") or "")
    if not plugin_version:
        return False, "plugin.json has no `version` field"

    try:
        installed = version("lore")
    except PackageNotFoundError:
        return False, "lore Python package not installed in this environment"

    if plugin_version != installed:
        return False, (
            f"plugin.json version {plugin_version} != installed pip {installed}; "
            "after `pipx install --upgrade lore` run `/plugin update lore` in Claude Code"
        )

    return True, (
        f"plugin.json {plugin_version} matches pip — but Claude Code's plugin "
        "cache is opaque from here. After every pip upgrade, run "
        "`/plugin update lore` in a Claude Code session to refresh hooks/skills/MCP."
    )


def _check_claude_plugin_cache_drift(cwd: str) -> Check:
    """Compare the Claude Code plugin cache version to the installed pip version.

    Complements ``_check_plugin_manifest_sync`` (which compares against the
    on-disk source tree): this check reads the actual plugin-cache state
    that ``claude plugin update lore@lore`` writes to. The mismatch this
    catches is the headline footgun:

      * user runs ``claude plugin update lore@lore`` → cache bumps
        (e.g. 0.10.0 → 0.13.1)
      * pipx-installed ``lore`` binary stays on 0.10.0
      * SessionStart's banner reads ``importlib.metadata.version("lore")``
        and silently reports the *binary* version, hiding the plugin
        update entirely

    Cache state lives in ``~/.claude/plugins/installed_plugins.json`` —
    plain JSON; opaque is too strong a word.
    """
    import json as _json
    from importlib.metadata import PackageNotFoundError, version

    cache_index = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    if not cache_index.exists():
        return True, (
            "skipped: no Claude plugin cache at "
            f"{cache_index} (Claude Code not installed?)"
        )

    try:
        index = _json.loads(cache_index.read_text())
    except (OSError, ValueError) as exc:
        return False, f"plugin cache index unreadable: {exc}"

    entries = index.get("plugins", {}).get("lore@lore") or []
    # Pick the most recently updated entry; multiple scopes (user/project)
    # can coexist but the user-scope is what SessionStart hooks bind to.
    user_entry = next(
        (e for e in entries if e.get("scope") == "user"),
        entries[0] if entries else None,
    )
    if user_entry is None:
        return True, (
            "skipped: lore@lore not present in Claude plugin cache "
            "(install via `/plugin` in Claude Code)"
        )

    cache_version = str(user_entry.get("version") or "").strip()
    if not cache_version or cache_version == "unknown":
        return True, (
            f"Claude plugin cache version is `{cache_version or 'missing'}` — "
            "skipping comparison"
        )

    try:
        installed = version("lore")
    except PackageNotFoundError:
        return False, "lore Python package not installed in this environment"

    if cache_version == installed:
        return True, f"Claude plugin cache {cache_version} matches pip"

    return False, (
        f"Claude plugin cache is {cache_version} but pip-installed lore is "
        f"{installed}. SessionStart's banner will report `{installed}` "
        "until you upgrade the binary. Run: pipx upgrade lore "
        "(or `pipx install --force --editable <path-to-lore-repo>`), then "
        "restart Claude Code."
    )


def _check_recent_crashes(cwd: str) -> Check:
    """Report hook crashes recorded in the last 7 days.

    Crash logs are written by `lore_cli._crash_log.write_crash` whenever
    a hook escapes ``_shield_hook`` or the top-level ``main()`` backstop
    catches an unexpected exception. Surfacing them here gives the user
    a single place to discover silent failures Claude Code suppressed
    behind the friendly banner.

    Advisory only — recent crashes don't fail the install (the user
    might already know about them and be working on a fix).
    """
    from lore_cli._crash_log import recent_crashes

    crashes = recent_crashes(within_days=7)
    if not crashes:
        return True, "no hook crashes in last 7 days"
    latest = crashes[0]
    return False, (
        f"{len(crashes)} hook crash(es) in last 7 days; latest: {latest}"
    )


def _check_spine_writable(cwd: str) -> Check:
    """Surface a spine write-failure degrade marker, if present.

    The event spine (`lore_core.spine.SpineWriter`) is best-effort and
    never raises on the hot path; a failed write touches
    ``.lore/spine-failed.marker`` instead. Doctor reads that marker so a
    telemetry blackout (full disk, bad permissions) is not itself silent.

    Advisory only — the marker records a *past* failure; the writer
    retries on the next event.
    """
    from datetime import UTC, datetime

    from lore_core.config import get_lore_root

    marker = get_lore_root() / ".lore" / "spine-failed.marker"
    if not marker.exists():
        return True, "event spine writes OK"
    try:
        mtime = datetime.fromtimestamp(marker.stat().st_mtime, tz=UTC)
        when = mtime.isoformat().replace("+00:00", "Z")
    except OSError:
        when = "unknown time"
    return False, (
        f"spine write failed (last at {when}) \u2014 check disk / permissions; "
        f"clear {marker} once resolved"
    )


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


def _check_vale(cwd: str) -> Check:
    """Vale is PATH-detected, not bundled — absence degrades the
    writing-rules lint to instruction-only and never blocks (ADR 0006)."""
    path = shutil.which("vale")
    if path is None:
        return False, "vale not on PATH — writing-rules lint is instruction-only until installed"
    return True, f"vale found at {path}"


def _check_attach(cwd: str) -> Check:
    from lore_core.session import _resolve_attach_block

    cfg = _resolve_attach_block(Path(cwd))
    if cfg is None:
        return True, f"no attachment covers {cwd} (skipped)"
    path, block = cfg
    return True, f"attached at {path.parent}: wiki={block.get('wiki')}"


def _check_attachments(cwd: str) -> Check:
    """Validate every attachments.json row: path exists, wiki dir exists,
    scope in scopes.json (and its registered wiki matches the attachment's),
    fingerprint matches current `.lore.yml` if one exists at the
    attachment path.
    """
    from lore_core.config import get_lore_root, get_wiki_root
    from lore_core.offer import FILENAME as LORE_YML
    from lore_core.offer import offer_fingerprint, parse_lore_yml
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
        else:
            resolved_wiki = sf.resolve_wiki(a.scope)
            if resolved_wiki != a.wiki:
                issues.append(
                    f"{a.path}: wiki `{a.wiki}` doesn't match scope registry "
                    f"(scope `{a.scope}` resolves to wiki `{resolved_wiki}`)"
                )
        # Fingerprint check — only when a .lore.yml is present at the attachment root
        if a.offer_fingerprint is not None:
            lore_yml = a.path / LORE_YML
            if lore_yml.exists():
                offer = parse_lore_yml(lore_yml)
                if offer is not None and offer_fingerprint(offer) != a.offer_fingerprint:
                    issues.append(f"{a.path}: .lore.yml fingerprint drift (run `lore attach accept`)")

    return _issue_summary(f"{total} attachment(s)", issues, f"{total} attachment(s), all valid")


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

    return _issue_summary(f"{len(ids)} scope(s)", issues, f"{len(ids)} scope(s), tree healthy")


def _check_ledger_buckets(cwd: str) -> Check:
    """Surface the ledger's __orphan__/__unattached__ buckets as
    actionable informational output. Never fails — these are not errors,
    they're surfaces the user may want to act on via
    `lore attach attachments purge-unattached`.
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
        parts.append(f"{unattached} unattached (run `lore attach attachments purge-unattached`)")
    return True, " · ".join(parts)


def _check_pending(cwd: str) -> Check:
    """Report registered transcripts per attached wiki.

    A pending entry is a transcript lore has recorded and stamped with
    linkage. Nothing consumes the pending set — there is no gate to be
    waiting on and no backlog to drain — so this reports a count, not a
    verdict. Never fails the install.
    """
    from lore_core.config import get_lore_root
    from lore_core.ledger import TranscriptLedger

    lore_root = get_lore_root()
    ledger_path = lore_root / ".lore" / "transcript-ledger.json"
    if not ledger_path.exists():
        return True, "no pending — capture hasn't fired yet"

    try:
        buckets = TranscriptLedger(lore_root).pending_by_wiki()
    except Exception as e:
        return False, f"ledger read failed: {e}"

    parts = [
        f"{wiki_name}: {len(entries)} registered"
        for wiki_name, entries in sorted(buckets.items())
        if not wiki_name.startswith("__") and entries
    ]
    if not parts:
        return True, "no attached-wiki pending"
    return True, " · ".join(parts)


# ---------------------------------------------------------------------------
# `--fix` repairs.
#
# Each repair is self-contained: it reads state, prints what it would
# change, asks for consent (`typer.confirm`, skippable with `--yes`), and
# only writes after consent. attachments.json is the non-regenerable
# consent record (docs/architecture/state.md) — no repair here may ever
# drop a row, only rebuild scopes.json or update fields on an existing
# row in place.
# ---------------------------------------------------------------------------


def _fix_rebuild_scopes(lore_root: Path, *, yes: bool) -> bool:
    """Rebuild scopes.json from every accepted attachment.

    scopes.json is regenerable; re-derives each attachment's scope chain
    via the same `ingest_chain` `lore attach accept` uses, additively
    repairing missing or corrupt entries (a corrupt file parses to empty,
    so this also self-heals a corrupt scopes.json). Mutation stays
    in-memory until confirmed — a decline leaves the file untouched.

    ponytail: additive only, doesn't prune scope entries whose attachment
    was later removed. Add a prune pass if orphaned scopes become a
    problem in practice.
    """
    from lore_core.state.attachments import AttachmentsFile
    from lore_core.state.scopes import ScopeConflict, ScopesFile

    af = AttachmentsFile(lore_root)
    af.load()
    attachments = af.all()
    if not attachments:
        return False

    sf = ScopesFile(lore_root)
    sf.load()
    before_ids = set(sf.all_ids())

    conflicts: list[str] = []
    for a in attachments:
        try:
            sf.ingest_chain(a.scope, a.wiki)
        except ScopeConflict as exc:
            conflicts.append(str(exc))

    added = sorted(set(sf.all_ids()) - before_ids)
    if not added and not conflicts:
        return False

    console.print("\n[bold]Repair: rebuild scopes.json[/bold] from attachments.json")
    for sid in added:
        console.print(f"  [green]+ {sid}[/green]")
    for c in conflicts:
        console.print(f"  [yellow]! {c} (skipped)[/yellow]")

    if not yes and not typer.confirm("Apply?", default=False):
        console.print("  [yellow]declined — no changes written[/yellow]")
        return False

    sf.save()
    console.print("  [green]written[/green]")
    return True


def _fix_restamp_fingerprints(lore_root: Path, *, yes: bool) -> bool:
    """Re-stamp offer_fingerprint for attachments whose `.lore.yml` has
    drifted, after showing what would change and getting consent.

    Mirrors `lore attach accept`'s re-acceptance: re-parses the current
    offer and updates wiki/scope/offer_fingerprint to match it. Never
    drops the attachment row — only updates fields in place.
    """
    from lore_core.offer import FILENAME as LORE_YML
    from lore_core.offer import offer_fingerprint, parse_lore_yml
    from lore_core.state.attachments import Attachment, AttachmentsFile
    from lore_core.state.scopes import ScopeConflict, ScopesFile

    af = AttachmentsFile(lore_root)
    af.load()
    sf = ScopesFile(lore_root)
    sf.load()

    changed = False
    for a in af.all():
        if a.offer_fingerprint is None:
            continue
        lore_yml = a.path / LORE_YML
        if not lore_yml.exists():
            continue
        offer = parse_lore_yml(lore_yml)
        if offer is None:
            continue
        new_fp = offer_fingerprint(offer)
        if new_fp == a.offer_fingerprint:
            continue

        console.print(f"\n[bold]Repair: re-stamp offer fingerprint[/bold] for {a.path}")
        console.print(f"  wiki:        {a.wiki} -> {offer.wiki}")
        console.print(f"  scope:       {a.scope} -> {offer.scope}")
        console.print(f"  fingerprint: {a.offer_fingerprint} -> {new_fp}")

        if not yes and not typer.confirm("Apply?", default=False):
            console.print("  [yellow]declined — no changes written[/yellow]")
            continue

        try:
            sf.ingest_chain(offer.scope, offer.wiki)
        except ScopeConflict as exc:
            console.print(f"  [red]scope conflict: {exc} — skipped[/red]")
            continue

        af.add(
            Attachment(
                path=a.path,
                wiki=offer.wiki,
                scope=offer.scope,
                attached_at=a.attached_at,
                source=a.source,
                offer_fingerprint=new_fp,
            )
        )
        changed = True
        console.print("  [green]re-stamped[/green]")

    if changed:
        af.save()
        sf.save()
    return changed


def _fix_migrate_paths(lore_root: Path, old_prefix: str, new_prefix: str, *, yes: bool) -> bool:
    """Rewrite attachment paths after a vault/repo move.

    Any attachment path under `old_prefix` is rewritten to the same
    relative path under `new_prefix`. attachments.json is host-local and
    not portable (docs/architecture/state.md) — this is the explicit
    migration path when a host's paths changed but its consent records
    should carry over.
    """
    from lore_core.state.attachments import Attachment, AttachmentsFile

    old_path = Path(old_prefix)
    new_path = Path(new_prefix)

    af = AttachmentsFile(lore_root)
    af.load()

    rewrites: list[tuple[Attachment, Path]] = []
    for a in af.all():
        try:
            rel = a.path.relative_to(old_path)
        except ValueError:
            continue
        rewrites.append((a, new_path / rel))

    if not rewrites:
        return False

    console.print(f"\n[bold]Repair: migrate attachment paths[/bold] {old_prefix} -> {new_prefix}")
    for a, new in rewrites:
        console.print(f"  {a.path} -> {new}")

    if not yes and not typer.confirm("Apply?", default=False):
        console.print("  [yellow]declined — no changes written[/yellow]")
        return False

    for a, new in rewrites:
        af.remove(a.path)
        af.add(
            Attachment(
                path=new,
                wiki=a.wiki,
                scope=a.scope,
                attached_at=a.attached_at,
                source=a.source,
                offer_fingerprint=a.offer_fingerprint,
            )
        )
    af.save()
    console.print("  [green]migrated[/green]")
    return True


def _run_repairs(*, yes: bool, migrate_path_from: str | None, migrate_path_to: str | None) -> None:
    """Entry point for `--fix`. Runs before the check pass so a single
    invocation both repairs and reports the post-repair state."""
    from lore_core.config import get_lore_root

    lore_root = get_lore_root()
    if not lore_root.exists():
        return

    _fix_rebuild_scopes(lore_root, yes=yes)
    _fix_restamp_fingerprints(lore_root, yes=yes)
    if migrate_path_from and migrate_path_to:
        _fix_migrate_paths(lore_root, migrate_path_from, migrate_path_to, yes=yes)


# ---------------------------------------------------------------------------
# Cursor integration checks (advisory — `fails_run=False`)
#
# All three checks are gated on ``~/.cursor/`` existing. We use the
# directory marker — not ``shutil.which("cursor")`` — because most
# Linux/macOS users install Cursor as a GUI .deb / .AppImage / .dmg
# without a CLI on PATH, and we'd silently miss them otherwise.
# ---------------------------------------------------------------------------


def _cursor_installed() -> bool:
    """True when Cursor is installed on this host (any flavor)."""
    return (Path.home() / ".cursor").is_dir()


def _check_cursor_plugin_dir(cwd: str) -> Check:
    """Plugin dir exists with sentinel + manifest version matches lore."""
    if not _cursor_installed():
        return True, "skipped: ~/.cursor not present"

    from importlib.metadata import PackageNotFoundError, version

    from lore_core.install._helpers import cursor_plugin_dir
    from lore_core.managed_files import PLUGIN_SENTINEL

    plugin_dir = cursor_plugin_dir()
    if not plugin_dir.exists():
        return False, (
            f"{plugin_dir} not present — run `lore install --integration cursor` "
            "to materialize the plugin"
        )
    if not (plugin_dir / PLUGIN_SENTINEL).exists():
        return False, (
            f"{plugin_dir} missing {PLUGIN_SENTINEL} sentinel — directory may "
            "predate plugin packaging; reinstall with --force"
        )
    manifest_path = plugin_dir / ".cursor-plugin" / "plugin.json"
    if not manifest_path.exists():
        return False, f"{manifest_path} not present — reinstall to refresh"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        return False, f"plugin manifest unreadable: {exc}"
    plugin_version = str(manifest.get("version") or "").strip()
    if not plugin_version:
        return False, "plugin manifest has no `version` field"
    try:
        installed = version("lore")
    except PackageNotFoundError:
        return False, "lore Python package not installed in this environment"
    if plugin_version != installed:
        return False, (
            f"plugin manifest {plugin_version} != installed pip {installed} — "
            "run `lore install --integration cursor` after each pip upgrade"
        )
    return True, f"plugin {plugin_version} matches pip"


def _check_cursor_mcp_command_resolves(cwd: str) -> Check:
    """Plugin-local mcp.json `command` resolves to an existing executable.

    Catches the sticky-abs-path footgun: at install time the lore CLI
    is at one absolute path; after a pipx reinstall (different venv
    UUID) or a binary move the path goes stale and Cursor silently
    fails to spawn the MCP server with no Cursor-side error message.
    """
    if not _cursor_installed():
        return True, "skipped: ~/.cursor not present"

    from lore_core.install._helpers import cursor_plugin_dir

    plugin_dir = cursor_plugin_dir()
    mcp_path = plugin_dir / "mcp.json"
    if not mcp_path.exists():
        return False, f"{mcp_path} not present — reinstall to materialize"
    try:
        data = json.loads(mcp_path.read_text())
    except (OSError, ValueError) as exc:
        return False, f"plugin-local mcp.json unreadable: {exc}"
    entry = (data.get("mcpServers") or {}).get("lore") or {}
    cmd = entry.get("command")
    if not cmd:
        return False, "mcpServers.lore has no `command` field"
    cmd_path = Path(cmd)
    if not cmd_path.is_absolute():
        return False, (
            f"command is relative ({cmd!r}) — Cursor's GUI subprocess "
            "PATH does not include ~/.local/bin; reinstall to fix"
        )
    if not cmd_path.exists():
        return False, (
            f"command points to {cmd} which no longer exists — "
            "after `pipx upgrade lore`, run `lore install --integration cursor`"
        )
    if not os.access(cmd_path, os.X_OK):
        return False, f"command {cmd} is not executable"
    return True, f"mcp command {cmd} resolves"


def _check_cursor_hooks_config(cwd: str) -> Check:
    """Plugin-local hooks.json parses with the expected event coverage."""
    if not _cursor_installed():
        return True, "skipped: ~/.cursor not present"

    from lore_core.install._helpers import cursor_plugin_dir

    plugin_dir = cursor_plugin_dir()
    hooks_path = plugin_dir / "hooks.json"
    if not hooks_path.exists():
        return False, f"{hooks_path} not present — reinstall to materialize"
    try:
        data = json.loads(hooks_path.read_text())
    except (OSError, ValueError) as exc:
        return False, f"hooks.json unreadable: {exc}"
    if data.get("version") != 1:
        return False, f"hooks.json version is {data.get('version')!r} (expected 1)"
    hooks = data.get("hooks") or {}
    if not hooks:
        return False, "hooks.json has empty hooks block"
    return True, f"{len(hooks)} event(s) wired: {', '.join(sorted(hooks))}"


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
    # Surfaces curator A spawn-gate state per wiki. Tells the user
    # "why isn't curator A firing?" at a glance.
    ("pending", _check_pending, False),
    ("SessionStart hook", _check_hook_runnable, True),
    # Advisory: surfaces a past event-spine write failure via its degrade
    # marker so a telemetry blackout is not itself silent.
    ("event spine", _check_spine_writable, False),
    # Advisory: surfaces silent failures Claude Code hides behind the
    # friendly hook banner. Doesn't fail the install — the user may
    # already be triaging.
    ("recent crashes", _check_recent_crashes, False),
    # Advisory: version drift is informational. The CLI still functions
    # at the older version; the user just sees a stale SessionStart line.
    ("CLI version", _check_lore_version_drift, False),
    # Advisory: plugin-cache drift only matters once the user has
    # upgraded pip; the message itself is the value (it tells them
    # to run /plugin update lore).
    ("plugin manifest", _check_plugin_manifest_sync, False),
    # Failing (was advisory): catches the inverse — user ran `claude
    # plugin update` but didn't refresh the pipx binary. Banner silently
    # reports the old version; this names the fix command. The drift is
    # common enough in practice that it should block a green `doctor` run.
    ("plugin cache", _check_claude_plugin_cache_drift, True),
    # Cursor integration checks. All advisory: a Claude-only user has
    # no ~/.cursor dir and these all skip cleanly.
    ("cursor plugin", _check_cursor_plugin_dir, False),
    ("cursor mcp", _check_cursor_mcp_command_resolves, False),
    ("cursor hooks", _check_cursor_hooks_config, False),
    ("attach", _check_attach, False),
    ("vale", _check_vale, False),
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
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Repair fixable issues: rebuild scopes.json, re-stamp drifted "
        "offer fingerprints, migrate attachment paths. Prompts per repair "
        "unless --yes.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Skip repair confirmation prompts (non-interactive --fix).",
    ),
    migrate_path_from: str = typer.Option(
        None,
        "--migrate-path-from",
        help="Old absolute path prefix to rewrite in attachments.json (used with --fix).",
    ),
    migrate_path_to: str = typer.Option(
        None,
        "--migrate-path-to",
        help="New absolute path prefix (used with --fix).",
    ),
) -> None:
    """Walk the most common breakage points and print one line per check."""
    cwd = cwd or os.getcwd()

    if fix:
        _run_repairs(yes=yes, migrate_path_from=migrate_path_from, migrate_path_to=migrate_path_to)

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
