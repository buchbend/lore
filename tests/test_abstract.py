"""Tests for lore_curator.abstract — cluster abstraction step."""
from __future__ import annotations

from pathlib import Path

import pytest

from lore_curator.abstract import abstract_cluster, AbstractedSurface, _HIGH_OFF_WARNING_ID
from lore_curator.cluster import Cluster
from lore_core.surfaces import SurfacesDoc, SurfaceDef


# ---------------------------------------------------------------------------
# Fake Anthropic client
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
    def __init__(self, response):
        self._response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response):
        self.messages = _FakeMessagesAPI(response)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(data: dict) -> _FakeAnthropicClient:
    block = _FakeContentBlock(type_="tool_use", input_=data)
    return _FakeAnthropicClient(_FakeResponse([block]))


def _resolver(tier: str) -> str:
    return f"model-{tier}"


def _simple_cluster() -> Cluster:
    return Cluster(
        topic="test topic",
        scope="test scope",
        session_notes=["[[note1]]", "[[note2]]"],
        suggested_surface="concept",
    )


def _simple_surfaces_doc() -> SurfacesDoc:
    return SurfacesDoc(
        schema_version=2,
        surfaces=[
            SurfaceDef(
                name="concept",
                description="Cross-cutting idea or pattern.",
                extract_when="pattern appears across sessions",
            ),
            SurfaceDef(
                name="decision",
                description="A trade-off made.",
                extract_when="session records a trade-off",
            ),
        ],
        path=Path("/x"),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_abstract_empty_cluster_short_circuits():
    """Empty session_notes → [] with no LLM call."""
    cluster = Cluster(topic="x", scope="y", session_notes=[], suggested_surface=None)
    client = _make_client({"surfaces": []})
    result = abstract_cluster(
        cluster=cluster,
        surfaces_doc=_simple_surfaces_doc(),
        source_notes_by_wikilink={},
        anthropic_client=client,
        model_resolver=_resolver,
    )
    assert result == []
    assert client.messages.calls == []


def test_abstract_emits_surface_for_clear_pattern():
    """Fake returns one surface → list has 1 AbstractedSurface with those values."""
    data = {"surfaces": [{"surface_name": "concept", "title": "Test", "body": "Body"}]}
    client = _make_client(data)
    result = abstract_cluster(
        cluster=_simple_cluster(),
        surfaces_doc=_simple_surfaces_doc(),
        source_notes_by_wikilink={},
        anthropic_client=client,
        model_resolver=_resolver,
    )
    assert len(result) == 1
    s = result[0]
    assert isinstance(s, AbstractedSurface)
    assert s.surface_name == "concept"
    assert s.title == "Test"
    assert s.body == "Body"


def test_abstract_emits_zero_surfaces_when_pattern_unclear():
    """Fake returns empty surfaces list → empty list, no error."""
    client = _make_client({"surfaces": []})
    result = abstract_cluster(
        cluster=_simple_cluster(),
        surfaces_doc=_simple_surfaces_doc(),
        source_notes_by_wikilink={},
        anthropic_client=client,
        model_resolver=_resolver,
    )
    assert result == []


def test_abstract_uses_high_tier_by_default():
    """Default call uses high tier model."""
    client = _make_client({"surfaces": []})
    abstract_cluster(
        cluster=_simple_cluster(),
        surfaces_doc=_simple_surfaces_doc(),
        source_notes_by_wikilink={},
        anthropic_client=client,
        model_resolver=_resolver,
    )
    assert client.messages.calls[0]["model"] == "model-high"


def test_abstract_falls_back_to_middle_when_high_off():
    """high_tier_off=True → middle-tier model used."""
    client = _make_client({"surfaces": []})
    abstract_cluster(
        cluster=_simple_cluster(),
        surfaces_doc=_simple_surfaces_doc(),
        source_notes_by_wikilink={},
        anthropic_client=client,
        model_resolver=_resolver,
        high_tier_off=True,
    )
    assert client.messages.calls[0]["model"] == "model-middle"


def test_abstract_warning_logged_once_when_high_off(tmp_path):
    """Calling twice with high_tier_off=True logs the marker exactly once."""
    client = _make_client({"surfaces": []})
    for _ in range(2):
        abstract_cluster(
            cluster=_simple_cluster(),
            surfaces_doc=_simple_surfaces_doc(),
            source_notes_by_wikilink={},
            anthropic_client=client,
            model_resolver=_resolver,
            high_tier_off=True,
            lore_root=tmp_path,
        )
    log_path = tmp_path / ".lore" / "warnings.log"
    assert log_path.exists()
    content = log_path.read_text()
    assert content.count(_HIGH_OFF_WARNING_ID) == 1


def test_abstract_no_warning_when_high_tier_on(tmp_path):
    """high_tier_off=False → no warnings.log file written."""
    client = _make_client({"surfaces": []})
    abstract_cluster(
        cluster=_simple_cluster(),
        surfaces_doc=_simple_surfaces_doc(),
        source_notes_by_wikilink={},
        anthropic_client=client,
        model_resolver=_resolver,
        high_tier_off=False,
        lore_root=tmp_path,
    )
    log_path = tmp_path / ".lore" / "warnings.log"
    assert not log_path.exists()


def test_abstract_no_warning_when_lore_root_none_even_if_high_off():
    """lore_root=None + high_tier_off=True → no crash, no log written."""
    client = _make_client({"surfaces": []})
    # Should not raise
    abstract_cluster(
        cluster=_simple_cluster(),
        surfaces_doc=_simple_surfaces_doc(),
        source_notes_by_wikilink={},
        anthropic_client=client,
        model_resolver=_resolver,
        high_tier_off=True,
        lore_root=None,
    )
    # No assertions needed beyond not crashing


def test_abstract_drops_surface_with_invalid_name():
    """Surface with surface_name not in vocab is silently dropped."""
    data = {"surfaces": [{"surface_name": "bogus_not_in_vocab", "title": "X", "body": "Y"}]}
    client = _make_client(data)
    result = abstract_cluster(
        cluster=_simple_cluster(),
        surfaces_doc=_simple_surfaces_doc(),
        source_notes_by_wikilink={},
        anthropic_client=client,
        model_resolver=_resolver,
    )
    assert result == []


def test_abstract_drops_surface_missing_title_or_body():
    """Surfaces missing title or body are dropped."""
    data = {
        "surfaces": [
            {"surface_name": "concept", "body": "Body"},   # missing title
            {"surface_name": "concept", "title": "T"},     # missing body
        ]
    }
    client = _make_client(data)
    result = abstract_cluster(
        cluster=_simple_cluster(),
        surfaces_doc=_simple_surfaces_doc(),
        source_notes_by_wikilink={},
        anthropic_client=client,
        model_resolver=_resolver,
    )
    assert result == []


def test_abstract_forces_tool_choice():
    """tool_choice must be {"type": "tool", "name": "abstract"}."""
    client = _make_client({"surfaces": []})
    abstract_cluster(
        cluster=_simple_cluster(),
        surfaces_doc=_simple_surfaces_doc(),
        source_notes_by_wikilink={},
        anthropic_client=client,
        model_resolver=_resolver,
    )
    assert client.messages.calls[0]["tool_choice"] == {"type": "tool", "name": "abstract"}


def test_abstract_includes_source_note_bodies_in_prompt():
    """source_notes_by_wikilink bodies appear in the sent prompt."""
    client = _make_client({"surfaces": []})
    abstract_cluster(
        cluster=_simple_cluster(),
        surfaces_doc=_simple_surfaces_doc(),
        source_notes_by_wikilink={"[[note1]]": "session body content here"},
        anthropic_client=client,
        model_resolver=_resolver,
    )
    messages = client.messages.calls[0]["messages"]
    prompt = messages[0]["content"]
    assert "session body content here" in prompt


def test_abstract_truncates_long_source_bodies_in_prompt():
    """Bodies longer than 1000 chars are truncated with ellipsis in prompt."""
    long_body = "x" * 2000
    client = _make_client({"surfaces": []})
    abstract_cluster(
        cluster=_simple_cluster(),
        surfaces_doc=_simple_surfaces_doc(),
        source_notes_by_wikilink={"[[note1]]": long_body},
        anthropic_client=client,
        model_resolver=_resolver,
    )
    messages = client.messages.calls[0]["messages"]
    prompt = messages[0]["content"]
    assert "…" in prompt
    assert long_body not in prompt


def test_abstract_empty_surfaces_doc_returns_empty():
    """SurfacesDoc with no surfaces → empty list, no LLM call."""
    empty_doc = SurfacesDoc(schema_version=2, surfaces=[], path=Path("/x"))
    client = _make_client({"surfaces": []})
    result = abstract_cluster(
        cluster=_simple_cluster(),
        surfaces_doc=empty_doc,
        source_notes_by_wikilink={},
        anthropic_client=client,
        model_resolver=_resolver,
    )
    assert result == []
    assert client.messages.calls == []
