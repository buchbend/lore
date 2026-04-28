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

from lore_core.offer import FILENAME, Offer, find_lore_yml, offer_fingerprint, parse_lore_yml
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

    Pure — writes nothing. Two paths:

    * **Attachment present** in the registry (longest-prefix match):
      the user has already committed for this subtree. Look up the
      offer directly at the attachment root (no walk-up policy) to
      verify fingerprint state. Yields ATTACHED / DRIFT / MANUAL.
    * **No attachment**: apply the inherit-aware walk-up via
      :func:`find_lore_yml`. Yields OFFERED / DORMANT / UNTRACKED.

    The asymmetry is deliberate: ``inherit: true`` gates *unsolicited
    prompts* in unattached descendants (issue #24), but does not
    re-litigate state in attached subtrees.
    """
    attachment = attachments.longest_prefix_match(cwd)

    if attachment is not None:
        offer = parse_lore_yml(attachment.path / FILENAME)
        if offer is None:
            # Attached without a parseable offer at the attachment root.
            # Either the attachment was created via `lore attach manual`
            # (no .lore.yml at all) or the file has gone missing/broken.
            return ConsentResult(
                state=ConsentState.MANUAL,
                offer=None,
                repo_root=attachment.path,
                offer_fingerprint=None,
            )
        fp = offer_fingerprint(offer)
        if attachment.offer_fingerprint == fp:
            return ConsentResult(
                state=ConsentState.ATTACHED,
                offer=offer,
                repo_root=attachment.path,
                offer_fingerprint=fp,
            )
        return ConsentResult(
            state=ConsentState.DRIFT,
            offer=offer,
            repo_root=attachment.path,
            offer_fingerprint=fp,
        )

    # No attachment — apply the inherit-aware policy for OFFERED /
    # DORMANT / UNTRACKED.
    offer, offer_path = _load_offer(cwd)
    if offer is None:
        return ConsentResult(
            state=ConsentState.UNTRACKED,
            offer=None,
            repo_root=None,
            offer_fingerprint=None,
        )

    assert offer_path is not None  # invariant: offer implies offer_path
    repo_root = offer_path.parent
    fp = offer_fingerprint(offer)

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
    """Discover an applicable `.lore.yml` and return ``(offer, path)``.

    Returns ``(None, None)`` if no offer applies — absent, malformed,
    or an ancestor that lacks ``inherit: true``. The chokepoint
    semantics live in :func:`find_lore_yml`; this is just a shim for
    the consent system's preferred ``(offer, path)`` ordering.
    """
    found = find_lore_yml(cwd)
    if found is None:
        return None, None
    path, offer = found
    return offer, path
