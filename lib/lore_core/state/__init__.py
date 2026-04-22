"""Local Lore state: attachments.json + scopes.json under `$LORE_ROOT/.lore/`.

See `wiki/private/concepts/lore/local-lore-state.md` in the design vault
and `docs/superpowers/plans/2026-04-22-local-lore-state-plan.md`.
"""

from lore_core.state.attachments import Attachment, AttachmentsFile, Declined
from lore_core.state.scopes import ScopeEntry, ScopesFile, parent_of

__all__ = [
    "Attachment",
    "AttachmentsFile",
    "Declined",
    "ScopeEntry",
    "ScopesFile",
    "parent_of",
]
