"""One-shot migrations for Lore state evolution.

Distinct from :mod:`lore_core.migrate` (which handles frontmatter schema
evolution for *notes*). This package handles *state* migrations — e.g.,
converting legacy ``## Lore`` CLAUDE.md blocks to the Phase 1+ registry
model (``attachments.json`` + ``scopes.json`` + ``.lore.yml``).
"""

from lore_core.migration.attachments import (
    MigrationResult,
    migrate_repo,
)

__all__ = ["MigrationResult", "migrate_repo"]
