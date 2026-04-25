"""Tests for Curator B + briefing auto-trigger integration."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from lore_core.ledger import WikiLedger

# ---------------------------------------------------------------------------
# Shared fixtures from test_curator_b (re-declared here to keep tests isolated)
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

_NOW = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)

_BRIEFING_AUTO_YML = """\
briefing:
  auto: true
  sinks: []
"""


class _FakeContentBlock:
    def __init__(self, type_, input_=None, text=None):
        self.type = type_
        self.input = input_
        self.text = text


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeMessagesAPI:
    def __init__(self, cluster_data: dict, abstract_data: dict):
        self._cluster_data = cluster_data
        self._abstract_data = abstract_data
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        tc = kwargs.get("tool_choice", {})
        name = tc.get("name") if isinstance(tc, dict) else None
        data = self._abstract_data if name == "abstract" else self._cluster_data
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


def _setup_wiki(lore_root: Path, wiki_name: str = "private", briefing_yml: str | None = None) -> Path:
    wiki_dir = lore_root / "wiki" / wiki_name
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "sessions").mkdir(exist_ok=True)
    (wiki_dir / "SURFACES.md").write_text(_STANDARD_SURFACES_MD)
    if briefing_yml is not None:
        (wiki_dir / ".lore-wiki.yml").write_text(briefing_yml)
    return wiki_dir


def _write_session_note(sessions_dir: Path, stem: str, body: str = "") -> Path:
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


def _run_with_one_cluster(lore_root: Path, wiki: str = "private", **kwargs):
    """Run curator_b with 3 session notes and one fake cluster/abstract."""
    from lore_curator.curator_b import run_curator_b

    wiki_dir = lore_root / "wiki" / wiki
    sessions_dir = wiki_dir / "sessions"
    for i in range(3):
        _write_session_note(sessions_dir, f"2026-04-1{5+i}-work")

    note_stems = [p.stem for p in sessions_dir.glob("*.md")]
    note_wikilinks = [f"[[{s}]]" for s in note_stems]
    client = _make_client(note_wikilinks=note_wikilinks)

    return run_curator_b(
        lore_root=lore_root,
        wiki=wiki,
        llm_client=client,
        now=_NOW,
        since=_NOW - timedelta(days=7),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Canned gather result for monkeypatching
# ---------------------------------------------------------------------------

_CANNED_GATHER = {
    "wiki": "private",
    "today": "2026-04-18",
    "ledger": {"last_briefing": None, "incorporated_count": 0},
    "sink_config": None,
    "new_sessions": [
        {
            "path": "sessions/2026-04-17-work.md",
            "date": "2026-04-17",
            "slug": "work",
            "frontmatter": {"description": "test session", "type": "session"},
        }
    ],
}


# ---------------------------------------------------------------------------
# Test 1: briefing.auto=true writes the markdown sink
# ---------------------------------------------------------------------------


def test_curator_b_publishes_briefing_when_config_auto_true(tmp_path, monkeypatch):
    briefing_out = tmp_path / "briefing.md"
    yml = f"""\
briefing:
  auto: true
  sinks:
    - "markdown:{briefing_out}"
"""
    _setup_wiki(tmp_path, briefing_yml=yml)

    import lore_core.briefing as _b

    monkeypatch.setattr(_b, "gather", lambda **kw: _CANNED_GATHER)

    result = _run_with_one_cluster(tmp_path)

    assert result.surfaces_emitted, "Expected at least one surface emitted"
    assert briefing_out.exists(), f"Expected briefing at {briefing_out}"
    content = briefing_out.read_text()
    assert "# Lore briefing" in content


# ---------------------------------------------------------------------------
# Test 2: briefing.auto=false → no briefing written
# ---------------------------------------------------------------------------


def test_curator_b_skips_briefing_when_config_auto_false(tmp_path, monkeypatch):
    briefing_out = tmp_path / "briefing.md"
    yml = f"""\
briefing:
  auto: false
  sinks:
    - "markdown:{briefing_out}"
"""
    _setup_wiki(tmp_path, briefing_yml=yml)

    import lore_core.briefing as _b

    monkeypatch.setattr(_b, "gather", lambda **kw: _CANNED_GATHER)

    result = _run_with_one_cluster(tmp_path)

    assert result.surfaces_emitted, "Expected surfaces emitted"
    assert not briefing_out.exists(), "Briefing should NOT be written when auto=false"


# ---------------------------------------------------------------------------
# Test 3: briefing failure does not break Curator B
# ---------------------------------------------------------------------------


def test_curator_b_briefing_failure_does_not_break_curator(tmp_path, monkeypatch):
    briefing_out = tmp_path / "briefing.md"
    yml = f"""\
