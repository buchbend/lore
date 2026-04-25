"""Tests for lore_curator.curator_b — Curator B pipeline."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lore_core.ledger import WikiLedger


# ---------------------------------------------------------------------------
# Standard SURFACES.md content (the canonical template)
# ---------------------------------------------------------------------------

_STANDARD_SURFACES_MD = """\
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
A trade-off made — alternatives considered, path chosen.

```yaml
required: [type, created, last_reviewed, description, tags]
optional: [superseded_by, implements]
```

Extract when: a session note records a trade-off decision.

## session
Work session log filed by Curator A.

```yaml
required: [type, created, last_reviewed, description]
optional: [scope, tags, draft, source_transcripts]
```
"""

# ---------------------------------------------------------------------------
# Fake Anthropic client — branches on tool_choice.name
# ---------------------------------------------------------------------------


class _FakeContentBlock:
    def __init__(self, type_, input_=None, text=None):
        self.type = type_
        self.input = input_
        self.text = text


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeMessagesAPI:
    """Responds differently based on tool_choice.name ('cluster' vs 'abstract')."""

    def __init__(self, cluster_data: dict, abstract_data: dict):
        self._cluster_data = cluster_data
        self._abstract_data = abstract_data
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        tc = kwargs.get("tool_choice", {})
        name = tc.get("name") if isinstance(tc, dict) else None
        if name == "abstract":
            data = self._abstract_data
        else:
            data = self._cluster_data
        block = _FakeContentBlock(type_="tool_use", input_=data)
        return _FakeResponse([block])


class FakeAnthropicClient:
    def __init__(self, cluster_data: dict, abstract_data: dict):
        self.messages = _FakeMessagesAPI(
            cluster_data=cluster_data,
            abstract_data=abstract_data,
        )


def _make_client(
    *,
    note_wikilinks: list[str] | None = None,
    topic: str = "test topic",
    surface_name: str = "concept",
    title: str = "Test Concept",
    body: str = "This is a test concept body.",
) -> FakeAnthropicClient:
    """Build a fake client that returns 1 cluster + 1 abstracted surface."""
    notes = note_wikilinks or []
    cluster_data = {
        "clusters": [
            {
                "topic": topic,
                "scope": "proj:test",
                "session_notes": notes,
                "suggested_surface": surface_name,
            }
        ]
    }
    abstract_data = {
        "surfaces": [
            {
                "surface_name": surface_name,
                "title": title,
                "body": body,
                "extra_frontmatter": {},
            }
        ]
    }
    return FakeAnthropicClient(cluster_data=cluster_data, abstract_data=abstract_data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)


def _setup_wiki(lore_root: Path, wiki_name: str = "private") -> Path:
    """Create minimal wiki structure + SURFACES.md."""
    wiki_dir = lore_root / "wiki" / wiki_name
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "sessions").mkdir(exist_ok=True)
    (wiki_dir / "SURFACES.md").write_text(_STANDARD_SURFACES_MD)
    return wiki_dir


def _write_session_note(sessions_dir: Path, stem: str, body: str = "") -> Path:
    """Write a fake session note with frontmatter."""
    text = f"""\
---
schema_version: 2
type: session
created: 2026-04-17
last_reviewed: 2026-04-17
description: {stem}
tags: []
---

