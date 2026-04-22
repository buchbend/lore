"""Consent state for a repo's attachment lifecycle.

Combines ``.lore.yml`` presence with the host's ``attachments.json`` to
answer: *in what state is this cwd's attachment relationship with Lore?*

The state machine (see ``wiki/private/concepts/lore/local-lore-state.md``):

=================  ================  ==========  ==========================
offer present?     attachment?       declined?   state
=================  ================  ==========  ==========================
no                 no                no          UNTRACKED (Lore inert)
yes                no                no          OFFERED (prompt once)
yes                yes (match fp)    no          ATTACHED
yes                no                yes         DORMANT (never ask again)
no                 yes               —           MANUAL (direct ``/lore:attach``)
yes (changed fp)   yes (old fp)      —           DRIFT (prompt to re-accept)
=================  ================  ==========  ==========================
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from lore_core.offer import Offer, find_lore_yml, offer_fingerprint, parse_lore_yml
from lore_core.state.attachments import AttachmentsFile


class ConsentState(Enum):
    UNTRACKED = "untracked"
    OFFERED = "offered"
    ATTACHED = "attached"
    DORMANT = "dormant"
    MANUAL = "manual"
    DRIFT = "drift"


@dataclass(frozen=True)
class ConsentResult:
    state: ConsentState
    offer: Offer | None
    repo_root: Path | None
    offer_fingerprint: str | None


def classify_state(cwd: Path, attachments: AttachmentsFile) -> ConsentResult:
    """Classify the attachment state for ``cwd``.

    Looks up ``.lore.yml`` via walk-up (bounded); checks ``attachments``
    for an active row and ``declined`` list for a prior dismiss. Pure:
    does not write anything.
    """
    offer, offer_path = _load_offer(cwd)
    attachment = attachments.longest_prefix_match(cwd)

    if offer is None:
        if attachment is not None:
            return ConsentResult(
                state=ConsentState.MANUAL,
                offer=None,
                repo_root=attachment.path,
                offer_fingerprint=None,
            )
        return ConsentResult(
            state=ConsentState.UNTRACKED,
            offer=None,
            repo_root=None,
            offer_fingerprint=None,
        )

    # Offer is present.
    assert offer_path is not None  # invariant: offer implies offer_path
    repo_root = offer_path.parent
    fp = offer_fingerprint(offer)

    if attachment is not None:
        if attachment.offer_fingerprint == fp:
            return ConsentResult(
                state=ConsentState.ATTACHED,
                offer=offer,
                repo_root=repo_root,
                offer_fingerprint=fp,
            )
        # Attachment exists but fingerprint mismatches — the offer has
        # changed since acceptance, or the attachment was manual (no fp).
        return ConsentResult(
            state=ConsentState.DRIFT,
            offer=offer,
            repo_root=repo_root,
            offer_fingerprint=fp,
        )

    if attachments.is_declined(repo_root, fp):
        return ConsentResult(
            state=ConsentState.DORMANT,
            offer=offer,
            repo_root=repo_root,
            offer_fingerprint=fp,
        )

    return ConsentResult(
        state=ConsentState.OFFERED,
        offer=offer,
        repo_root=repo_root,
        offer_fingerprint=fp,
    )


def _load_offer(cwd: Path) -> tuple[Offer | None, Path | None]:
    """Walk up for ``.lore.yml`` and parse. Returns ``(None, None)`` if
    absent or malformed."""
    offer_path = find_lore_yml(cwd)
    if offer_path is None:
        return None, None
    offer = parse_lore_yml(offer_path)
    if offer is None:
        # File present but malformed — treat as absent for consent
        # purposes so a broken offer doesn't trigger prompts.
        return None, None
    return offer, offer_path
