"""Phase 2: Curator B partitions session-note input by exact ``scope:``
value before clustering.

Each cluster the LLM produces is bounded to a single scope. Notes with
empty/missing ``scope:`` form an "unscoped" group and emit a hook event
so the user can fix them. Mismatched LLM cluster-scopes (LLM emits a
different scope than the input partition) are overridden and warned.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml


_NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


# Reuse the wiki setup from test_curator_b.
def _setup_wiki(tmp_path: Path) -> Path:
    """Minimal vault layout that ``run_curator_b`` accepts."""
    (tmp_path / ".lore").mkdir(parents=True, exist_ok=True)
    wiki_root = tmp_path / "wiki" / "private"
    sessions_dir = wiki_root / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (wiki_root / "concepts").mkdir(exist_ok=True)
    (wiki_root / "decisions").mkdir(exist_ok=True)
    (wiki_root / "papers").mkdir(exist_ok=True)
    (wiki_root / "SURFACES.md").write_text(_SURFACES_MD)
    return wiki_root


_SURFACES_MD = """\
# Surfaces — private
schema_version: 2

## concept
A concept.

```yaml
required: [type, created, last_reviewed, description, tags]
plural: concepts
extract_prompt: extract a concept
```

Extract when: applicable.

## decision
A decision.

```yaml
required: [type, created, last_reviewed, description, tags]
plural: decisions
extract_prompt: extract a decision
```

Extract when: choice with rationale.

## session
Work session.