{body or f'This session was about {stem}.'}
"""
    p = sessions_dir / f"{stem}.md"
    p.write_text(text)
    return p


# ---------------------------------------------------------------------------
# Test 1: empty sessions dir → short-circuit; ledger still bumped
# ---------------------------------------------------------------------------


def test_curator_b_no_recent_notes_short_circuits(tmp_path):
    wiki_dir = _setup_wiki(tmp_path)
    client = _make_client()

    from lore_curator.curator_b import run_curator_b

    result = run_curator_b(
        lore_root=tmp_path,
        wiki="private",
        anthropic_client=client,
        now=_NOW,
        since=_NOW - timedelta(days=3),
    )

    assert result.notes_considered == 0
    # No LLM calls should have been made
    assert client.messages.calls == []
    # Ledger should be bumped to now
    entry = WikiLedger(tmp_path, "private").read()
    assert entry.last_curator_b == _NOW


# ---------------------------------------------------------------------------
# Test 2: cluster → abstract → file surface
# ---------------------------------------------------------------------------


def test_curator_b_regenerates_threads_md_even_with_no_recent_notes(tmp_path):
    """H1 regression: ``threads.md`` must regenerate on every successful
    Curator B run, including the no-recent-notes short-circuit. First-
    install or post-idle users would otherwise never see threads.md
    materialise until the curator successfully clusters."""
    import yaml

    wiki_dir = _setup_wiki(tmp_path)
    sessions_dir = wiki_dir / "sessions" / "2026" / "04"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    # Two old notes (well past any plausible cutoff) sharing a file →
    # they SHOULD surface as a thread even though Curator B's cluster
    # phase finds nothing recent.
    def _write(name, *, files, created):
        fm = {
            "schema_version": 2, "type": "session",
            "created": created, "last_reviewed": created,
            "description": name, "scope": "proj:test",
            "draft": True, "files_touched": files,
        }
        dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
        p = sessions_dir / f"{name}.md"
        p.write_text(f"---\n{dumped}\n---\n\nbody\n")
        # Also force the mtime so _load_recent_session_notes definitely
        # treats them as outside the cutoff.
        import os
        old_ts = (datetime(2026, 1, 1, tzinfo=UTC)).timestamp()
        os.utime(p, (old_ts, old_ts))

    _write("01-auth-day1", files=["auth.py"], created="2026-01-01")
    _write("02-auth-day2", files=["auth.py"], created="2026-01-02")

    # Cluster client returns nothing — irrelevant since notes_considered
    # will be 0 anyway, but we still need to provide one.
    client = _make_client(note_wikilinks=[], surface_name="concept",
                          title="x", body="y")

    from lore_curator.curator_b import run_curator_b
    result = run_curator_b(
        lore_root=tmp_path,
        wiki="private",
        anthropic_client=client,
        now=_NOW,
        since=_NOW - timedelta(hours=1),  # narrow window: nothing recent
    )

    assert result.notes_considered == 0  # confirm we hit the early-return path
    threads_md = wiki_dir / "threads.md"
    assert threads_md.exists(), \
        "threads.md must regenerate even when no recent notes triggered clustering"
    text = threads_md.read_text()
    assert "[[01-auth-day1]]" in text
    assert "[[02-auth-day2]]" in text


def test_curator_b_writes_threads_md_at_wiki_root(tmp_path):
    """Phase D: each Curator B run regenerates ``threads.md`` at the
    wiki root from session-note ``files_touched`` frontmatter. Pure
    algorithm — no LLM call, no back-patching of the source notes."""
    import yaml

    wiki_dir = _setup_wiki(tmp_path)
    sessions_dir = wiki_dir / "sessions" / "2026" / "04"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    # Two notes share auth.py → one thread. A solo schema.sql note has
    # no peer and is not a thread.
    def _write(name, *, files, created):
        fm = {
            "schema_version": 2, "type": "session",
            "created": created, "last_reviewed": created,
            "description": name, "scope": "proj:test",
            "draft": True, "files_touched": files,
        }
        dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
        (sessions_dir / f"{name}.md").write_text(f"---\n{dumped}\n---\n\nbody\n")

    _write("23-auth-day1", files=["auth.py"], created="2026-04-23")
    _write("24-auth-day2", files=["auth.py", "helpers.py"], created="2026-04-24")
    _write("24-schema",   files=["schema.sql"],          created="2026-04-24")

    note_stems = [p.stem for p in sessions_dir.glob("*.md")]
    note_wikilinks = [f"[[{s}]]" for s in note_stems]
    client = _make_client(
        note_wikilinks=note_wikilinks,
        surface_name="concept",
        title="Test Concept",
        body="x",
    )

    from lore_curator.curator_b import run_curator_b
    run_curator_b(
        lore_root=tmp_path,
        wiki="private",
        anthropic_client=client,
        now=_NOW,
        since=_NOW - timedelta(days=30),
    )

    threads_md = wiki_dir / "threads.md"
    assert threads_md.exists(), "Curator B must regenerate threads.md"
    text = threads_md.read_text()
    # Both auth-day notes appear (the thread); the schema solo note
    # does NOT appear because it has no peer to thread with.
    assert "[[23-auth-day1]]" in text
    assert "[[24-auth-day2]]" in text
    assert "[[24-schema]]" not in text


def test_curator_b_clusters_then_abstracts_then_files(tmp_path):
    wiki_dir = _setup_wiki(tmp_path)
    sessions_dir = wiki_dir / "sessions"

    for i in range(3):
        _write_session_note(sessions_dir, f"2026-04-1{5+i}-work")

    note_stems = [p.stem for p in sessions_dir.glob("*.md")]
    note_wikilinks = [f"[[{s}]]" for s in note_stems]
    client = _make_client(
        note_wikilinks=note_wikilinks,
        surface_name="concept",
        title="Test Concept",
        body="A cross-cutting concept.",
    )

    from lore_curator.curator_b import run_curator_b

    result = run_curator_b(
        lore_root=tmp_path,
        wiki="private",
        anthropic_client=client,
        now=_NOW,
        since=_NOW - timedelta(days=7),
    )

    assert result.notes_considered == 3
    assert result.clusters_formed == 1
    assert len(result.surfaces_emitted) == 1

    surface_path = result.surfaces_emitted[0]
    assert surface_path.exists(), f"Expected file at {surface_path}"
    # Should be under concepts/
    assert "concepts" in str(surface_path)
    assert surface_path.suffix == ".md"


# ---------------------------------------------------------------------------
# Test 3: filed surfaces always have draft: true in frontmatter
# ---------------------------------------------------------------------------


def test_curator_b_files_surfaces_with_draft_true(tmp_path):
    wiki_dir = _setup_wiki(tmp_path)
    sessions_dir = wiki_dir / "sessions"
    _write_session_note(sessions_dir, "2026-04-15-alpha")
    _write_session_note(sessions_dir, "2026-04-16-beta")

    note_wikilinks = [f"[[2026-04-1{5+i}-{n}]]" for i, n in enumerate(["alpha", "beta"])]
    client = _make_client(
        note_wikilinks=note_wikilinks,
        surface_name="concept",
        title="Draft Concept",
        body="Should be filed as draft.",
    )

    from lore_curator.curator_b import run_curator_b

    result = run_curator_b(
        lore_root=tmp_path,
        wiki="private",
        anthropic_client=client,
        now=_NOW,
        since=_NOW - timedelta(days=7),
    )

    assert len(result.surfaces_emitted) == 1
    path = result.surfaces_emitted[0]
    assert path.exists()

    import yaml as _yaml

    text = path.read_text()
    # Strip frontmatter
    assert text.startswith("---")
    end = text.find("\n---", 3)
    fm_text = text[4:end]
    fm = _yaml.safe_load(fm_text)
    assert fm.get("draft") is True


# ---------------------------------------------------------------------------
# Test 4: ledger last_curator_b advances to `now`
# ---------------------------------------------------------------------------


def test_curator_b_advances_last_curator_b_on_wiki_ledger(tmp_path):
    _setup_wiki(tmp_path)

    from lore_curator.curator_b import run_curator_b

    run_curator_b(
        lore_root=tmp_path,
        wiki="private",
        anthropic_client=_make_client(),
        now=_NOW,
        since=_NOW - timedelta(days=3),
    )

    entry = WikiLedger(tmp_path, "private").read()
    assert entry.last_curator_b == _NOW


# ---------------------------------------------------------------------------
# Test 5: dry_run writes nothing; ledger NOT bumped
# ---------------------------------------------------------------------------


def test_curator_b_dry_run_writes_nothing(tmp_path):
    wiki_dir = _setup_wiki(tmp_path)
    sessions_dir = wiki_dir / "sessions"
    _write_session_note(sessions_dir, "2026-04-15-note1")
    _write_session_note(sessions_dir, "2026-04-16-note2")

    note_wikilinks = ["[[2026-04-15-note1]]", "[[2026-04-16-note2]]"]
    client = _make_client(
        note_wikilinks=note_wikilinks,
        surface_name="concept",
        title="Dry Run Concept",
        body="Should not be written.",
    )

    from lore_curator.curator_b import run_curator_b

    result = run_curator_b(
        lore_root=tmp_path,
        wiki="private",
        anthropic_client=client,
        dry_run=True,
        now=_NOW,
        since=_NOW - timedelta(days=7),
    )

    # surfaces_emitted contains pseudo paths
    assert len(result.surfaces_emitted) >= 1
    for p in result.surfaces_emitted:
        assert not p.exists(), f"Dry run should not create {p}"

    # concepts dir should not exist (or be empty)
    concepts_dir = wiki_dir / "concepts"
    if concepts_dir.exists():
        assert list(concepts_dir.glob("*.md")) == []

    # Ledger should NOT be bumped
    entry = WikiLedger(tmp_path, "private").read()
    assert entry.last_curator_b is None


# ---------------------------------------------------------------------------
# Test 6: lock contention records skip
# ---------------------------------------------------------------------------


def test_curator_b_lock_contention_records_skip(tmp_path):
    _setup_wiki(tmp_path)

    # Pre-create lock directory to simulate a held lock
    lock_dir = tmp_path / ".lore" / "curator.lock"
    lock_dir.mkdir(parents=True)

    try:
        from lore_curator.curator_b import run_curator_b

        result = run_curator_b(
            lore_root=tmp_path,
            wiki="private",
            anthropic_client=_make_client(),
            now=_NOW,
        )

        assert result.skipped_reasons.get("lock_contended", 0) == 1
    finally:
        try:
            os.rmdir(lock_dir)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Test 7: no anthropic client → records skip
# ---------------------------------------------------------------------------


def test_curator_b_no_anthropic_client_records_skip(tmp_path):
    _setup_wiki(tmp_path)

    from lore_curator.curator_b import run_curator_b

    result = run_curator_b(
        lore_root=tmp_path,
        wiki="private",
        anthropic_client=None,
        now=_NOW,
    )

    assert result.skipped_reasons.get("no_anthropic_client", 0) >= 1
    # No surfaces written
    concepts_dir = tmp_path / "wiki" / "private" / "concepts"
    assert not concepts_dir.exists() or list(concepts_dir.glob("*.md")) == []


# ---------------------------------------------------------------------------
# Test 8: broken SURFACES.md (empty surfaces list) refuses to run
# ---------------------------------------------------------------------------


def test_curator_b_broken_surfaces_md_refuses_to_run(tmp_path):
    wiki_dir = tmp_path / "wiki" / "private"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "sessions").mkdir()

    # Write a SURFACES.md that exists but has no ## sections, so it parses
    # to zero usable surfaces — the "broken" case the code must refuse.
    broken_surfaces = """\
