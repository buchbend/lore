"""Tests for ``lore_core.freshness_filter`` — slice 3 of PRD #65."""

from __future__ import annotations

from lore_core.freshness_filter import (
    FilterAudit,
    FilterAuditEntry,
    apply_inject_filter,
    apply_search_filter,
)


# ---------------------------------------------------------------------------
# apply_search_filter
# ---------------------------------------------------------------------------


def _hit(path: str, score: float, status: str, reason: str | None = None) -> dict:
    return {
        "path": path,
        "wiki": "demo",
        "score": score,
        "freshness": {"status": status, "cause": "authored_marker", "reason": reason},
    }


def test_search_filter_at_tied_scores_confirmed_wins() -> None:
    hits = [
        _hit("a.md", 0.9, "stale-candidate", "marked stale"),
        _hit("b.md", 0.9, "confirmed"),
        _hit("c.md", 0.5, "confirmed"),
    ]
    out, audit = apply_search_filter(hits)
    paths = [h["path"] for h in out]
    # Tied at 0.9: confirmed b before stale-candidate a.
    assert paths == ["b.md", "a.md", "c.md"]
    # Audit notes the downrank.
    assert any(e.path == "a.md" and e.action == "downranked" for e in audit.entries)


def test_search_filter_higher_score_stale_still_first() -> None:
    """Recall property — stale-candidate with a higher score still leads."""
    hits = [
        _hit("a.md", 0.95, "stale-candidate", "marked stale"),
        _hit("b.md", 0.5, "confirmed"),
    ]
    out, _audit = apply_search_filter(hits)
    assert [h["path"] for h in out] == ["a.md", "b.md"]


def test_search_filter_only_match_stale_still_surfaces() -> None:
    """A stale-candidate that is the only match must surface."""
    hits = [_hit("only.md", 0.7, "stale-candidate", "marked stale")]
    out, audit = apply_search_filter(hits)
    assert [h["path"] for h in out] == ["only.md"]
    # It IS downranked in the audit (recorded), but still in the result.
    assert audit.entries[0].path == "only.md"
    assert audit.entries[0].action == "downranked"


def test_search_filter_empty_input() -> None:
    out, audit = apply_search_filter([])
    assert out == []
    assert not audit


def test_search_filter_preserves_within_tier_order_at_same_score() -> None:
    """When several confirmed hits are tied, the input order survives."""
    hits = [
        _hit("a.md", 0.5, "confirmed"),
        _hit("b.md", 0.5, "confirmed"),
        _hit("c.md", 0.5, "confirmed"),
    ]
    out, _ = apply_search_filter(hits)
    assert [h["path"] for h in out] == ["a.md", "b.md", "c.md"]


def test_search_filter_no_freshness_block_treated_as_confirmed() -> None:
    hits = [
        {"path": "a.md", "wiki": "w", "score": 0.5},
        {"path": "b.md", "wiki": "w", "score": 0.5,
         "freshness": {"status": "stale-candidate", "cause": "authored_marker", "reason": "marked stale"}},
    ]
    out, _ = apply_search_filter(hits)
    assert [h["path"] for h in out] == ["a.md", "b.md"]


# ---------------------------------------------------------------------------
# apply_inject_filter
# ---------------------------------------------------------------------------


def _item(slug: str, status: str, reason: str | None = None) -> dict:
    return {
        "slug": slug,
        "freshness": {"status": status, "cause": "authored_marker", "reason": reason},
    }


def _f(item):
    return item.get("freshness")


def _p(item):
    return item.get("slug", "")


def _w(_item):
    return "demo"


def test_inject_filter_excludes_hard_stale() -> None:
    items = [
        _item("a", "confirmed"),
        _item("b", "stale-candidate", "marked stale"),  # hard
        _item("c", "stale-candidate", "supersede candidate: [[x]]"),  # soft
    ]
    result = apply_inject_filter(items, _f, path_of=_p, wiki_of=_w)
    kept_slugs = [i["slug"] for i in result.kept]
    assert "b" not in kept_slugs  # hard stale excluded
    assert "a" in kept_slugs and "c" in kept_slugs
    assert [i["slug"] for i in result.excluded] == ["b"]


def test_inject_filter_downranks_soft_stale_after_confirmed() -> None:
    items = [
        _item("soft1", "stale-candidate", "supersede candidate: [[x]]"),
        _item("ok1", "confirmed"),
        _item("soft2", "stale-candidate", "supersede candidate of [[y]]"),
        _item("ok2", "confirmed"),
    ]
    result = apply_inject_filter(items, _f, path_of=_p, wiki_of=_w)
    assert [i["slug"] for i in result.kept] == ["ok1", "ok2", "soft1", "soft2"]


def test_inject_filter_excludes_superseded_by_as_hard() -> None:
    items = [
        _item("a", "stale-candidate", "superseded by [[newer]]"),
    ]
    result = apply_inject_filter(items, _f, path_of=_p, wiki_of=_w)
    assert result.excluded == items
    assert result.kept == []


def test_inject_filter_audit_log_shape() -> None:
    items = [
        _item("a", "confirmed"),
        _item("b", "stale-candidate", "marked stale"),
        _item("c", "stale-candidate", "supersede candidate: [[x]]"),
    ]
    result = apply_inject_filter(items, _f, path_of=_p, wiki_of=_w)
    actions = {(e.path, e.action) for e in result.audit.entries}
    assert ("b", "excluded") in actions
    assert ("c", "downranked") in actions
    assert ("a", "downranked") not in actions and ("a", "excluded") not in actions


def test_inject_filter_render_lines_includes_section_heading() -> None:
    items = [_item("b", "stale-candidate", "marked stale")]
    result = apply_inject_filter(items, _f, path_of=_p, wiki_of=_w)
    lines = result.audit.render_lines()
    assert lines[0] == "### Filtered for staleness"
    assert any("excluded" in line for line in lines[1:])


def test_inject_filter_empty_audit_renders_nothing() -> None:
    items = [_item("a", "confirmed")]
    result = apply_inject_filter(items, _f, path_of=_p, wiki_of=_w)
    assert result.audit.render_lines() == []


def test_inject_filter_treats_missing_freshness_as_confirmed() -> None:
    items = [{"slug": "a"}, {"slug": "b", "freshness": None}]
    result = apply_inject_filter(items, _f, path_of=_p, wiki_of=_w)
    assert [i["slug"] for i in result.kept] == ["a", "b"]
    assert result.excluded == []
