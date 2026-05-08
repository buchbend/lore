"""Retrieval-time freshness filter — slice 3 of PRD #65.

Consumes :class:`lore_core.freshness.FreshnessSignal` blocks attached
in slice 1 and applies retrieval-time policy to result lists. Two
public entry points:

- :func:`apply_search_filter` — stable secondary-sort hits by status
  so ``confirmed`` ranks above ``stale-candidate`` at *tied relevance
  scores*. The relevance ordering itself is preserved — recall is
  never sacrificed; a ``stale-candidate`` that is the only match for
  a query still surfaces.

- :func:`apply_inject_filter` — pre-classify session-hint candidates
  (and any other inject-source list) into kept / downranked / excluded
  tiers. ``status: stale`` (and equivalent hard markers like
  ``superseded_by``) are excluded entirely from the SessionStart
  ``additionalContext`` block. Soft-only ``stale-candidate`` notes
  remain available but are appended after confirmed peers, so the
  LLM still gets the breadcrumb without being primed by the body.

Both functions return a structured ``audit`` log of the decisions
they made; the caller stitches it into the ``/lore:context`` cache
so users can see why a note was excluded or downranked.

The module is pure data-structure manipulation — no I/O, no LLM
calls, no fs walks beyond what the caller has already done. Latency
budget at retrieval is therefore the cost of one stable sort.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal


# Priority used by ``apply_search_filter`` as the *secondary* sort key
# (relevance score remains primary). Lower = appears first.
_STATUS_PRIORITY = {"confirmed": 0, "stale-candidate": 1}


@dataclass(frozen=True)
class FilterAuditEntry:
    """One decision made by the filter.

    Fields:
        path: Note relative path within its wiki.
        wiki: Wiki name.
        action: ``downranked`` (kept but lower priority) or ``excluded``
            (removed entirely from the inject set).
        cause: Echoes the FreshnessSignal cause that drove the decision.
        reason: Short human-readable explanation, suitable for direct
            rendering in /lore:context.
    """

    path: str
    wiki: str | None
    action: Literal["downranked", "excluded"]
    cause: str | None
    reason: str | None


@dataclass
class FilterAudit:
    """Aggregate audit log for one filter call."""

    entries: list[FilterAuditEntry] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.entries)

    def render_lines(self) -> list[str]:
        """Render entries as lines for the /lore:context cache.

        Empty list when no decisions were made — caller suppresses the
        whole subsection in that case.
        """
        if not self.entries:
            return []
        lines = ["### Filtered for staleness"]
        for e in self.entries:
            wiki = f"{e.wiki}/" if e.wiki else ""
            note = f"[[{wiki}{e.path}]]" if e.path else "(unknown)"
            verb = "excluded" if e.action == "excluded" else "downranked"
            tail = e.reason or e.cause or ""
            lines.append(f"- {note} — {verb}" + (f" — {tail}" if tail else ""))
        return lines


def _is_hard_stale(freshness: dict | None) -> bool:
    """Hard-stale = ``status == stale-candidate`` AND the cause is a hard
    authored marker (``status: stale`` / ``superseded_by``).

    The slice-3 inject filter excludes hard-stale notes entirely. Soft
    markers (``supersede_candidate``) only downrank. Disagreement
    (slice 9) layers on top by escalating to the hard-stale branch.
    """
    if not freshness:
        return False
    if freshness.get("status") != "stale-candidate":
        return False
    reason = (freshness.get("reason") or "").lower()
    # The reason text comes from
    # ``lore_core.freshness._format_marker_reason`` and is the cleanest
    # disambiguator between hard and soft authored markers without
    # re-parsing frontmatter on this side.
    if "marked stale" in reason:
        return True
    if reason.startswith("superseded by"):
        return True
    return False


def _status_of(freshness: dict | None) -> str:
    if not freshness:
        return "confirmed"
    return freshness.get("status") or "confirmed"


def apply_search_filter(
    hits: list[dict[str, Any]],
    *,
    score_key: str = "score",
    freshness_key: str = "freshness",
    path_key: str = "path",
    wiki_key: str = "wiki",
) -> tuple[list[dict[str, Any]], FilterAudit]:
    """Stable-sort ``hits`` so confirmed precede stale-candidate at tied
    scores. Relevance ordering is the primary sort key; freshness is
    secondary.

    Returns the (possibly reordered) hits and an audit log of
    ``downranked`` entries — useful for the /lore:context render.

    No exclusions: the search filter never silently hides a hit. The
    recall property in PRD #65 is load-bearing for "did the system
    forget about that note?".
    """
    audit = FilterAudit()
    if not hits:
        return hits, audit

    # Build the sort key. ``-score`` so higher scores rank first;
    # status priority second so confirmed wins ties; original index
    # third so within a (score, status) tie we preserve the backend's
    # ordering deterministically.
    indexed = list(enumerate(hits))

    def _sort_key(item):
        idx, h = item
        score = h.get(score_key, 0.0) or 0.0
        status = _status_of(h.get(freshness_key))
        prio = _STATUS_PRIORITY.get(status, 99)
        return (-float(score), prio, idx)

    sorted_hits = [h for _idx, h in sorted(indexed, key=_sort_key)]

    for h in sorted_hits:
        fr = h.get(freshness_key) or {}
        if fr.get("status") == "stale-candidate":
            audit.entries.append(
                FilterAuditEntry(
                    path=str(h.get(path_key, "")),
                    wiki=h.get(wiki_key),
                    action="downranked",
                    cause=fr.get("cause"),
                    reason=fr.get("reason"),
                )
            )

    return sorted_hits, audit


@dataclass
class InjectFilterResult:
    """Outcome of :func:`apply_inject_filter`.

    Fields:
        kept: Items to include in the injected context, in the order
            they should appear (confirmed first, soft stale-candidate
            after).
        excluded: Items dropped entirely.
        audit: Structured audit log for /lore:context.
    """

    kept: list[Any]
    excluded: list[Any]
    audit: FilterAudit


def apply_inject_filter(
    items: Iterable[Any],
    freshness_of,
    *,
    path_of=lambda item: "",
    wiki_of=lambda item: None,
) -> InjectFilterResult:
    """Partition ``items`` into kept / excluded / audit.

    Args:
        items: Inject candidates (e.g., session-hint tuples, project
            note dicts). The shape is opaque to this filter; callers
            supply accessors.
        freshness_of: ``item -> dict | None`` — the freshness block
            for the item, or None if unknown (treated as ``confirmed``).
        path_of: ``item -> str`` accessor for the audit log.
        wiki_of: ``item -> str | None`` accessor for the audit log.

    Recall semantics:
        * Hard-stale (status:stale, superseded_by) → excluded.
        * Soft stale-candidate (supersede_candidate variants) → kept,
          but appended after confirmed items in the result.
        * Confirmed → kept in original order.

    Stable: relative order within each tier is preserved.
    """
    confirmed: list[Any] = []
    soft: list[Any] = []
    excluded: list[Any] = []
    audit = FilterAudit()

    for item in items:
        fr = freshness_of(item)
        status = _status_of(fr)
        if status == "stale-candidate" and _is_hard_stale(fr):
            excluded.append(item)
            audit.entries.append(
                FilterAuditEntry(
                    path=str(path_of(item)),
                    wiki=wiki_of(item),
                    action="excluded",
                    cause=(fr or {}).get("cause"),
                    reason=(fr or {}).get("reason"),
                )
            )
            continue
        if status == "stale-candidate":
            soft.append(item)
            audit.entries.append(
                FilterAuditEntry(
                    path=str(path_of(item)),
                    wiki=wiki_of(item),
                    action="downranked",
                    cause=(fr or {}).get("cause"),
                    reason=(fr or {}).get("reason"),
                )
            )
            continue
        confirmed.append(item)

    return InjectFilterResult(
        kept=confirmed + soft,
        excluded=excluded,
        audit=audit,
    )