# Surfaces — private
schema_version: 2

This file has no surface sections defined.
"""
    (wiki_dir / "SURFACES.md").write_text(broken_surfaces)

    from lore_curator.curator_b import run_curator_b

    result = run_curator_b(
        lore_root=tmp_path,
        wiki="private",
        anthropic_client=_make_client(),
        now=_NOW,
    )

    assert result.skipped_reasons.get("surfaces_md_invalid", 0) == 1


# ---------------------------------------------------------------------------
# Test 9: high_tier_off still runs; warnings.log gets the marker
# ---------------------------------------------------------------------------


def test_curator_b_high_tier_off_still_runs_with_warning(tmp_path):
    wiki_dir = _setup_wiki(tmp_path)
    sessions_dir = wiki_dir / "sessions"
    _write_session_note(sessions_dir, "2026-04-15-alpha")
    _write_session_note(sessions_dir, "2026-04-16-beta")

    # Write a wiki config with high tier off
    wiki_config_text = """\
models:
  high: "off"
  middle: claude-sonnet-4-6
  simple: claude-haiku-4-5
"""
    (wiki_dir / ".lore-wiki.yml").write_text(wiki_config_text)

    note_wikilinks = ["[[2026-04-15-alpha]]", "[[2026-04-16-beta]]"]
    client = _make_client(
        note_wikilinks=note_wikilinks,
        surface_name="concept",
        title="High Off Concept",
        body="Uses middle tier.",
    )

    from lore_curator.curator_b import run_curator_b

    result = run_curator_b(
        lore_root=tmp_path,
        wiki="private",
        anthropic_client=client,
        now=_NOW,
        since=_NOW - timedelta(days=7),
    )

    # Should still emit surfaces (using middle-tier fallback)
    assert len(result.surfaces_emitted) >= 1

    # warnings.log should contain the high-off marker
    warnings_log = tmp_path / ".lore" / "warnings.log"
    assert warnings_log.exists(), "warnings.log should exist"
    content = warnings_log.read_text()
    assert "abstract-high-tier-off-v1" in content


# ---------------------------------------------------------------------------
# Test 10: wiki_not_found records skip
# ---------------------------------------------------------------------------


def test_curator_b_wiki_not_found_records_skip(tmp_path):
    from lore_curator.curator_b import run_curator_b

    result = run_curator_b(
        lore_root=tmp_path,
        wiki="nonexistent",
        anthropic_client=_make_client(),
        now=_NOW,
    )

    assert result.skipped_reasons.get("wiki_not_found", 0) == 1


# ---------------------------------------------------------------------------
# Existing-surface awareness — Curator B reads the inventory + handles merge_into
# ---------------------------------------------------------------------------


def _write_existing_concept(wiki_dir: Path, slug: str, description: str) -> Path:
    """Helper to drop an existing concept note into wiki/concepts/."""
    concepts_dir = wiki_dir / "concepts"
    concepts_dir.mkdir(exist_ok=True)
    text = f"""\