```yaml
required: [type, created, last_reviewed, description]
plural: sessions
authored_by: curator_a
```
"""


class _ScopeAwareClusterClient:
    """Anthropic-shaped fake whose cluster output mirrors the input
    scopes (so the test can assert that Curator B passes scope-uniform
    inputs to ``cluster_session_notes``)."""

    def __init__(self):
        self.cluster_calls: list[dict] = []

    @property
    def messages(self):
        return self

    def create(self, *, model, max_tokens, tools, tool_choice, messages):
        prompt_text = messages[0]["content"]

        class _Block:
            def __init__(self, payload):
                self.type = "tool_use"
                self.input = payload

        class _Resp:
            def __init__(self, payload):
                self.content = [_Block(payload)]

        # Only record cluster calls — the curator also makes thread-label
        # calls (`threads.py`) and abstract calls (`abstract.py`).
        is_cluster_call = prompt_text.startswith("You are clustering recent session notes")
        if not is_cluster_call:
            # Stub responses for non-cluster calls so the curator can keep
            # running. Threads-labeller wants {"label": "..."}; abstract
            # wants a no-op surface decision.
            if prompt_text.startswith("Produce a concise topical label"):
                return _Resp({"label": "test thread"})
            # Abstract: signal "no surfaces extracted"
            return _Resp({"surfaces": []})

        # Record the scopes that appear in the prompt — that's the
        # contract Phase 2 enforces (one scope per call).
        scopes_in_prompt = set()
        for line in prompt_text.splitlines():
            line = line.strip()
            if line.startswith("scope:"):
                scopes_in_prompt.add(line.split(":", 1)[1].strip())
        self.cluster_calls.append({"scopes": scopes_in_prompt, "prompt": prompt_text})

        # Return a dummy cluster pinned to whichever single scope appears.
        scope = next(iter(scopes_in_prompt)) if scopes_in_prompt else ""
        wikilinks: list[str] = []
        for line in prompt_text.splitlines():
            line = line.strip()
            if line.startswith("- path:"):
                wikilinks.append(line.split(":", 1)[1].strip())

        tool_payload = {
            "clusters": [
                {
                    "topic": "test-topic",
                    "scope": scope,
                    "session_notes": wikilinks[:5],
                    "suggested_surface": None,
                }
            ]
        }
        return _Resp(tool_payload)


def _write_session_with_scope(sessions_dir: Path, slug: str, scope: str, created: str):
    sessions_dir.mkdir(parents=True, exist_ok=True)
    fm = {
        "schema_version": 2,
        "type": "session",
        "created": created,
        "last_reviewed": created,
        "description": f"session {slug}",
        "scope": scope,
        "draft": True,
        "files_touched": ["foo.py"],
    }
    if not scope:
        fm.pop("scope")
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    (sessions_dir / f"{slug}.md").write_text(
        f"---\n{dumped}\n---\n\n## Summary\n\nbody here.\n"
    )


def test_curator_b_partitions_by_scope_exact_match(tmp_path):
    """Notes from two different scopes form two separate cluster calls,
    each with scope-uniform input. The LLM never sees notes from
    multiple scopes in the same prompt."""
    from lore_curator.daily_curator import run_curator_b

    wiki_dir = _setup_wiki(tmp_path)
    sessions_dir = wiki_dir / "sessions" / "2026" / "04"
    _write_session_with_scope(sessions_dir, "01-ops", "ccat:ops-db", "2026-04-28")
    _write_session_with_scope(sessions_dir, "02-ops", "ccat:ops-db", "2026-04-28")
    _write_session_with_scope(sessions_dir, "03-xfer", "ccat:data-transfer", "2026-04-28")
    _write_session_with_scope(sessions_dir, "04-xfer", "ccat:data-transfer", "2026-04-28")

    client = _ScopeAwareClusterClient()
    run_curator_b(
        lore_root=tmp_path,
        wiki="private",
        llm_client=client,
        now=_NOW,
        since=_NOW - timedelta(days=30),
    )

    # One cluster call per distinct scope. The fake records the scopes
    # it saw in each prompt — assert both calls are scope-uniform.
    assert len(client.cluster_calls) == 2, (
        f"expected 2 cluster calls (one per scope); got {len(client.cluster_calls)}"
    )
    for call in client.cluster_calls:
        assert len(call["scopes"]) == 1, (
            f"each cluster call must be scope-uniform; got {call['scopes']}"
        )
    seen_scopes = {next(iter(c["scopes"])) for c in client.cluster_calls}
    assert seen_scopes == {"ccat:ops-db", "ccat:data-transfer"}


def test_curator_b_does_not_treat_subtree_as_same_scope(tmp_path):
    """Strict, exact-match: a parent scope and a child scope are two
    separate groups. The Phase 2 contract is exact match, not subtree."""
    from lore_curator.daily_curator import run_curator_b

    wiki_dir = _setup_wiki(tmp_path)
    sessions_dir = wiki_dir / "sessions" / "2026" / "04"
    _write_session_with_scope(sessions_dir, "01-parent", "ccat", "2026-04-28")
    _write_session_with_scope(sessions_dir, "02-child", "ccat:ops-db", "2026-04-28")

    client = _ScopeAwareClusterClient()
    run_curator_b(
        lore_root=tmp_path,
        wiki="private",
        llm_client=client,
        now=_NOW,
        since=_NOW - timedelta(days=30),
    )

    assert len(client.cluster_calls) == 2
    seen_scopes = {next(iter(c["scopes"])) for c in client.cluster_calls}
    assert seen_scopes == {"ccat", "ccat:ops-db"}


def test_curator_b_unscoped_notes_form_their_own_group(tmp_path):
    """Notes with empty/missing ``scope:`` cluster together (unscoped
    group) and emit a hook event so the user can fix them."""
    from lore_curator.daily_curator import run_curator_b

    wiki_dir = _setup_wiki(tmp_path)
    sessions_dir = wiki_dir / "sessions" / "2026" / "04"
    _write_session_with_scope(sessions_dir, "01-noscope", "", "2026-04-28")
    _write_session_with_scope(sessions_dir, "02-scoped", "ccat:ops-db", "2026-04-28")

    client = _ScopeAwareClusterClient()
    run_curator_b(
        lore_root=tmp_path,
        wiki="private",
        llm_client=client,
        now=_NOW,
        since=_NOW - timedelta(days=30),
    )

    assert len(client.cluster_calls) == 2
    seen_scopes = {next(iter(c["scopes"])) for c in client.cluster_calls if c["scopes"]}
    # Scoped cluster call should be present.
    assert "ccat:ops-db" in seen_scopes

    # Hook event log should contain an "unscoped-notes" emission.
    runs_dir = tmp_path / ".lore" / "runs"
    assert runs_dir.exists()
    found_unscoped_event = False
    for jsonl in runs_dir.rglob("*.jsonl"):
        for line in jsonl.read_text().splitlines():
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if event.get("type") == "unscoped-notes":
                found_unscoped_event = True
                assert event.get("count", 0) >= 1
    assert found_unscoped_event, (
        "Curator B must emit an 'unscoped-notes' hook event "
        "when sessions lack scope:"
    )


def test_curator_b_single_scope_still_runs_one_cluster_call(tmp_path):
    """No regression: a wiki where every recent note shares one scope
    still does exactly one cluster call (not zero, not two)."""
    from lore_curator.daily_curator import run_curator_b

    wiki_dir = _setup_wiki(tmp_path)
    sessions_dir = wiki_dir / "sessions" / "2026" / "04"
    _write_session_with_scope(sessions_dir, "01-a", "proj:test", "2026-04-28")
    _write_session_with_scope(sessions_dir, "02-b", "proj:test", "2026-04-28")

    client = _ScopeAwareClusterClient()
    run_curator_b(
        lore_root=tmp_path,
        wiki="private",
        llm_client=client,
        now=_NOW,
        since=_NOW - timedelta(days=30),
    )

    assert len(client.cluster_calls) == 1
    assert client.cluster_calls[0]["scopes"] == {"proj:test"}
