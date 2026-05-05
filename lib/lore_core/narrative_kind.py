"""Narrative-shape selector for session-note Phase-2 composition.

The shape of a session note (work / discussion) is a deterministic
function of:

- whether any file was modified in the slice (``files_modified``)
- whether the user expressed strong assent (commitment between named
  alternatives, or override of a model proposal)
- whether the user explicitly disclaimed intent to change code
  ("no code change", "just brainstorming")

This module composes those primitives into a ``NarrativeShape`` dataclass
of booleans. The renderer and Phase-2 schema/prompt gating in
``lore_curator.synthesis`` switch on those booleans, not on a string —
the string ``kind`` property is only for logging.

Why a dataclass of booleans, not an enum: the original sketch used
``narrative_kind: Literal["work","discussion","discussion_with_signals"]``,
but that's three states fighting to be a 2-bit product. Per the
architectural review of plan ``yes-do-that-keen-yeti``, the renderer
must switch on the booleans so adding a future shape (e.g. "review-only:
reads but no edits, no assent") is a one-bit addition rather than a
new enum variant threaded through every consumer.
"""
from __future__ import annotations

from dataclasses import dataclass

from lore_core.decision_signals import extract_signals
from lore_core.types import Turn


__all__ = ["NarrativeShape", "select_shape"]


@dataclass(frozen=True)
class NarrativeShape:
    """Booleans driving Phase-2 schema/prompt selection and renderer gates.

    - ``has_edits``: at least one file in ``files_modified``. Drives the
      core fork between work-shape (Decisions / What we worked on) and
      discussion-shape (Discussion).
    - ``decisions_allowed``: whether a Decisions section may appear at
      all. True when ``has_edits`` (assent-by-action) OR there's at least
      one strong (non-hedged) assent hit. False when neither condition
      holds — the schema then omits ``decisions[]`` entirely so the LLM
      cannot emit it even if it wanted to.
    - ``no_edit_intent``: user explicitly said "no code change" /
      "just brainstorm". A pure echo of the regex signal; useful for
      prompt steering ("the user already disclaimed intent — write
      Discussion, not What we worked on").
    - ``adr_flagged``: user explicitly used the ADR vocabulary. Surfaces
      to frontmatter as ``adr_flagged: true``; the renderer does not
      auto-create ADR stub notes (deferred per plan out-of-scope).
    """

    has_edits: bool
    decisions_allowed: bool
    no_edit_intent: bool
    adr_flagged: bool

    @property
    def kind(self) -> str:
        """Short label for logging. Not load-bearing — the renderer and
        gate logic switch on the booleans, never this string."""
        if self.has_edits:
            return "work"
        if self.decisions_allowed:
            return "discussion_with_signals"
        return "discussion"


def select_shape(
    turns: list[Turn],
    files_modified: list[str],
) -> NarrativeShape:
    """Compose the shape from raw inputs.

    ``turns`` drives the regex prefilter (``decision_signals.extract_signals``);
    ``files_modified`` drives the work/discussion fork. Callers pass
    both because ``files_modified`` is typically already computed
    upstream (during buffer append or flush) — recomputing it here would
    duplicate work for no gain.
    """
    has_edits = bool(files_modified)
    signals = extract_signals(turns)
    decisions_allowed = has_edits or signals.has_strong_assent
    return NarrativeShape(
        has_edits=has_edits,
        decisions_allowed=decisions_allowed,
        no_edit_intent=signals.no_edit_intent,
        adr_flagged=signals.adr_flagged,
    )
