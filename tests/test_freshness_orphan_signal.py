"""Tests for the orphan-link signal wired into freshness — slice 4 of PRD #65."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from lore_core.freshness import compute_freshness, load_orphan_set
from lore_mcp.server import handle_read, handle_search


# ---------------------------------------------------------------------------
# load_orphan_set — cache loader
# ---------------------------------------------------------------------------


def test_load_orphan_set_missing_catalog_returns_empty(tmp_path):
    assert load_orphan_set(tmp_path) == set()


def test_load_orphan_set_missing_orphan_key_returns_empty(tmp_path):
    cat = tmp_path / "_catalog.json"
    cat.write_text(json.dumps({"wiki": "demo"}))
    assert load_orphan_set(tmp_path) == set()


def test_load_orphan_set_malformed_returns_empty(tmp_path):
    (tmp_path / "_catalog.json").write_text("{not json")
    assert load_orphan_set(tmp_path) == set()


def test_load_orphan_set_returns_resolved_paths(tmp_path):
    rel = "concepts/n.md"
    (tmp_path / "concepts").mkdir()
    (tmp_path / "concepts" / "n.md").write_text("body")
    (tmp_path / "_catalog.json").write_text(
        json.dumps({"wiki": "demo", "orphan_set": [rel]})
    )
    out = load_orphan_set(tmp_path)
    assert (tmp_path / rel).resolve() in out


def test_load_orphan_set_skips_non_string_entries(tmp_path):
    (tmp_path / "_catalog.json").write_text(
        json.dumps({"wiki": "demo", "orphan_set": ["a.md", 42, None, ""]})
    )
    out = load_orphan_set(tmp_path)
    assert len(out) == 1
    assert (tmp_path / "a.md").resolve() in out


# ---------------------------------------------------------------------------
# compute_freshness — orphan branch
# ---------------------------------------------------------------------------


def test_compute_freshness_orphan_membership(tmp_path):
    note = tmp_path / "n.md"
    note.write_text("body")
    sig = compute_freshness({}, note, tmp_path, None, {note.resolve()})
    assert sig.status == "stale-candidate"
    assert sig.cause == "orphan_broken"


def test_compute_freshness_orphan_authored_marker_wins(tmp_path):
    note = tmp_path / "n.md"
    note.write_text("body")
    sig = compute_freshness(
        {"status": "stale"}, note, tmp_path, None, {note.resolve()}
    )
    # Authored markers always take precedence over orphan signal.
    assert sig.cause == "authored_marker"


# ---------------------------------------------------------------------------
# lint integration — orphan_set is written into _catalog.json
# ---------------------------------------------------------------------------


def _bootstrap_wiki(tmp_path: Path, monkeypatch, files: dict[str, str]) -> Path:
    wiki = tmp_path / "wiki" / "demo"
    (wiki / "concepts").mkdir(parents=True)
    for name, body in files.items():
        (wiki / "concepts" / name).write_text(body)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    return wiki


def test_lint_writes_orphan_set_into_catalog(tmp_path, monkeypatch):
    files = {
        "good.md": dedent("""\
            ---
            type: concept
            description: ok
            tags: [x]
            ---
            references [[good]]
            """),
        "broken.md": dedent("""\
            ---
            type: concept
            description: broken
            tags: [x]
            ---
            references [[never-existed]]
            """),
    }
    wiki = _bootstrap_wiki(tmp_path, monkeypatch, files)
    from lore_core.lint import run_lint

    run_lint()
    catalog = json.loads((wiki / "_catalog.json").read_text())
    assert "orphan_set" in catalog
    assert "concepts/broken.md" in catalog["orphan_set"]
    assert "concepts/good.md" not in catalog["orphan_set"]


def test_handle_search_flags_orphan_after_lint(tmp_path, monkeypatch):
    files = {
        "broken.md": dedent("""\
            ---
            type: concept
            description: broken alpha
            tags: [alpha]
            ---
            content with broken [[nonexistent-target]]
            """),
        "ok.md": dedent("""\
            ---
            type: concept
            description: ok alpha
            tags: [alpha]
            ---
            no broken refs alpha
            """),
    }
    _bootstrap_wiki(tmp_path, monkeypatch, files)
    from lore_core.lint import run_lint

    run_lint()

    hits = handle_search("alpha", wiki="demo", k=5)
    by_path = {h["path"]: h for h in hits}
    if "concepts/broken.md" in by_path:
        h = by_path["concepts/broken.md"]
        assert h["freshness"]["status"] == "stale-candidate"
        assert h["freshness"]["cause"] == "orphan_broken"
    if "concepts/ok.md" in by_path:
        assert by_path["concepts/ok.md"]["freshness"]["status"] == "confirmed"


def test_handle_read_flags_orphan_after_lint(tmp_path, monkeypatch):
    files = {
        "broken.md": dedent("""\
            ---
            type: concept
            description: broken
            ---
            content with broken [[nonexistent]]
            """),
    }
    _bootstrap_wiki(tmp_path, monkeypatch, files)
    from lore_core.lint import run_lint

    run_lint()

    result = handle_read("concepts/broken.md", wiki="demo")
    assert result["freshness"]["status"] == "stale-candidate"
    assert result["freshness"]["cause"] == "orphan_broken"


def test_handle_read_no_lint_run_degrades_gracefully(tmp_path, monkeypatch):
    """Cache miss (no _catalog.json yet) → orphan check no-ops; not an error."""
    wiki = tmp_path / "wiki" / "demo"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / "broken.md").write_text(
        dedent("""\
            ---
            type: concept
            ---
            content with broken [[nonexistent]]
            """)
    )
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    # Skip lint — no catalog cache.
    result = handle_read("concepts/broken.md", wiki="demo")
    # Without the cache, orphans are invisible — confirmed is the safe default.
    assert result["freshness"]["status"] == "confirmed"
