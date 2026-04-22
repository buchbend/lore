"""Migrate a repo's legacy ``## Lore`` CLAUDE.md block to the Phase 1+ registry.

Idempotent one-shot. Re-running on a repo already migrated is a no-op.

Steps for one repo at ``repo_path`` (a directory):

  1. Read ``CLAUDE.md`` for a ``## Lore`` section. Absent → no-op.
  2. Write ``.lore.yml`` at ``repo_path/.lore.yml`` with the same fields.
  3. Insert an ``attachments.json`` row with ``source="migrated"`` and a
     fingerprint matching the just-written ``.lore.yml``.
  4. Ingest the scope chain into ``scopes.json``.
  5. Strip the ``## Lore`` section from ``CLAUDE.md`` using the existing
     ``remove_section`` helper (leaves surrounding content untouched).

Shared by both the one-shot CLI (``lore migrate attachments``) and the
lazy fallback in the legacy walk-up resolver (Phase 5 transition).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from lore_core.attach import read_attach
from lore_core.offer import FILENAME as LORE_YML_NAME
from lore_core.offer import offer_fingerprint, parse_lore_yml
from lore_core.state.attachments import Attachment, AttachmentsFile
from lore_core.state.scopes import ScopeConflict, ScopesFile


@dataclass
class MigrationResult:
    repo_path: Path
    action: str
    # "migrated"  — did the full conversion
    # "no-block"  — no ## Lore block at repo_path/CLAUDE.md
    # "already"   — .lore.yml already exists with matching wiki/scope
    # "skipped"   — an external condition prevented migration (e.g. scope conflict)
    wrote_lore_yml: bool = False
    wrote_attachment: bool = False
    stripped_claude_md: bool = False
    detail: str = ""


def migrate_repo(
    repo_path: Path,
    *,
    lore_root: Path,
    dry_run: bool = False,
    now: datetime | None = None,
) -> MigrationResult:
    """Migrate a single repo. See module docstring for the step list."""
    now = now or datetime.now(UTC)

    claude_md = repo_path / "CLAUDE.md"
    block = read_attach(claude_md) if claude_md.exists() else {}
    wiki = block.get("wiki") if block else None
    scope = block.get("scope") if block else None

    # Resolve required fields
    if not wiki or not scope:
        return MigrationResult(
            repo_path=repo_path,
            action="no-block",
            detail="no `## Lore` section with wiki+scope at CLAUDE.md",
        )

    lore_yml = repo_path / LORE_YML_NAME

    # Idempotence: if .lore.yml already exists with matching routing fields, no-op.
    if lore_yml.exists():
        existing = parse_lore_yml(lore_yml)
        if existing is not None and existing.wiki == wiki and existing.scope == scope:
            return MigrationResult(
                repo_path=repo_path,
                action="already",
                detail=f".lore.yml already present ({wiki}, {scope})",
            )

    # Build the .lore.yml content (stable-key order: wiki, scope, optional extras).
    payload_lines = [f"wiki: {wiki}", f"scope: {scope}"]
    backend = block.get("backend")
    if backend and backend != "none":
        payload_lines.append(f"backend: {backend}")
    issues = block.get("issues")
    if issues:
        payload_lines.append(f"issues: {issues}")
    prs = block.get("prs")
    if prs:
        payload_lines.append(f"prs: {prs}")
    lore_yml_text = "\n".join(payload_lines) + "\n"

    if dry_run:
        return MigrationResult(
            repo_path=repo_path,
            action="migrated",
            wrote_lore_yml=False,
            wrote_attachment=False,
            stripped_claude_md=False,
            detail=f"dry-run: would write .lore.yml + register ({wiki}, {scope})",
        )

    # Write .lore.yml
    lore_yml.write_text(lore_yml_text)

    # Parse freshly to get a stable Offer for fingerprinting.
    offer = parse_lore_yml(lore_yml)
    assert offer is not None  # invariant: we just wrote a well-formed file
    fp = offer_fingerprint(offer)

    # Register in attachments.json + scopes.json
    attachments = AttachmentsFile(lore_root)
    attachments.load()
    scopes = ScopesFile(lore_root)
    scopes.load()

    try:
        scopes.ingest_chain(scope, wiki)
    except ScopeConflict as exc:
        return MigrationResult(
            repo_path=repo_path,
            action="skipped",
            wrote_lore_yml=True,  # we already wrote it — file exists on disk
            detail=f"scope conflict at root {exc.scope_root!r} "
                   f"({exc.existing_wiki!r} vs {exc.incoming_wiki!r}); "
                   f"resolve with `lore scopes rename` before re-running",
        )

    attachments.add(
        Attachment(
            path=repo_path,
            wiki=wiki,
            scope=scope,
            attached_at=now,
            source="migrated",
            offer_fingerprint=fp,
        )
    )
    attachments.save()
    scopes.save()

    # Strip `## Lore` from CLAUDE.md (local import — keeps lore_core
    # independent of the CLI-layer writer at load time).
    from lore_cli.attach_cmd import remove_section

    stripped = remove_section(claude_md)

    return MigrationResult(
        repo_path=repo_path,
        action="migrated",
        wrote_lore_yml=True,
        wrote_attachment=True,
        stripped_claude_md=stripped,
        detail=f"migrated → wiki={wiki} scope={scope}",
    )