briefing:
  auto: true
  sinks:
    - "markdown:{briefing_out}"
"""
    _setup_wiki(tmp_path, briefing_yml=yml)

    import lore_curator.curator_b as _cb

    def _raising_publish(**kw):
        raise RuntimeError("simulated briefing failure")

    monkeypatch.setattr(_cb, "_maybe_publish_briefing", _raising_publish)

    result = _run_with_one_cluster(tmp_path)

    assert result.surfaces_emitted, "Surfaces should still be filed even when briefing fails"
    # No exception should have propagated.


# ---------------------------------------------------------------------------
# Test 4: last_briefing ledger field updated on success
# ---------------------------------------------------------------------------


def test_curator_b_advances_last_briefing_on_success(tmp_path, monkeypatch):
    briefing_out = tmp_path / "briefing.md"
    yml = f"""\
briefing:
  auto: true
  sinks:
    - "markdown:{briefing_out}"
"""
    _setup_wiki(tmp_path, briefing_yml=yml)

    import lore_core.briefing as _b

    monkeypatch.setattr(_b, "gather", lambda **kw: _CANNED_GATHER)

    result = _run_with_one_cluster(tmp_path)

    assert result.surfaces_emitted
    entry = WikiLedger(tmp_path, "private").read()
    assert entry.last_briefing is not None, "last_briefing should be set after successful publish"
    assert entry.last_briefing == _NOW


# ---------------------------------------------------------------------------
# Test 5: dry_run=True skips briefing even with auto=true
# ---------------------------------------------------------------------------


def test_curator_b_dry_run_does_not_publish_briefing(tmp_path, monkeypatch):
    briefing_out = tmp_path / "briefing.md"
    yml = f"""\
briefing:
  auto: true
  sinks:
    - "markdown:{briefing_out}"
"""
    _setup_wiki(tmp_path, briefing_yml=yml)

    import lore_core.briefing as _b

    monkeypatch.setattr(_b, "gather", lambda **kw: _CANNED_GATHER)

    result = _run_with_one_cluster(tmp_path, dry_run=True)

    # dry_run still populates surfaces_emitted (with <dry-run:...> paths)
    assert result.surfaces_emitted
    assert not briefing_out.exists(), "Briefing must NOT be written in dry_run mode"
    entry = WikiLedger(tmp_path, "private").read()
    assert entry.last_briefing is None, "last_briefing must not be set in dry_run"


# ---------------------------------------------------------------------------
# Test 6: zero surfaces emitted → no briefing
# ---------------------------------------------------------------------------


def test_curator_b_no_surfaces_emitted_no_briefing(tmp_path, monkeypatch):
    briefing_out = tmp_path / "briefing.md"
    yml = f"""\
briefing:
  auto: true
  sinks:
    - "markdown:{briefing_out}"
"""
    _setup_wiki(tmp_path, briefing_yml=yml)

    import lore_core.briefing as _b

    monkeypatch.setattr(_b, "gather", lambda **kw: _CANNED_GATHER)

    from lore_curator.curator_b import run_curator_b

    # No session notes → zero surfaces emitted.
    client = _make_client()
    result = run_curator_b(
        lore_root=tmp_path,
        wiki="private",
        llm_client=client,
        now=_NOW,
        since=_NOW - timedelta(days=7),
    )

    assert result.surfaces_emitted == []
    assert not briefing_out.exists(), "Briefing must NOT be written when no surfaces emitted"


# ---------------------------------------------------------------------------
# Test 7: unsupported sink → logged and skipped, no crash
# ---------------------------------------------------------------------------


def test_curator_b_unsupported_sink_logged_and_skipped(tmp_path, monkeypatch):
    yml = """\
briefing:
  auto: true
  sinks:
    - "matrix:#dev-notes"
"""
    _setup_wiki(tmp_path, briefing_yml=yml)

    import lore_core.briefing as _b

    monkeypatch.setattr(_b, "gather", lambda **kw: _CANNED_GATHER)

    result = _run_with_one_cluster(tmp_path)

    assert result.surfaces_emitted, "Surfaces should still be emitted"

    # Curator log should mention "skipping"
    log_path = tmp_path / ".lore" / "curator.log"
    assert log_path.exists(), "curator.log should exist"
    log_content = log_path.read_text()
    assert "skipping" in log_content.lower(), f"Expected 'skipping' in log, got: {log_content!r}"
