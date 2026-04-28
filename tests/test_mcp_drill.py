"""`lore_drill` — composite multi-stage retrieval.

Settles P3.2 from the multi-agent synthesis review. The handler chains
``search → read → expand → read_expanded`` in one MCP call and returns
``{trace, result}`` with structured stage breadcrumbs alongside the
note bodies. See ``docs/architecture/lore-drill.md``.

These tests stub the underlying handlers (``handle_search``,
``handle_read``, ``extract_wikilinks``) so they exercise the
composition + short-circuit + truncation logic only — the leaf
handlers have their own coverage.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


def _hit(path: str, score: float = 0.5) -> dict:
    return {
        "path": path,
        "wiki": "private",
        "filename": path.split("/")[-1],
        "score": score,
        "description": f"desc for {path}",
        "tags": [],
    }


def _read(path: str, body: str) -> dict:
    return {"wiki": "private", "path": path, "content": body}


def test_drill_full_chain_records_each_stage():
    from lore_mcp.server import handle_drill

    hits = [_hit("foo.md"), _hit("bar.md")]
    bodies = {
        "foo.md": "## Foo\nlinks to [[baz]]",
        "bar.md": "## Bar\nlinks to [[qux]] and [[baz]]",
        "baz.md": "## Baz",
        "qux.md": "## Qux",
    }

    def fake_read(path, wiki=None):
        return _read(path, bodies[path])

    def fake_resolve(wiki_path, slug):
        return f"{slug}.md" if f"{slug}.md" in bodies else None

    with patch("lore_mcp.server.handle_search", return_value=hits) as m_search, \
         patch("lore_mcp.server.handle_read", side_effect=fake_read) as m_read, \
         patch("lore_mcp.server._resolve_slug", side_effect=fake_resolve), \
         patch("lore_mcp.server._resolve_wiki", return_value="WIKI_PATH_STUB"):
        out = handle_drill(query="foo", wiki="private")

    assert "trace" in out
    assert "result" in out

    stages = [step["stage"] for step in out["trace"]]
    assert stages == ["search", "read", "expand", "read_expanded"]

    # search recorded query + hit count
    assert out["trace"][0]["hits"] == 2
    assert out["trace"][0]["query"] == "foo"
    # read recorded the paths
    assert set(out["trace"][1]["paths"]) == {"foo.md", "bar.md"}
    # expand recorded unique wikilinks (deduped)
    assert set(out["trace"][2]["wikilinks"]) == {"baz", "qux"}
    # read_expanded recorded resolved paths
    assert set(out["trace"][3]["paths"]) == {"baz.md", "qux.md"}

    # Every stage has elapsed_ms.
    for step in out["trace"]:
        assert "elapsed_ms" in step
        assert isinstance(step["elapsed_ms"], int)

    # result.notes has one entry per read note (top-k + expanded).
    assert {n["path"] for n in out["result"]["notes"]} == {"foo.md", "bar.md", "baz.md", "qux.md"}


def test_drill_short_circuits_when_search_returns_zero():
    from lore_mcp.server import handle_drill

    with patch("lore_mcp.server.handle_search", return_value=[]), \
         patch("lore_mcp.server._resolve_wiki", return_value="WIKI_PATH_STUB"):
        out = handle_drill(query="nothing", wiki="private")

    stages = [step["stage"] for step in out["trace"]]
    # search runs; the rest are recorded as skipped without doing work.
    assert stages == ["search", "read", "expand", "read_expanded"]
    assert out["trace"][0]["hits"] == 0
    assert out["trace"][1].get("skipped") == "search_returned_zero"
    assert out["trace"][2].get("skipped") == "search_returned_zero"
    assert out["trace"][3].get("skipped") == "search_returned_zero"
    assert out["result"]["notes"] == []


def test_drill_short_circuits_expand_when_no_wikilinks():
    from lore_mcp.server import handle_drill

    hits = [_hit("solo.md")]
    bodies = {"solo.md": "## Solo\nno wikilinks here"}

    with patch("lore_mcp.server.handle_search", return_value=hits), \
         patch("lore_mcp.server.handle_read", side_effect=lambda path, wiki=None: _read(path, bodies[path])), \
         patch("lore_mcp.server._resolve_wiki", return_value="WIKI_PATH_STUB"):
        out = handle_drill(query="solo", wiki="private")

    assert out["trace"][2].get("skipped") == "no_wikilinks"
    assert out["trace"][3].get("skipped") == "no_wikilinks"
    # search and read still ran successfully.
    assert {n["path"] for n in out["result"]["notes"]} == {"solo.md"}


def test_drill_truncates_expanded_when_over_limit():
    from lore_mcp.server import handle_drill

    hits = [_hit("hub.md")]
    # Hub note linking to ten others.
    links = " ".join(f"[[child{i}]]" for i in range(10))
    bodies = {"hub.md": f"## Hub\n{links}"}
    for i in range(10):
        bodies[f"child{i}.md"] = f"## Child{i}"

    def fake_resolve(_wiki_path, slug):
        return f"{slug}.md" if f"{slug}.md" in bodies else None

    with patch("lore_mcp.server.handle_search", return_value=hits), \
         patch("lore_mcp.server.handle_read", side_effect=lambda path, wiki=None: _read(path, bodies[path])), \
         patch("lore_mcp.server._resolve_slug", side_effect=fake_resolve), \
         patch("lore_mcp.server._resolve_wiki", return_value="WIKI_PATH_STUB"):
        out = handle_drill(query="hub", wiki="private", expand_limit=3)

    expanded = out["trace"][3]
    assert expanded["truncated"] == 7
    assert expanded["kept"] == 3
    # result.notes carries hub + 3 children.
    paths = {n["path"] for n in out["result"]["notes"]}
    assert "hub.md" in paths
    assert sum(1 for p in paths if p.startswith("child")) == 3


def test_drill_unknown_wiki_returns_error():
    from lore_mcp.server import handle_drill

    with patch("lore_mcp.server._resolve_wiki", return_value=None):
        out = handle_drill(query="x", wiki="nope")

    assert "error" in out
    assert out["error"]["code"] == "wiki_not_found"


def test_drill_skips_unresolvable_wikilinks():
    """Wikilinks that don't resolve to a real note are skipped silently
    (not an error — broken links are common during refactors)."""
    from lore_mcp.server import handle_drill

    hits = [_hit("foo.md")]
    bodies = {
        "foo.md": "## Foo\nlinks to [[exists]] and [[missing]]",
        "exists.md": "## Exists",
    }

    def fake_resolve(_wiki_path, slug):
        return f"{slug}.md" if f"{slug}.md" in bodies else None

    with patch("lore_mcp.server.handle_search", return_value=hits), \
         patch("lore_mcp.server.handle_read", side_effect=lambda path, wiki=None: _read(path, bodies[path])), \
         patch("lore_mcp.server._resolve_slug", side_effect=fake_resolve), \
         patch("lore_mcp.server._resolve_wiki", return_value="WIKI_PATH_STUB"):
        out = handle_drill(query="foo", wiki="private")

    paths = {n["path"] for n in out["result"]["notes"]}
    assert paths == {"foo.md", "exists.md"}
    # `expand` records the slug list (whether resolvable or not).
    # `read_expanded.paths` records only the resolved-and-read paths.
    assert "exists" in out["trace"][2]["wikilinks"]
    assert "missing" in out["trace"][2]["wikilinks"]
    assert out["trace"][3]["paths"] == ["exists.md"]


def test_drill_default_k_is_five():
    from lore_mcp.server import handle_drill

    with patch("lore_mcp.server.handle_search", return_value=[]) as m, \
         patch("lore_mcp.server._resolve_wiki", return_value="WIKI_PATH_STUB"):
        handle_drill(query="x", wiki="private")

    assert m.call_args.kwargs["k"] == 5


def test_drill_does_not_report_truncation_when_under_cap_due_to_unresolvable():
    """Code-reviewer major #1: `truncated`/`kept` only set when the cap actually
    stopped the loop, not when the candidate set was smaller than expand_limit
    after unresolvable slugs were skipped."""
    from lore_mcp.server import handle_drill

    hits = [_hit("hub.md")]
    bodies = {
        "hub.md": "## Hub\n[[a]] [[b]] [[c]] [[d]] [[e]] [[f]]",
        "a.md": "## A",
        "b.md": "## B",
    }

    def fake_resolve(_wiki_path, slug):
        return f"{slug}.md" if f"{slug}.md" in bodies else None

    with patch("lore_mcp.server.handle_search", return_value=hits), \
         patch("lore_mcp.server.handle_read", side_effect=lambda path, wiki=None: _read(path, bodies[path])), \
         patch("lore_mcp.server._resolve_slug", side_effect=fake_resolve), \
         patch("lore_mcp.server._resolve_wiki", return_value="WIKI_PATH_STUB"):
        out = handle_drill(query="hub", wiki="private", expand_limit=5)

    expanded = out["trace"][3]
    # Discovery set has 6 slugs, but only 2 resolve. We never hit the cap of 5
    # because we ran out of resolvable candidates first.
    assert "truncated" not in expanded
    assert "kept" not in expanded
    assert sorted(expanded["paths"]) == ["a.md", "b.md"]


def test_drill_records_read_failures_in_trace():
    """Code-reviewer major #4: silently swallowed read errors leave `paths`
    longer than the actual notes returned. Surface failed reads in the
    trace as `read_failed` so the divergence is debuggable."""
    from lore_mcp.server import handle_drill

    hits = [_hit("good.md"), _hit("broken.md")]
    bodies = {"good.md": "## Good\nno wikilinks"}

    def fake_read(path, wiki=None):
        if path in bodies:
            return _read(path, bodies[path])
        return {"error": {"code": "path_not_found", "message": f"not found: {path}"}}

    with patch("lore_mcp.server.handle_search", return_value=hits), \
         patch("lore_mcp.server.handle_read", side_effect=fake_read), \
         patch("lore_mcp.server._resolve_wiki", return_value="WIKI_PATH_STUB"):
        out = handle_drill(query="x", wiki="private")

    read_step = out["trace"][1]
    assert read_step["paths"] == ["good.md", "broken.md"]
    assert read_step.get("read_failed") == ["broken.md"]
    # Result still excludes the broken note's body — we never invented one.
    assert {n["path"] for n in out["result"]["notes"]} == {"good.md"}
