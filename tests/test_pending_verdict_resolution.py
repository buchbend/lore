"""A recorded verdict takes a note off the pending list.

The pending list is a worklist: it answers "what still needs a human
decision?", not "what is stale?". A stale-candidate signal stays true
for the life of the note, so reusing it verbatim as a worklist leaves
resolved notes on the list with no verdict able to remove them.

A verdict counts as recorded when the note carries `superseded_by`, or
`status: stale` together with a non-empty `stale_reason`. The one
exception is a disagreement, which is a fresh conflict rather than a
settled decision.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from lore_core.freshness import count_pending_verdicts, list_pending_verdicts
from lore_core.verdicts_sidecar import set_confirmed


def _write_catalog(
    wiki: Path, entries: list[dict], orphan_set: list[str] | None = None
) -> None:
    (wiki / "_catalog.json").write_text(
        json.dumps(
            {
                "wiki": wiki.name,
                "sections": {"concepts": entries},
                "orphan_set": orphan_set or [],
            }
        )
    )


def _write_note(wiki: Path, rel: str, frontmatter: dict) -> None:
    p = wiki / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in frontmatter.items():
        lines.append(f"{k}: '{v}'" if isinstance(v, str) else f"{k}: {v}")
    lines.append("---")
    lines.append("body")
    p.write_text("\n".join(lines))


def _paths(wiki: Path, handle: str | None = None) -> list[str]:
    return [e.path for e in list_pending_verdicts(wiki, handle)]


# ---------------------------------------------------------------------------
# Recorded verdicts leave the list
# ---------------------------------------------------------------------------


def test_superseded_by_alone_is_not_pending(tmp_path):
    """Supersession names the successor, so the decision is already made."""
    _write_note(tmp_path, "concepts/a.md", {"superseded_by": "[[b]]"})
    _write_catalog(tmp_path, [{"path": "concepts/a.md", "superseded_by": "[[b]]"}])

    assert _paths(tmp_path) == []
    assert count_pending_verdicts(tmp_path) == (0, False)


def test_stale_with_reason_is_not_pending(tmp_path):
    """A complete stale verdict needs no second verdict from the same user."""
    _write_note(
        tmp_path,
        "concepts/a.md",
        {
            "status": "stale",
            "stale_reason": "replaced by the registry",
            "stale_by": "buchbend",
            "stale_at": "2026-05-12",
        },
    )
    _write_catalog(tmp_path, [{"path": "concepts/a.md", "status": "stale"}])

    assert _paths(tmp_path) == []
    assert count_pending_verdicts(tmp_path) == (0, False)


def test_superseded_orphan_is_not_pending(tmp_path):
    """A broken link in an already-superseded note needs no verdict."""
    _write_note(tmp_path, "concepts/a.md", {"superseded_by": "[[b]]"})
    _write_catalog(
        tmp_path,
        [{"path": "concepts/a.md", "superseded_by": "[[b]]"}],
        orphan_set=["concepts/a.md"],
    )

    assert _paths(tmp_path) == []
    assert count_pending_verdicts(tmp_path) == (0, False)


# ---------------------------------------------------------------------------
# Open questions stay on the list
# ---------------------------------------------------------------------------


def test_stale_without_reason_is_pending(tmp_path):
    """A bare marker records no reason, so a human still has to supply one."""
    _write_note(tmp_path, "concepts/a.md", {"status": "stale"})
    _write_catalog(tmp_path, [{"path": "concepts/a.md", "status": "stale"}])

    assert _paths(tmp_path) == ["concepts/a.md"]
    assert count_pending_verdicts(tmp_path) == (1, False)


def test_orphan_without_recorded_verdict_is_pending(tmp_path):
    _write_note(tmp_path, "concepts/a.md", {"type": "concept"})
    _write_catalog(
        tmp_path, [{"path": "concepts/a.md"}], orphan_set=["concepts/a.md"]
    )

    assert _paths(tmp_path) == ["concepts/a.md"]
    assert count_pending_verdicts(tmp_path) == (1, False)


def test_disagreement_survives_the_recorded_verdict(tmp_path):
    """A later personal confirm contradicts the stale verdict, so ask."""
    stale_at = date.today() - timedelta(days=10)
    _write_note(
        tmp_path,
        "concepts/a.md",
        {
            "status": "stale",
            "stale_reason": "replaced by the registry",
            "stale_by": "someone",
            "stale_at": stale_at.isoformat(),
        },
    )
    _write_catalog(tmp_path, [{"path": "concepts/a.md", "status": "stale"}])
    set_confirmed(tmp_path, "buchbend", "concepts/a.md", stale_at + timedelta(days=1))

    entries = list_pending_verdicts(tmp_path, "buchbend")
    assert [e.path for e in entries] == ["concepts/a.md"]
    assert entries[0].disagreement is not None


# ---------------------------------------------------------------------------
# The chip and the picker never drift
# ---------------------------------------------------------------------------


def test_chip_count_matches_picker_rows(tmp_path):
    """One predicate, two surfaces — including the disagreement carve-out."""
    stale_at = date.today() - timedelta(days=10)
    _write_note(tmp_path, "concepts/settled.md", {"superseded_by": "[[b]]"})
    _write_note(tmp_path, "concepts/bare.md", {"status": "stale"})
    _write_note(
        tmp_path,
        "concepts/conflict.md",
        {
            "status": "stale",
            "stale_reason": "replaced by the registry",
            "stale_by": "someone",
            "stale_at": stale_at.isoformat(),
        },
    )
    _write_catalog(
        tmp_path,
        [
            {"path": "concepts/settled.md", "superseded_by": "[[b]]"},
            {"path": "concepts/bare.md", "status": "stale"},
            {"path": "concepts/conflict.md", "status": "stale"},
        ],
    )
    set_confirmed(
        tmp_path, "buchbend", "concepts/conflict.md", stale_at + timedelta(days=1)
    )

    rows = list_pending_verdicts(tmp_path, "buchbend")
    count, _ = count_pending_verdicts(tmp_path, "buchbend")
    assert count == len(rows) == 2
