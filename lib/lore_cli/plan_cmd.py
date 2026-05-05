"""``lore plan`` — list / delete / import / step / advance plan notes.

Five subcommands:

  lore plan list      — enumerate active plans (optionally filtered by repo).
  lore plan delete    — remove a plan note (refuses on incoming wikilinks
                        without ``--force``; confirms on ``status: active``).
  lore plan import    — re-ingest an orphan-dumped JSON envelope OR a raw
                        markdown file (e.g. historical ``~/.claude/plans/``).
                        Modes are explicit: ``--from-orphan`` / ``--from-markdown``;
                        bare invocation dispatches by extension.
  lore plan step      — set ``step_status[<step_id>]`` atomically.
  lore plan advance   — sugar: mark the current in-progress step done; else
                        the next pending step.

Hook-side capture is ``lore hook plan-capture`` (lives in ``hooks.py`` for
dispatcher consistency).
"""
from __future__ import annotations

import json
import sys
from datetime import date as _date
from pathlib import Path

import typer

from lore_cli._argv_compat import argv_main

app = typer.Typer(
    add_completion=False,
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emit_json(envelope: dict) -> None:
    print(json.dumps(envelope, indent=2))


def _resolve_wiki(target_wiki: str | None) -> Path:
    """Resolve the wiki root path. Raises typer.Exit on ambiguity."""
    from lore_core.config import get_wiki_root

    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        typer.echo(f"lore: vault root not found: {wiki_root}", err=True)
        raise typer.Exit(code=1)
    if target_wiki:
        candidate = wiki_root / target_wiki
        if not candidate.exists():
            typer.echo(f"lore: wiki not found: {target_wiki}", err=True)
            raise typer.Exit(code=1)
        return candidate
    wikis = [p for p in sorted(wiki_root.iterdir()) if p.is_dir()]
    if len(wikis) == 1:
        return wikis[0]
    if not wikis:
        typer.echo(f"lore: no wikis under {wiki_root}", err=True)
        raise typer.Exit(code=1)
    typer.echo(
        "lore: multiple wikis present — pass --wiki <name>: "
        + ", ".join(p.name for p in wikis),
        err=True,
    )
    raise typer.Exit(code=1)


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _confirm(prompt: str, *, default: bool = False) -> bool:
    """y/n prompt. Non-interactive → return ``default``."""
    if not _is_interactive():
        return default
    suffix = " [y/N]" if not default else " [Y/n]"
    try:
        ans = input(prompt + suffix + " ").strip().lower()
    except EOFError:
        return default
    if not ans:
        return default
    return ans.startswith("y")


# ---------------------------------------------------------------------------
# `lore plan list`
# ---------------------------------------------------------------------------


@app.command("list")
def cmd_list(
    wiki: str = typer.Option(None, "--wiki", help="Wiki name (default: only-wiki)."),
    repo: str = typer.Option(None, "--repo", help="Filter by repo slug (org/name)."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON envelope."),
) -> None:
    """List active plans, ranked by recency."""
    from lore_core.plans.registry import list_active

    wiki_root = _resolve_wiki(wiki)
    cards = list_active(wiki_root, repo=repo)

    if json_out:
        _emit_json({
            "schema": "lore.plan.list/1",
            "data": {
                "wiki": wiki_root.name,
                "repo": repo,
                "plans": [
                    {
                        "slug": c.slug,
                        "description": c.description,
                        "status": c.status,
                        "repo": c.repo,
                        "steps_total": c.steps_total,
                        "steps_done": c.steps_done,
                        "steps_in_progress": c.steps_in_progress,
                        "next_pending_step": c.next_pending_step(),
                        "last_reviewed": c.last_reviewed,
                    }
                    for c in cards
                ],
            },
        })
        return

    if not cards:
        scope_hint = f" for repo `{repo}`" if repo else ""
        print(f"(no active plans{scope_hint})")
        return
    for c in cards:
        line = f"{c.slug} · {c.steps_done}/{c.steps_total} done"
        if c.steps_in_progress:
            line += f" · in-progress: {', '.join(c.steps_in_progress)}"
        np = c.next_pending_step()
        if np:
            line += f" · next: {np}"
        if c.repo:
            line += f"  ({c.repo})"
        print(line)
        if c.description:
            print(f"  {c.description}")


# ---------------------------------------------------------------------------
# `lore plan delete`
# ---------------------------------------------------------------------------


@app.command("delete")
def cmd_delete(
    slug: str = typer.Argument(..., help="Plan slug to delete."),
    wiki: str = typer.Option(None, "--wiki", help="Wiki name (default: only-wiki)."),
    force: bool = typer.Option(
        False, "--force", help="Skip the active-status confirm and incoming-link refuse."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON envelope."),
) -> None:
    """Delete a plan note. Refuses if other notes wikilink to it (use --force)."""
    from lore_core.plans.registry import read_one, scan_incoming_wikilinks
    from lore_core.plans.writer import plan_path

    wiki_root = _resolve_wiki(wiki)
    target = plan_path(wiki_root, slug)
    if not target.exists():
        typer.echo(f"lore: plan not found: {slug}", err=True)
        raise typer.Exit(code=1)

    card = read_one(wiki_root, slug)

    if not force:
        # Refuse on incoming wikilinks to avoid silent rug-pulls.
        incoming = scan_incoming_wikilinks(wiki_root, slug)
        if incoming:
            typer.echo(
                f"lore: refusing — {len(incoming)} note(s) wikilink to plan/{slug}; "
                "rerun with --force to delete anyway:",
                err=True,
            )
            for p in incoming:
                typer.echo(f"  - {p}", err=True)
            raise typer.Exit(code=2)

        # Confirm only when status is active (cleanup of done/abandoned is friction-free).
        if card and card.status == "active":
            if not _confirm(
                f"Delete active plan {slug}? This cannot be undone (use git to recover).",
                default=False,
            ):
                typer.echo("aborted", err=True)
                raise typer.Exit(code=1)

    target.unlink()
    msg = f"deleted {target}"
    hint = (
        f"commit when ready: cd {wiki_root.parent.parent} && "
        f"git add -A && git commit -m 'drop plan {slug}'"
    )
    if json_out:
        _emit_json({
            "schema": "lore.plan.delete/1",
            "data": {"slug": slug, "path": str(target), "commit_hint": hint},
        })
    else:
        print(msg)
        print(hint)


# ---------------------------------------------------------------------------
# `lore plan import`
# ---------------------------------------------------------------------------


@app.command("import")
def cmd_import(
    path: Path = typer.Argument(..., help="Path to orphan JSON envelope or markdown plan."),
    from_orphan: bool = typer.Option(
        False, "--from-orphan", help="Treat <path> as a JSON orphan envelope (recovery)."
    ),
    from_markdown: bool = typer.Option(
        False,
        "--from-markdown",
        help="Treat <path> as raw plan markdown (e.g. ~/.claude/plans/foo.md).",
    ),
    wiki: str = typer.Option(None, "--wiki"),
    repo: str = typer.Option(None, "--repo"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Import a plan from an orphan dump (JSON envelope) or raw markdown.

    Bare invocation (neither flag) dispatches by file extension:

    * ``.json`` → ``--from-orphan``
    * ``.md`` / ``.markdown`` → ``--from-markdown``
    * anything else → hard error (use one of the explicit flags)
    """
    if from_orphan and from_markdown:
        typer.echo("lore: pass at most one of --from-orphan / --from-markdown", err=True)
        raise typer.Exit(code=2)

    if not from_orphan and not from_markdown:
        suffix = path.suffix.lower()
        if suffix == ".json":
            from_orphan = True
        elif suffix in (".md", ".markdown"):
            from_markdown = True
        else:
            typer.echo(
                f"lore: ambiguous file type {suffix!r}; pass --from-orphan or --from-markdown",
                err=True,
            )
            raise typer.Exit(code=2)

    if not path.exists():
        typer.echo(f"lore: not found: {path}", err=True)
        raise typer.Exit(code=1)

    if from_orphan:
        plan_text = _extract_plan_from_orphan(path)
    else:
        plan_text = path.read_text()

    from lore_core.plans.parser import parse
    from lore_core.plans.writer import compute_source_hash, write_plan_note

    wiki_root = _resolve_wiki(wiki)
    plan = parse(plan_text)
    result = write_plan_note(
        wiki_root=wiki_root,
        plan=plan,
        source_hash=compute_source_hash(plan_text),
        source_adapter="manual-import" if from_markdown else "claude-code-orphan",
        repo=repo,
    )
    if json_out:
        _emit_json({
            "schema": "lore.plan.import/1",
            "data": {
                "slug": result.slug,
                "path": str(result.path),
                "outcome": result.outcome,
                "step_count": result.step_count,
            },
        })
    else:
        print(f"{result.outcome}: {result.path}")


def _extract_plan_from_orphan(path: Path) -> str:
    """Read the orphan JSON envelope and pull the plan markdown out."""
    from lore_core.plans.parser import parse_payload

    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        typer.echo(f"lore: orphan JSON malformed: {e}", err=True)
        raise typer.Exit(code=1)
    plan_text, source_field = parse_payload(payload)
    if plan_text is None:
        typer.echo(
            f"lore: no plan markdown found in orphan payload (source_field={source_field})",
            err=True,
        )
        raise typer.Exit(code=1)
    return plan_text


# ---------------------------------------------------------------------------
# `lore plan file` — primary producer-facing capture command
# ---------------------------------------------------------------------------


@app.command("file")
def cmd_file(
    path: Path = typer.Argument(
        ..., help="Path to envelope JSON (with --json) or markdown."
    ),
    json_envelope: bool = typer.Option(
        False, "--json", help="Treat <path> as a `lore.plan.envelope/1` JSON envelope."
    ),
    wiki: str = typer.Option(None, "--wiki"),
    repo: str = typer.Option(None, "--repo"),
    json_out: bool = typer.Option(False, "--json-out", help="Emit a JSON report."),
) -> None:
    """File a plan via the structured envelope path.

    The envelope (``lore.plan.envelope/1``) is the canonical interop
    contract for tools that can emit JSON — Cursor, Aider, Cline,
    custom CI scripts, etc. Producers skip markdown shape detection
    entirely; they construct the canonical IR directly and lore
    validates + writes.

    Markdown plans (legacy/recovery path) still go via
    ``lore plan import``.
    """
    if not json_envelope:
        typer.echo(
            "lore: pass --json <path> to file an envelope plan; "
            "use `lore plan import` for markdown",
            err=True,
        )
        raise typer.Exit(code=2)

    if not path.exists():
        typer.echo(f"lore: not found: {path}", err=True)
        raise typer.Exit(code=1)

    from lore_core.plans.envelope import EnvelopeError
    from lore_core.plans.ingest import IngestSource, ingest_plan
    from lore_core.plans.writer import compute_source_hash, write_plan_note

    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        typer.echo(f"lore: envelope read failed: {e}", err=True)
        raise typer.Exit(code=1)

    try:
        result = ingest_plan(
            IngestSource(kind="envelope", payload=payload, producer="cli")
        )
    except EnvelopeError as e:
        typer.echo(f"lore: envelope schema error: {e}", err=True)
        raise typer.Exit(code=1)

    wiki_root = _resolve_wiki(wiki)
    canonical_text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    write_result = write_plan_note(
        wiki_root=wiki_root,
        plan=result.plan,
        source_hash=compute_source_hash(canonical_text),
        source_adapter="envelope-cli",
        repo=repo,
    )

    if json_out:
        _emit_json({
            "schema": "lore.plan.file/1",
            "data": {
                "slug": write_result.slug,
                "path": str(write_result.path),
                "outcome": write_result.outcome,
                "step_count": write_result.step_count,
                "confidence": result.confidence,
                "adapter": result.adapter_name,
            },
        })
    else:
        print(f"{write_result.outcome}: {write_result.path}")


# ---------------------------------------------------------------------------
# `lore plan migrate-ids` — one-shot legacy ``s<N>`` → canonical ``step-<N>``
# ---------------------------------------------------------------------------


def _migrate_one_plan(path: Path, *, dry_run: bool) -> dict:
    """Migrate one plan file in place. Returns a per-file report dict.

    Idempotent: a plan that's already canonical is read once, found
    unchanged, and not rewritten — preserving mtime. Files that fail
    to parse (malformed YAML, missing frontmatter, ``type != "plan"``)
    are reported as ``skipped`` and left untouched — the migration
    walks the rest of the vault rather than aborting.
    """
    from lore_core.io import atomic_write_text
    from lore_core.plans import canonical
    from lore_core.schema import parse_frontmatter, strip_frontmatter

    report: dict = {
        "path": str(path),
        "headings_rewritten": 0,
        "status_keys_rewritten": 0,
        "changed": False,
        "skipped": False,
        "skip_reason": None,
    }

    try:
        text = path.read_text()
        fm = parse_frontmatter(text)
    except (OSError, ValueError, Exception) as e:  # noqa: BLE001
        report["skipped"] = True
        report["skip_reason"] = f"parse_failed: {type(e).__name__}"
        return report

    if not isinstance(fm, dict) or not fm:
        report["skipped"] = True
        report["skip_reason"] = "no_frontmatter"
        return report

    if fm.get("type") != "plan":
        report["skipped"] = True
        report["skip_reason"] = "not_a_plan"
        return report

    body = strip_frontmatter(text)
    body_after, body_rewrites = canonical.migrate_legacy_body(body)
    fm_rewrites = canonical.migrate_legacy_step_status(fm)

    report["headings_rewritten"] = body_rewrites
    report["status_keys_rewritten"] = fm_rewrites
    report["changed"] = bool(body_rewrites or fm_rewrites)

    if not report["changed"] or dry_run:
        return report

    # Re-render frontmatter using yaml.safe_dump (consistent with writer).
    # ``allow_unicode=True`` matches writer._render_markdown — without it,
    # non-ASCII description text gets backslash-escaped.
    import yaml as _yaml

    new_text = (
        "---\n"
        + _yaml.safe_dump(
            fm, default_flow_style=False, sort_keys=False, allow_unicode=True
        ).strip()
        + "\n---\n\n"
        + body_after.lstrip("\n")
    )
    atomic_write_text(path, new_text)
    return report


@app.command("migrate-ids")
def cmd_migrate_ids(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report changes without writing."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit a JSON report."),
) -> None:
    """Rewrite legacy ``s<N>`` step IDs to canonical ``step-<N>`` across the vault.

    Walks every wiki under ``$LORE_ROOT/wiki/`` and rewrites:
      * Body headings ``### s<N>: …`` → ``### step-<N>: …``
      * Frontmatter ``step_status`` / ``step_status_updated`` keys

    Idempotent — already-canonical plans are untouched (mtime preserved).
    The same logic runs piecemeal during plan re-capture; this command
    is the standalone path for vaults that won't re-capture every plan.
    """
    from lore_core.config import get_lore_root

    lore_root = get_lore_root()
    if lore_root is None or not lore_root.exists():
        typer.echo("lore: $LORE_ROOT not configured", err=True)
        raise typer.Exit(code=1)

    wiki_root = lore_root / "wiki"
    if not wiki_root.is_dir():
        typer.echo(f"lore: no wikis under {wiki_root}", err=True)
        raise typer.Exit(code=1)

    reports: list[dict] = []
    for wiki_dir in sorted(wiki_root.iterdir()):
        if not wiki_dir.is_dir():
            continue
        plans_dir = wiki_dir / "plans"
        if not plans_dir.is_dir():
            continue
        for plan_path in sorted(plans_dir.glob("*.md")):
            reports.append(_migrate_one_plan(plan_path, dry_run=dry_run))

    changed = [r for r in reports if r["changed"]]
    skipped = [r for r in reports if r.get("skipped")]
    if json_out:
        _emit_json({
            "schema": "lore.plan.migrate-ids/1",
            "data": {
                "dry_run": dry_run,
                "scanned": len(reports),
                "changed": len(changed),
                "skipped": len(skipped),
                "reports": reports,
            },
        })
        return

    verb = "would migrate" if dry_run else "migrated"
    if not changed and not skipped:
        typer.echo(f"lore: scanned {len(reports)} plans — none needed migration")
        return
    for r in changed:
        slug = Path(r["path"]).stem
        typer.echo(
            f"  {verb} {slug}: "
            f"{r['headings_rewritten']} headings, "
            f"{r['status_keys_rewritten']} step_status keys"
        )
    for r in skipped:
        slug = Path(r["path"]).stem
        typer.echo(f"  skipped {slug}: {r['skip_reason']}")
    summary_parts: list[str] = []
    if changed:
        summary_parts.append(f"{verb} {len(changed)}")
    if skipped:
        summary_parts.append(f"skipped {len(skipped)}")
    typer.echo(
        f"lore: {' / '.join(summary_parts)} of {len(reports)} plans scanned"
    )


# ---------------------------------------------------------------------------
# `lore plan migrate-step-files` — re-extract step_files from plan bodies
# ---------------------------------------------------------------------------


_INFERENCE_CONFIDENCE_FLOOR = 0.5
_DEFAULT_INFERENCE_MODEL = "claude-haiku-4-5-20251001"


def _write_step_files(path: Path, fm: dict, body: str, merged: dict[str, list[str]]) -> None:
    """Atomically rewrite a plan's frontmatter with new step_files."""
    from lore_core.io import atomic_write_text
    import yaml as _yaml

    fm_new = dict(fm)
    fm_new["step_files"] = merged
    new_text = (
        "---\n"
        + _yaml.safe_dump(
            fm_new, default_flow_style=False, sort_keys=False, allow_unicode=True
        ).strip()
        + "\n---\n\n"
        + body.lstrip("\n")
    )
    atomic_write_text(path, new_text)


def _extract_step_files_one_plan(
    path: Path,
    *,
    dry_run: bool,
    use_llm: bool = False,
    confidence_floor: float = _INFERENCE_CONFIDENCE_FLOOR,
    model: str | None = None,
) -> dict:
    """Re-extract ``step_files`` from a single plan's body.

    Two-tier extraction:

    1. **Deterministic** — runs the canonical parser, which honours
       ``Files:`` directives in any supported shape (backtick-wrapped,
       annotated bullets, comma-list inline).
    2. **LLM-judged** (when ``use_llm=True``) — falls back to one
       :func:`infer_step_files` call when the parser returns nothing
       *or* the existing ``step_files`` frontmatter has gaps. Per-step
       confidence below ``confidence_floor`` is dropped.

    Merges the result conservatively: a step that already has a
    non-empty file list in frontmatter is left alone; only missing or
    empty entries are populated.
    """
    from lore_core.plans.parser import parse
    from lore_core.schema import parse_frontmatter, strip_frontmatter

    report: dict = {
        "path": str(path),
        "steps_total": 0,
        "steps_with_files": 0,
        "frontmatter_steps_added": 0,
        "frontmatter_steps_kept": 0,
        "changed": False,
        "skipped": False,
        "skip_reason": None,
        "source": None,  # "parser" | "llm" | None
        "llm_low_confidence_dropped": 0,
    }

    try:
        text = path.read_text()
        fm = parse_frontmatter(text)
    except (OSError, ValueError, Exception) as e:  # noqa: BLE001
        report["skipped"] = True
        report["skip_reason"] = f"parse_failed: {type(e).__name__}"
        return report

    if not isinstance(fm, dict) or fm.get("type") != "plan":
        report["skipped"] = True
        report["skip_reason"] = "not_a_plan"
        return report

    try:
        plan = parse(text)
    except Exception as e:  # noqa: BLE001
        report["skipped"] = True
        report["skip_reason"] = f"parser_failed: {type(e).__name__}"
        return report

    report["steps_total"] = len(plan.steps)
    report["steps_with_files"] = sum(1 for s in plan.steps if s.files)

    existing_step_files = fm.get("step_files") or {}
    if not isinstance(existing_step_files, dict):
        existing_step_files = {}

    merged: dict[str, list[str]] = {}
    added = 0
    kept = 0
    needs_llm: list[str] = []
    for step in plan.steps:
        prior = existing_step_files.get(step.id)
        # An explicitly-recorded entry (even empty list) means a prior
        # run resolved this step — preserve it. Only steps with no
        # entry at all count as gaps that need filling.
        if isinstance(prior, list) and step.id in existing_step_files:
            merged[step.id] = list(prior)
            kept += 1
        elif step.files:
            merged[step.id] = list(step.files)
            added += 1
        else:
            needs_llm.append(step.id)

    report["frontmatter_steps_added"] = added
    report["frontmatter_steps_kept"] = kept

    # If parser found everything (no gaps), we're done deterministically.
    if not needs_llm:
        if added == 0:
            return report
        report["source"] = "parser"
        report["changed"] = True
        if not dry_run:
            body = strip_frontmatter(text)
            _write_step_files(path, fm, body, merged)
        return report

    # Parser left gaps. If --llm not requested, report and skip.
    if not use_llm:
        if added > 0:
            # Partial deterministic gain — still write the parser-derived rows.
            report["source"] = "parser"
            report["changed"] = True
            if not dry_run:
                body = strip_frontmatter(text)
                _write_step_files(path, fm, body, merged)
            return report
        report["skipped"] = True
        report["skip_reason"] = "no_files_in_body"
        return report

    # LLM path — one call per plan, fills the gaps.
    try:
        from lore_curator.llm_client import LlmClientError, make_llm_client
        from lore_curator.step_files_inference import infer_step_files
    except ImportError as e:
        report["skipped"] = True
        report["skip_reason"] = f"llm_import_failed: {e}"
        return report

    client = make_llm_client()
    if client is None:
        report["skipped"] = True
        report["skip_reason"] = "no_llm_client"
        return report

    body = strip_frontmatter(text)
    try:
        inference = infer_step_files(
            plan_slug=plan.slug,
            plan_title=plan.title,
            plan_body=body,
            step_ids=needs_llm,
            llm_client=client,
            model=model or _DEFAULT_INFERENCE_MODEL,
        )
    except (LlmClientError, ValueError) as e:
        report["skipped"] = True
        report["skip_reason"] = f"llm_failed: {type(e).__name__}: {e}"
        return report

    llm_added = 0
    low_conf_dropped = 0
    for step_id in needs_llm:
        files = inference.step_files.get(step_id, [])
        conf = inference.confidence.get(step_id, 0.0)
        if conf < confidence_floor:
            low_conf_dropped += 1
            continue
        if not files:
            # High-confidence empty list — record explicitly so we
            # don't re-LLM on next migrate run.
            merged[step_id] = []
            llm_added += 1
            continue
        merged[step_id] = files
        llm_added += 1

    report["llm_low_confidence_dropped"] = low_conf_dropped
    report["frontmatter_steps_added"] = added + llm_added

    if added + llm_added == 0:
        report["skipped"] = True
        report["skip_reason"] = "llm_returned_no_high_confidence"
        return report

    report["source"] = "llm" if llm_added > 0 else "parser"
    report["changed"] = True
    if not dry_run:
        _write_step_files(path, fm, body, merged)
    return report


@app.command("migrate-step-files")
def cmd_migrate_step_files(
    slug: str | None = typer.Argument(
        None, help="Plan slug to migrate. Omit to walk every plan in the vault."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report changes without writing."
    ),
    use_llm: bool = typer.Option(
        False, "--llm",
        help="When the deterministic parser leaves gaps, fall back to "
             "one LLM call per plan to infer step_files from prose.",
    ),
    model: str | None = typer.Option(
        None, "--model",
        help="Model ID for --llm (defaults to claude-haiku-4-5).",
    ),
    confidence_floor: float = typer.Option(
        _INFERENCE_CONFIDENCE_FLOOR, "--confidence-floor",
        min=0.0, max=1.0,
        help="Drop LLM-inferred entries below this confidence.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit a JSON report."),
) -> None:
    """Re-extract ``step_files`` from plan bodies into frontmatter.

    Plans authored before the ``Files:`` convention (or filed under
    older parser versions that mis-handled certain markdown idioms,
    such as backtick-wrapped directives) carry empty ``step_files``
    frontmatter. This command re-parses each plan's body with the
    current canonical extractor and writes back any newly-resolved
    ``step_files`` entries — without touching the body, and without
    overwriting non-empty existing entries.

    With ``--llm``, plans whose body has no parseable ``Files:``
    directive are sent to one LLM call that infers per-step paths
    from prose mentions. Per-step confidence below
    ``--confidence-floor`` is dropped.
    """
    from lore_core.config import get_lore_root
    from lore_core.plans.router import iter_plan_paths

    lore_root = get_lore_root()
    if lore_root is None or not lore_root.exists():
        typer.echo("lore: $LORE_ROOT not configured", err=True)
        raise typer.Exit(code=1)

    wiki_root = lore_root / "wiki"
    if not wiki_root.is_dir():
        typer.echo(f"lore: no wikis under {wiki_root}", err=True)
        raise typer.Exit(code=1)

    candidates: list[Path] = []
    for wiki_dir in sorted(wiki_root.iterdir()):
        if not wiki_dir.is_dir():
            continue
        for plan_path in iter_plan_paths(wiki_dir):
            if slug is not None and plan_path.stem != slug and not plan_path.stem.endswith(f"-{slug}"):
                continue
            candidates.append(plan_path)

    if slug is not None and not candidates:
        typer.echo(f"lore: no plan matching slug {slug!r}", err=True)
        raise typer.Exit(code=1)

    reports = [
        _extract_step_files_one_plan(
            p,
            dry_run=dry_run,
            use_llm=use_llm,
            confidence_floor=confidence_floor,
            model=model,
        )
        for p in candidates
    ]
    changed = [r for r in reports if r["changed"]]
    skipped = [r for r in reports if r.get("skipped")]

    if json_out:
        _emit_json({
            "schema": "lore.plan.migrate-step-files/1",
            "data": {
                "dry_run": dry_run,
                "use_llm": use_llm,
                "confidence_floor": confidence_floor,
                "scanned": len(reports),
                "changed": len(changed),
                "skipped": len(skipped),
                "reports": reports,
            },
        })
        return

    verb = "would update" if dry_run else "updated"
    if not changed and not skipped:
        typer.echo(f"lore: scanned {len(reports)} plans — nothing to migrate")
        return
    for r in changed:
        plan_slug = Path(r["path"]).stem
        suffix = ""
        if r.get("source") == "llm":
            dropped = r.get("llm_low_confidence_dropped", 0)
            suffix = f" [llm{f', {dropped} low-confidence dropped' if dropped else ''}]"
        typer.echo(
            f"  {verb} {plan_slug}: "
            f"+{r['frontmatter_steps_added']} step_files entries "
            f"(kept {r['frontmatter_steps_kept']}){suffix}"
        )
    for r in skipped:
        plan_slug = Path(r["path"]).stem
        typer.echo(f"  skipped {plan_slug}: {r['skip_reason']}")
    summary_parts: list[str] = []
    if changed:
        summary_parts.append(f"{verb} {len(changed)}")
    if skipped:
        summary_parts.append(f"skipped {len(skipped)}")
    typer.echo(
        f"lore: {' / '.join(summary_parts)} of {len(reports)} plans scanned"
    )


# ---------------------------------------------------------------------------
# `lore plan step`
# ---------------------------------------------------------------------------


@app.command("step")
def cmd_step(
    slug: str = typer.Argument(..., help="Plan slug."),
    step_id: str = typer.Argument(..., help="Step ID (e.g. step-2)."),
    done: bool = typer.Option(False, "--done", help="Mark step as done."),
    in_progress: bool = typer.Option(
        False, "--in-progress", help="Mark step as in_progress."
    ),
    blocked: bool = typer.Option(False, "--blocked", help="Mark step as blocked."),
    pending: bool = typer.Option(
        False, "--pending", help="Clear status (move step back to pending)."
    ),
    wiki: str = typer.Option(None, "--wiki"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Set ``step_status[<step_id>]`` atomically."""
    flags = sum(int(x) for x in (done, in_progress, blocked, pending))
    if flags != 1:
        print(
            "lore: pass exactly one of --done / --in-progress / --blocked / --pending",
            file=sys.stderr,
        )
        raise typer.Exit(code=2)

    from lore_core.plans.step_status import set_step
    from lore_core.plans.types import StepStatus

    if done:
        new_status: StepStatus | None = StepStatus.DONE
    elif in_progress:
        new_status = StepStatus.IN_PROGRESS
    elif blocked:
        new_status = StepStatus.BLOCKED
    else:
        new_status = None

    wiki_root = _resolve_wiki(wiki)
    try:
        update = set_step(
            wiki_root=wiki_root, slug=slug, step_id=step_id, status=new_status
        )
    except (FileNotFoundError, ValueError) as e:
        typer.echo(f"lore: {e}", err=True)
        raise typer.Exit(code=1)

    if json_out:
        _emit_json({
            "schema": "lore.plan.step/1",
            "data": {
                "slug": update.slug,
                "step_id": update.step_id,
                "previous": update.previous,
                "current": update.current,
                "step_status_updated": update.bumped_timestamp,
            },
        })
    else:
        prev = update.previous or "pending"
        curr = update.current or "pending"
        print(f"{slug} · {step_id}: {prev} → {curr}")


# ---------------------------------------------------------------------------
# `lore plan advance`
# ---------------------------------------------------------------------------


@app.command("advance")
def cmd_advance(
    slug: str = typer.Argument(..., help="Plan slug."),
    wiki: str = typer.Option(None, "--wiki"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Mark the current in-progress step done; else mark the next pending step done."""
    from lore_core.plans.step_status import advance

    wiki_root = _resolve_wiki(wiki)
    try:
        update = advance(wiki_root=wiki_root, slug=slug)
    except FileNotFoundError as e:
        typer.echo(f"lore: {e}", err=True)
        raise typer.Exit(code=1)
    if update is None:
        msg = f"{slug}: nothing to advance — all steps are done."
        if json_out:
            _emit_json({"schema": "lore.plan.advance/1", "data": {"slug": slug, "advanced": False}})
        else:
            print(msg)
        return
    if json_out:
        _emit_json({
            "schema": "lore.plan.advance/1",
            "data": {
                "slug": update.slug,
                "step_id": update.step_id,
                "previous": update.previous,
                "current": update.current,
                "advanced": True,
            },
        })
    else:
        prev = update.previous or "pending"
        print(f"{slug} · {update.step_id}: {prev} → done")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
