"""
End-to-end integration tests for the Curator B (graph abstraction) pipeline.

Exercises:
  - Cluster step (middle-tier LLM, mocked)
  - Abstract step (high-tier LLM, mocked)
  - Surface filer (writes concept/decision/... markdown notes)
  - WikiLedger (last_curator_b bump)
  - Auto-briefing integration

All Anthropic calls are intercepted by FakeAnthropic / _FakeMessages, routed
by tool_choice.name ("cluster" vs "abstract").
"""
from __future__ import annotations

import json
import os
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from lore_core.ledger import WikiLedger


_NOW = datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers — session note writer
# ---------------------------------------------------------------------------


def _write_session_note(
    sessions_dir: Path,
    *,
    slug: str,
    scope: str,
    description: str,
    body: str,
):
    """Write a session-shaped markdown file under <sessions_dir>/."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    fm = {
        "schema_version": 2,
        "type": "session",
        "created": _NOW.date().isoformat(),
        "last_reviewed": _NOW.date().isoformat(),
        "description": description,
        "scope": scope,
        "draft": True,
    }
    text = "---\n" + yaml.safe_dump(fm, sort_keys=False).strip() + "\n---\n\n" + body + "\n"
    (sessions_dir / f"{slug}.md").write_text(text)


# ---------------------------------------------------------------------------
# Wiki setup helper
# ---------------------------------------------------------------------------


def _setup_wiki_with_surfaces(
    tmp_path: Path,
    *,
    briefing_auto=False,
    sinks=None,
    models_high="claude-opus-4-7",
):
    """Create lore_root/wiki/private/ with SURFACES.md + .lore-wiki.yml."""
    lore_root = tmp_path / "vault"
    wiki_dir = lore_root / "wiki" / "private"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "sessions").mkdir()

    # Standard SURFACES.md (concept + decision + session)
    (wiki_dir / "SURFACES.md").write_text(
        textwrap.dedent("""\
            # Surfaces — private
            schema_version: 2

            ## concept
            Cross-cutting idea or pattern across sessions.

            ```yaml
            required: [type, created, last_reviewed, description, tags]
            optional: [aliases, superseded_by, draft]
            ```

            Extract when: pattern appears across 3+ session notes.

            ## decision
            A trade-off made.

            ```yaml
            required: [type, created, last_reviewed, description, tags]
            optional: [superseded_by]
            ```

            ## session
            Work session log.

            ```yaml
            required: [type, created, last_reviewed, description]
            optional: [scope, tags, draft]
            ```
            """)
    )

    # .lore-wiki.yml
    cfg = {
        "git": {"auto_commit": False, "auto_push": False, "auto_pull": False},
        "models": {
            "simple": "claude-haiku-4-5",
            "middle": "claude-sonnet-4-6",
            "high": models_high,
        },
        "briefing": {
            "auto": briefing_auto,
            "audience": "personal",
            "sinks": sinks or [],
        },
        "breadcrumb": {"mode": "normal", "scope_filter": True},
    }
    (wiki_dir / ".lore-wiki.yml").write_text(yaml.safe_dump(cfg))
    return lore_root, wiki_dir


# ---------------------------------------------------------------------------
# Fake Anthropic client — route by tool_choice.name
# ---------------------------------------------------------------------------


class _FakeBlock:
    def __init__(self, type_, input_=None, text=None):
        self.type = type_
        self.input = input_
        self.text = text


class _FakeResp:
    def __init__(self, content):
        self.content = content


class _FakeMessages:
    def __init__(self, by_tool):
        self._by_tool = by_tool
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        name = kwargs.get("tool_choice", {}).get("name")
        return self._by_tool.get(name, _FakeResp([]))


class FakeAnthropic:
    def __init__(self, by_tool):
        self.messages = _FakeMessages(by_tool)


# ---------------------------------------------------------------------------
# Pre-built canned responses
# ---------------------------------------------------------------------------


def _cluster_response(clusters):
    return _FakeResp([_FakeBlock("tool_use", input_={"clusters": clusters})])


def _abstract_response(surfaces):
    return _FakeResp([_FakeBlock("tool_use", input_={"surfaces": surfaces})])


# ---------------------------------------------------------------------------
# Helper: parse frontmatter
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    return yaml.safe_load(text[4:end]) or {}


# ---------------------------------------------------------------------------
# Test 1: session notes become surfaces
# ---------------------------------------------------------------------------


def test_graph_e2e_session_notes_become_surfaces(tmp_path):
    """4 session notes → cluster → abstract → concept file written."""
    lore_root, wiki_dir = _setup_wiki_with_surfaces(tmp_path)
    sessions_dir = wiki_dir / "sessions"

    slugs = ["s1", "s2", "s3", "s4"]
    for slug in slugs:
        _write_session_note(
            sessions_dir,
            slug=slug,
            scope="private:passive-capture",
            description=f"Session note {slug} about passive capture",
            body=f"Work done in {slug} on passive capture pipeline.",
        )

    fake = FakeAnthropic({
        "cluster": _cluster_response([
            {
                "topic": "passive capture",
                "scope": "private:passive-capture",
                "session_notes": ["[[s1]]", "[[s2]]", "[[s3]]", "[[s4]]"],
                "suggested_surface": "concept",
            }
        ]),
        "abstract": _abstract_response([
            {
                "surface_name": "concept",
                "title": "Passive Capture Pattern",
                "body": "Body text",
            }
        ]),
    })

    from lore_curator.daily_curator import run_curator_b

    result = run_curator_b(
        lore_root=lore_root,
        wiki="private",
        llm_client=fake,
        dry_run=False,
        now=_NOW,
    )

    # Surface file exists
    concepts_dir = wiki_dir / "concepts"
    assert concepts_dir.exists(), "concepts/ directory should have been created"
    concept_files = list(concepts_dir.glob("*.md"))
    assert len(concept_files) == 1, f"Expected 1 concept file, found {len(concept_files)}: {concept_files}"

    text = concept_files[0].read_text()
    fm = _parse_frontmatter(text)

    assert fm.get("type") == "concept", f"Expected type:concept, got {fm.get('type')}"
    assert fm.get("draft") is True, f"Expected draft:true, got {fm.get('draft')}"

    sources = fm.get("synthesis_sources", [])
    assert sources == ["[[s1]]", "[[s2]]", "[[s3]]", "[[s4]]"], (
        f"Expected synthesis_sources to list the 4 session wikilinks, got {sources}"
    )

    # Pipeline counters
    assert result.notes_considered == 4
    assert result.clusters_formed == 1
    assert len(result.surfaces_emitted) == 1


# ---------------------------------------------------------------------------
# Test 2: auto-briefing publishes after Curator B
# ---------------------------------------------------------------------------


def test_graph_e2e_briefing_auto_publishes_after_curator_b(tmp_path):
    """briefing_auto=True + markdown sink → briefing file written after Curator B."""
    briefing_path = tmp_path / "briefing.md"
    lore_root, wiki_dir = _setup_wiki_with_surfaces(
        tmp_path,
        briefing_auto=True,
        sinks=[f"markdown:{briefing_path}"],
    )
    sessions_dir = wiki_dir / "sessions"
    _write_session_note(
        sessions_dir,
        slug="s1",
        scope="private:passive-capture",
        description="Passive capture session",
        body="We built the passive capture pipeline.",
    )

    fake = FakeAnthropic({
        "cluster": _cluster_response([
            {
                "topic": "passive capture",
                "scope": "private:passive-capture",
                "session_notes": ["[[s1]]"],
                "suggested_surface": "concept",
            }
        ]),
        "abstract": _abstract_response([
            {
                "surface_name": "concept",
                "title": "Passive Capture Pattern",
                "body": "Body text",
            }
        ]),
    })

    from lore_curator.daily_curator import run_curator_b

    result = run_curator_b(
        lore_root=lore_root,
        wiki="private",
        llm_client=fake,
        dry_run=False,
        now=_NOW,
    )

    # Surface was emitted
    assert len(result.surfaces_emitted) == 1

    # Briefing file written (lore_core.briefing.gather is called via _maybe_publish_briefing)
    assert briefing_path.exists(), f"Expected briefing at {briefing_path}"
    content = briefing_path.read_text()
    assert len(content.strip()) > 0, "Briefing file should not be empty"
    # The briefing should contain the wiki name at minimum
    assert "private" in content.lower() or "briefing" in content.lower(), (
        f"Briefing file content unexpected: {content[:200]}"
    )


# ---------------------------------------------------------------------------
# Test 3: no recent notes → no writes, ledger bumped
# ---------------------------------------------------------------------------


def test_graph_e2e_no_recent_notes_no_writes(tmp_path):
    """Empty sessions/ dir → no surfaces, no LLM calls, last_curator_b bumped."""
    lore_root, wiki_dir = _setup_wiki_with_surfaces(tmp_path)
    # sessions dir is empty (created by _setup_wiki_with_surfaces)

    fake = FakeAnthropic({})  # no routes — any call would return _FakeResp([])

    from lore_curator.daily_curator import run_curator_b

    result = run_curator_b(
        lore_root=lore_root,
        wiki="private",
        llm_client=fake,
        dry_run=False,
        now=_NOW,
    )

    # No LLM calls made
    assert fake.messages.calls == [], (
        f"Expected no LLM calls for empty sessions, got {fake.messages.calls}"
    )

    # No surface files written
    concepts_dir = wiki_dir / "concepts"
    if concepts_dir.exists():
        files = list(concepts_dir.glob("*.md"))
        assert files == [], f"Expected no concept files, got {files}"

    # notes_considered == 0
    assert result.notes_considered == 0, f"Expected 0 notes considered, got {result.notes_considered}"

    # last_curator_b was bumped in the wiki ledger
    wledger = WikiLedger(lore_root, "private")
    wentry = wledger.read()
    assert wentry.last_curator_b is not None, "Expected last_curator_b to be bumped"
    assert wentry.last_curator_b == _NOW, (
        f"Expected last_curator_b={_NOW}, got {wentry.last_curator_b}"
    )


# ---------------------------------------------------------------------------
# Test 4: high tier off still emits surfaces + warnings.log
# ---------------------------------------------------------------------------


def test_graph_e2e_high_tier_off_still_emits_surfaces(tmp_path):
    """models_high='off' → abstract runs at middle tier; warnings.log gets marker."""
    lore_root, wiki_dir = _setup_wiki_with_surfaces(
        tmp_path,
        models_high="off",
    )
    sessions_dir = wiki_dir / "sessions"
    for slug in ["s1", "s2", "s3"]:
        _write_session_note(
            sessions_dir,
            slug=slug,
            scope="private:test",
            description=f"Session {slug}",
            body=f"Body of {slug}.",
        )

    fake = FakeAnthropic({
        "cluster": _cluster_response([
            {
                "topic": "test topic",
                "scope": "private:test",
                "session_notes": ["[[s1]]", "[[s2]]", "[[s3]]"],
                "suggested_surface": "concept",
            }
        ]),
        "abstract": _abstract_response([
            {
                "surface_name": "concept",
                "title": "Test Concept",
                "body": "Some concept body.",
            }
        ]),
    })

    from lore_curator.daily_curator import run_curator_b

    result = run_curator_b(
        lore_root=lore_root,
        wiki="private",
        llm_client=fake,
        dry_run=False,
        now=_NOW,
    )

    # Surface file was still emitted
    concepts_dir = wiki_dir / "concepts"
    concept_files = list(concepts_dir.glob("*.md"))
    assert len(concept_files) == 1, (
        f"Expected 1 concept file even with high tier off, got {len(concept_files)}"
    )

    # warnings.log contains the high-off marker
    warnings_log = lore_root / ".lore" / "warnings.log"
    assert warnings_log.exists(), "Expected warnings.log to be created"
    content = warnings_log.read_text()
    assert "abstract-high-tier-off-v1" in content, (
        f"Expected 'abstract-high-tier-off-v1' in warnings.log, got: {content}"
    )


# ---------------------------------------------------------------------------
# Test 5: broken SURFACES.md (no sections) refuses gracefully
# ---------------------------------------------------------------------------


def test_graph_e2e_broken_surfaces_md_refuses(tmp_path):
    """SURFACES.md with no ## sections → surfaces_md_invalid skip, no files written."""
    lore_root = tmp_path / "vault"
    wiki_dir = lore_root / "wiki" / "private"
    wiki_dir.mkdir(parents=True)
    sessions_dir = wiki_dir / "sessions"
    sessions_dir.mkdir()

    # SURFACES.md with NO ## sections — parser yields empty surfaces list
    (wiki_dir / "SURFACES.md").write_text(
        textwrap.dedent("""\
            # Surfaces — private
            schema_version: 2

            This file has no surface sections defined yet.
            Just a preamble with no double-hash headings.
            """)
    )

    # .lore-wiki.yml (minimal)
    cfg = {
        "git": {"auto_commit": False, "auto_push": False, "auto_pull": False},
        "models": {
            "simple": "claude-haiku-4-5",
            "middle": "claude-sonnet-4-6",
            "high": "claude-opus-4-7",
        },
        "briefing": {"auto": False, "audience": "personal", "sinks": []},
        "breadcrumb": {"mode": "normal", "scope_filter": True},
    }
    (wiki_dir / ".lore-wiki.yml").write_text(yaml.safe_dump(cfg))

    # Write a session note so it's not the empty-sessions early-out
    _write_session_note(
        sessions_dir,
        slug="s1",
        scope="private:test",
        description="A session",
        body="Session body.",
    )

    fake = FakeAnthropic({})

    from lore_curator.daily_curator import run_curator_b

    result = run_curator_b(
        lore_root=lore_root,
        wiki="private",
        llm_client=fake,
        dry_run=False,
        now=_NOW,
    )

    assert result.skipped_reasons.get("surfaces_md_invalid") == 1, (
        f"Expected surfaces_md_invalid skip, got skipped_reasons={result.skipped_reasons}"
    )
    # No surface files written anywhere under wiki_dir (exclude sessions/ and SURFACES.md itself)
    all_md = list(wiki_dir.rglob("*.md"))
    surface_outputs = [
        f for f in all_md
        if "sessions" not in str(f) and f.name != "SURFACES.md"
    ]
    assert surface_outputs == [], f"Expected no surface output files, got {surface_outputs}"
