"""Tests for lore_curator.cluster — session-note clustering step."""
from __future__ import annotations

import pytest

from lore_curator.cluster import cluster_session_notes, Cluster


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


def _make_text_client() -> _FakeAnthropicClient:
    """Client that returns only a text block (no tool_use)."""
    block = _FakeContentBlock(type_="text", text="some text response")
    return _FakeAnthropicClient(_FakeResponse([block]))


def _resolver(tier: str) -> str:
    return f"model-{tier}"


def _sample_notes() -> list[dict]:
    return [
        {
            "path": "sessions/2024-01-01.md",
            "frontmatter": {"scope": "lore", "description": "capture pipeline work"},
            "summary": "Worked on passive capture pipeline.",
        },
        {
            "path": "sessions/2024-01-02.md",
            "frontmatter": {"scope": "lore", "description": "more capture work"},
            "summary": "Continued passive capture work.",
        },
    ]


_SURFACES = ["concept", "decision", "result"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_cluster_empty_notes_short_circuits_no_llm_call():
    """Empty notes → [] and no LLM call made."""
    client = _make_client({"clusters": []})
    result = cluster_session_notes(
        notes=[],
        surfaces=_SURFACES,
        llm_client=client,
        model_resolver=_resolver,
    )
    assert result == []
    assert client.messages.calls == []


def test_cluster_returns_clusters_from_llm_response():
    """Fake LLM returns one cluster; result has that single Cluster."""
    data = {
        "clusters": [
            {
                "topic": "passive capture",
                "scope": "lore",
                "session_notes": ["[[s1]]", "[[s2]]"],
                "suggested_surface": "concept",
            }
        ]
    }
    client = _make_client(data)
    result = cluster_session_notes(
        notes=_sample_notes(),
        surfaces=_SURFACES,
        llm_client=client,
        model_resolver=_resolver,
    )
    assert len(result) == 1
    c = result[0]
    assert c.topic == "passive capture"
    assert c.scope == "lore"
    assert c.session_notes == ["[[s1]]", "[[s2]]"]
    assert c.suggested_surface == "concept"


def test_cluster_each_cluster_has_topic_scope_notes():
    """Validate schema: Cluster has topic, scope, session_notes attributes."""
    data = {
        "clusters": [
            {
                "topic": "graph abstraction",
                "scope": "lore",
                "session_notes": ["sessions/note.md"],
                "suggested_surface": "result",
            }
        ]
    }
    client = _make_client(data)
    result = cluster_session_notes(
        notes=_sample_notes(),
        surfaces=_SURFACES,
        llm_client=client,
        model_resolver=_resolver,
    )
    assert len(result) == 1
    c = result[0]
    assert isinstance(c, Cluster)
    assert isinstance(c.topic, str) and c.topic
    assert isinstance(c.scope, str)
    assert isinstance(c.session_notes, list) and len(c.session_notes) > 0
    assert c.suggested_surface in _SURFACES or c.suggested_surface is None


def test_cluster_suggested_surface_matches_wiki_vocabulary_or_none():
    """Unknown suggested_surface is silently dropped → None."""
    data = {
        "clusters": [
            {
                "topic": "some topic",
                "scope": "project",
                "session_notes": ["sessions/x.md"],
                "suggested_surface": "paper",  # not in ["concept", "decision"]
            }
        ]
    }
    surfaces = ["concept", "decision"]
    client = _make_client(data)
    result = cluster_session_notes(
        notes=_sample_notes(),
        surfaces=surfaces,
        llm_client=client,
        model_resolver=_resolver,
    )
    assert len(result) == 1
    assert result[0].suggested_surface is None


def test_cluster_uses_middle_tier_model():
    """model_resolver receives 'middle'; call uses the returned model name."""
    data = {"clusters": [{"topic": "t", "scope": "s", "session_notes": ["n"]}]}
    client = _make_client(data)
    cluster_session_notes(
        notes=_sample_notes(),
        surfaces=_SURFACES,
        llm_client=client,
        model_resolver=lambda t: f"model-{t}",
    )
    assert client.messages.calls[0]["model"] == "model-middle"


def test_cluster_handles_malformed_llm_response_gracefully():
    """No tool_use block in response → returns [], no crash."""
    client = _make_text_client()
    result = cluster_session_notes(
        notes=_sample_notes(),
        surfaces=_SURFACES,
        llm_client=client,
        model_resolver=_resolver,
    )
    assert result == []


def test_cluster_drops_clusters_missing_topic_or_notes():
    """Clusters without topic or with empty notes are silently dropped."""
    data = {
        "clusters": [
            # Missing topic
            {"scope": "x", "session_notes": ["[[s]]"]},
            # Empty notes
            {"topic": "y", "scope": "x", "session_notes": []},
            # Valid one — should survive
            {"topic": "valid", "scope": "z", "session_notes": ["[[valid]]"]},
        ]
    }
    client = _make_client(data)
    result = cluster_session_notes(
        notes=_sample_notes(),
        surfaces=_SURFACES,
        llm_client=client,
        model_resolver=_resolver,
    )
    assert len(result) == 1
    assert result[0].topic == "valid"


def test_cluster_forces_tool_choice():
    """tool_choice must be {"type": "tool", "name": "cluster"}."""
    data = {"clusters": [{"topic": "t", "scope": "s", "session_notes": ["n"]}]}
    client = _make_client(data)
    cluster_session_notes(
        notes=_sample_notes(),
        surfaces=_SURFACES,
        llm_client=client,
        model_resolver=_resolver,
    )
    assert client.messages.calls[0]["tool_choice"] == {"type": "tool", "name": "cluster"}


def test_cluster_truncates_long_summaries_in_prompt():
    """Summary longer than 300 chars is truncated with ellipsis in the prompt."""
    long_summary = "x" * 1000
    notes = [
        {
            "path": "sessions/long.md",
            "frontmatter": {"scope": "lore"},
            "summary": long_summary,
        }
    ]
    data = {"clusters": [{"topic": "t", "scope": "s", "session_notes": ["n"]}]}
    client = _make_client(data)
    cluster_session_notes(
        notes=notes,
        surfaces=_SURFACES,
        llm_client=client,
        model_resolver=_resolver,
    )
    sent_prompt = client.messages.calls[0]["messages"][0]["content"]
    assert "…" in sent_prompt
    assert long_summary not in sent_prompt