---
schema_version: 2
type: concept
created: '2026-04-10'
last_reviewed: '2026-04-10'
description: {description}
tags: []
---

Existing concept body.
"""
    p = concepts_dir / f"{slug}.md"
    p.write_text(text)
    return p


def test_load_existing_surfaces_returns_inventory_per_surface(tmp_path):
    """_load_existing_surfaces walks <wiki>/<plural>/ and returns wikilink+description per note."""
    from lore_core.surfaces import load_surfaces_or_default
    from lore_curator.curator_b import _load_existing_surfaces

    wiki_dir = _setup_wiki(tmp_path)
    _write_existing_concept(wiki_dir, "host-adapter-layer",
                            "Per-host adapter normalising tool-call shape.")
    _write_existing_concept(wiki_dir, "curator-spawn-backpressure",
                            "Three layers preventing curator spawn storms.")

    surfaces_doc = load_surfaces_or_default(wiki_dir)
    inventory = _load_existing_surfaces(wiki_dir, surfaces_doc)

    concept_entries = inventory.get("concept", [])
    wikilinks = sorted(item["wikilink"] for item in concept_entries)
    assert wikilinks == ["[[curator-spawn-backpressure]]", "[[host-adapter-layer]]"]
    descriptions = {item["wikilink"]: item["description"] for item in concept_entries}
    assert "Per-host adapter" in descriptions["[[host-adapter-layer]]"]


def test_load_existing_surfaces_skips_session_surface(tmp_path):
    """The session surface lives in sessions/ and is curator-A territory; skip it."""
    from lore_core.surfaces import load_surfaces_or_default
    from lore_curator.curator_b import _load_existing_surfaces

    wiki_dir = _setup_wiki(tmp_path)
    _write_session_note(wiki_dir / "sessions", "2026-04-15-some-work")
    surfaces_doc = load_surfaces_or_default(wiki_dir)

    inventory = _load_existing_surfaces(wiki_dir, surfaces_doc)
    # 'session' surface should not appear (sessions are Curator A's job;
    # listing them as merge candidates for Curator B is a category error).
    assert "session" not in inventory


def test_curator_b_skips_filing_when_llm_suggests_merge(tmp_path):
    """When the abstract LLM returns merge_into, no new note is written; merge-suggested logged."""
    wiki_dir = _setup_wiki(tmp_path)
    sessions_dir = wiki_dir / "sessions"
    _write_session_note(sessions_dir, "2026-04-17-some-work")
    _write_existing_concept(wiki_dir, "existing-thing",
                            "Some pre-existing concept to merge into.")

    note_wiki = "[[2026-04-17-some-work]]"
    cluster_data = {
        "clusters": [{
            "topic": "merge candidate",
            "scope": "proj:test",
            "session_notes": [note_wiki],
            "suggested_surface": "concept",
        }]
    }
    abstract_data = {
        "surfaces": [{
            "surface_name": "concept",
            "title": "extended thing",
            "body": "would-merge body",
            "merge_into": "[[existing-thing]]",
        }]
    }
    client = FakeAnthropicClient(cluster_data=cluster_data, abstract_data=abstract_data)

    from lore_curator.curator_b import run_curator_b

    result = run_curator_b(
        lore_root=tmp_path,
        wiki="private",
        anthropic_client=client,
        now=_NOW,
        since=_NOW - timedelta(days=30),
    )

    # No new concept written.
    new_concepts = list((wiki_dir / "concepts").glob("*.md"))
    assert len(new_concepts) == 1, \
        f"expected only the pre-existing concept, found: {[p.name for p in new_concepts]}"
    assert new_concepts[0].name == "existing-thing.md"
    # Result tracks no surfaces emitted.
    assert result.surfaces_emitted == []

    # The run-log must record a typed `merge-suggested` event (NOT a
    # downgraded `warning` — that would mean RECORD_TYPES is missing the
    # entry and downstream tooling can't filter on it).
    import json
    run_files = sorted((tmp_path / ".lore" / "runs").glob("*.jsonl"))
    assert run_files, "expected a run-log file"
    records = [json.loads(line) for line in run_files[-1].read_text().splitlines()]
    merge_events = [r for r in records if r.get("type") == "merge-suggested"]
    assert len(merge_events) == 1, \
        f"expected exactly one merge-suggested event, got types: {[r.get('type') for r in records]}"
    event = merge_events[0]
    assert event["merge_into"] == "[[existing-thing]]"
    assert event["surface_name"] == "concept"


def test_curator_b_passes_existing_surfaces_to_abstract_call(tmp_path):
    """Curator B must inject the existing-surfaces inventory into the abstract LLM call."""
    wiki_dir = _setup_wiki(tmp_path)
    sessions_dir = wiki_dir / "sessions"
    _write_session_note(sessions_dir, "2026-04-17-some-work")
    _write_existing_concept(wiki_dir, "preexisting-anchor",
                            "Anchor concept for the test.")

    client = _make_client(
        note_wikilinks=["[[2026-04-17-some-work]]"],
        surface_name="concept",
        title="new thing",
        body="x",
    )

    from lore_curator.curator_b import run_curator_b
    run_curator_b(
        lore_root=tmp_path,
        wiki="private",
        anthropic_client=client,
        now=_NOW,
        since=_NOW - timedelta(days=30),
    )

    # Find the abstract call (tool_choice.name == 'abstract') and confirm
    # the inventory wikilink reaches the LLM prompt.
    abstract_calls = [
        c for c in client.messages.calls
        if c.get("tool_choice", {}).get("name") == "abstract"
    ]
    assert abstract_calls, "expected an abstract call"
    prompt = abstract_calls[0]["messages"][0]["content"]
    assert "[[preexisting-anchor]]" in prompt
