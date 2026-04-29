"""Tests for ``lore.plan.envelope/1`` — the structured ingest schema.

The envelope is the first-class path for tools that can emit JSON
(future Cursor/Aider/CLI/MCP). It bypasses markdown shape detection
entirely; producers construct the canonical IR directly.

Schema (v1):
* Required: ``schema`` (= ``"lore.plan.envelope/1"``), ``title``, ``steps`` (≥1)
* Optional: ``description``, ``body_intro``, ``slug``, ``repo``,
  ``parse_warnings``
* Per-step required: ``title``
* Per-step optional: ``id`` (defaults to ``step-<idx>``), ``body``,
  ``group``
"""
from __future__ import annotations

import pytest

from lore_core.plans.envelope import (
    PLAN_ENVELOPE_V1,
    EnvelopeError,
    from_envelope,
)
from lore_core.plans.ingest import IngestSource, ingest_plan


# ---------------------------------------------------------------------------
# Schema constant
# ---------------------------------------------------------------------------


def test_schema_constant_is_versioned() -> None:
    """The schema discriminator is explicitly versioned so v2 can ship later."""
    assert PLAN_ENVELOPE_V1 == "lore.plan.envelope/1"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def _minimal_envelope(**overrides) -> dict:
    base = {
        "schema": PLAN_ENVELOPE_V1,
        "title": "Refactor authentication",
        "steps": [
            {"title": "Audit existing flows"},
            {"title": "Migrate to OIDC"},
            {"title": "Drop legacy verifier"},
        ],
    }
    base.update(overrides)
    return base


def test_minimal_envelope_round_trips() -> None:
    plan = from_envelope(_minimal_envelope())
    assert plan.title == "Refactor authentication"
    assert plan.slug == "refactor-authentication"  # derived from title
    assert len(plan.steps) == 3
    assert plan.steps[0].id == "step-1"
    assert plan.steps[0].title == "Audit existing flows"
    assert plan.steps[1].id == "step-2"
    assert plan.steps[2].id == "step-3"
    # No warnings on a clean envelope; confidence reflects structured input.
    assert plan.warnings == []
    assert plan.confidence == "structured"
    assert plan.mode == "envelope"


def test_envelope_with_explicit_step_ids() -> None:
    """Producer-supplied step IDs are accepted verbatim if canonical."""
    env = _minimal_envelope(steps=[
        {"id": "step-1", "title": "alpha"},
        {"id": "step-2", "title": "beta"},
    ])
    plan = from_envelope(env)
    assert [s.id for s in plan.steps] == ["step-1", "step-2"]


def test_envelope_canonicalizes_legacy_step_ids() -> None:
    """Legacy ``s<N>`` step IDs in an envelope are canonicalized on the way in."""
    env = _minimal_envelope(steps=[
        {"id": "s1", "title": "alpha"},
        {"id": "s2", "title": "beta"},
    ])
    plan = from_envelope(env)
    assert [s.id for s in plan.steps] == ["step-1", "step-2"]


def test_envelope_passes_through_optional_fields() -> None:
    env = _minimal_envelope(
        slug="custom-slug",
        description="explicit description",
        body_intro="some prose between title and steps",
        repo="lore",
    )
    plan = from_envelope(env)
    assert plan.slug == "custom-slug"
    assert plan.body_intro == "some prose between title and steps"


def test_envelope_step_group_metadata() -> None:
    """Steps can carry a ``group`` annotation (e.g. ``Phase 1 — Foundation``)."""
    env = _minimal_envelope(steps=[
        {"title": "AND fallback", "group": "Phase 1 — Foundation"},
        {"title": "slug_index", "group": "Phase 1 — Foundation"},
        {"title": "Drop minimal MCP", "group": "Phase 2 — Cleanup"},
    ])
    plan = from_envelope(env)
    assert plan.steps[0].group == "Phase 1 — Foundation"
    assert plan.steps[2].group == "Phase 2 — Cleanup"


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


def test_envelope_rejects_missing_schema_field() -> None:
    env = {"title": "x", "steps": [{"title": "a"}]}
    with pytest.raises(EnvelopeError, match="schema"):
        from_envelope(env)


def test_envelope_rejects_wrong_schema_version() -> None:
    env = {"schema": "lore.plan.envelope/2", "title": "x", "steps": [{"title": "a"}]}
    with pytest.raises(EnvelopeError, match="schema"):
        from_envelope(env)


def test_envelope_rejects_non_dict() -> None:
    with pytest.raises(EnvelopeError):
        from_envelope("not a dict")  # type: ignore[arg-type]
    with pytest.raises(EnvelopeError):
        from_envelope([])  # type: ignore[arg-type]


def test_envelope_rejects_missing_title() -> None:
    env = {"schema": PLAN_ENVELOPE_V1, "steps": [{"title": "a"}]}
    with pytest.raises(EnvelopeError, match="title"):
        from_envelope(env)


def test_envelope_rejects_missing_steps() -> None:
    env = {"schema": PLAN_ENVELOPE_V1, "title": "x"}
    with pytest.raises(EnvelopeError, match="steps"):
        from_envelope(env)


def test_envelope_rejects_empty_steps() -> None:
    """A plan with zero steps is meaningless — fail loud, not silent."""
    env = {"schema": PLAN_ENVELOPE_V1, "title": "x", "steps": []}
    with pytest.raises(EnvelopeError, match="steps"):
        from_envelope(env)


def test_envelope_rejects_step_without_title() -> None:
    env = _minimal_envelope(steps=[{"title": "ok"}, {}])
    with pytest.raises(EnvelopeError, match="title"):
        from_envelope(env)


def test_envelope_rejects_steps_not_list() -> None:
    env = {"schema": PLAN_ENVELOPE_V1, "title": "x", "steps": "not-a-list"}
    with pytest.raises(EnvelopeError, match="steps"):
        from_envelope(env)


# ---------------------------------------------------------------------------
# ingest_plan dispatch — envelope path
# ---------------------------------------------------------------------------


def test_ingest_plan_envelope_path() -> None:
    """Dispatcher routes ``kind="envelope"`` through ``from_envelope`` and
    reports ``confidence="structured"``."""
    source = IngestSource(
        kind="envelope",
        payload=_minimal_envelope(),
        producer="cli",
    )
    result = ingest_plan(source)
    assert result.confidence == "structured"
    assert result.adapter_name.startswith("envelope")
    assert len(result.plan.steps) == 3


def test_ingest_plan_envelope_validation_propagates() -> None:
    """A bad envelope raises EnvelopeError out of the dispatcher."""
    source = IngestSource(
        kind="envelope",
        payload={"schema": "wrong", "title": "x", "steps": [{"title": "a"}]},
        producer="cli",
    )
    with pytest.raises(EnvelopeError):
        ingest_plan(source)


# ---------------------------------------------------------------------------
# `lore plan file --json` CLI integration
# ---------------------------------------------------------------------------


def test_cli_plan_file_json_writes_canonical_plan(tmp_path, monkeypatch) -> None:
    """``lore plan file --json <path>`` validates an envelope file and writes
    the resulting canonical plan to the wiki."""
    import json

    from typer.testing import CliRunner

    from lore_cli.__main__ import app
    from lore_core.schema import strip_frontmatter

    lore_root = tmp_path / "lore"
    wiki_root = lore_root / "wiki" / "private"
    wiki_root.mkdir(parents=True)
    (lore_root / ".lore").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    envelope_path = tmp_path / "plan.json"
    envelope_path.write_text(json.dumps(_minimal_envelope(slug="my-plan")))

    runner = CliRunner()
    result = runner.invoke(app, ["plan", "file", "--json", str(envelope_path)])
    assert result.exit_code == 0, result.output

    written = wiki_root / "plans" / "my-plan.md"
    assert written.exists()
    body = strip_frontmatter(written.read_text())
    # Canonical headings emitted, not legacy.
    assert "### step-1: Audit existing flows" in body
    assert "### step-2: Migrate to OIDC" in body
    assert "### step-3: Drop legacy verifier" in body


def test_cli_plan_file_json_rejects_bad_envelope(tmp_path, monkeypatch) -> None:
    """An envelope file that fails validation exits non-zero with a
    structured error message — does NOT silently file a degraded plan."""
    import json

    from typer.testing import CliRunner

    from lore_cli.__main__ import app

    lore_root = tmp_path / "lore"
    (lore_root / "wiki" / "private").mkdir(parents=True)
    (lore_root / ".lore").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    bad_envelope = tmp_path / "bad.json"
    bad_envelope.write_text(json.dumps({"schema": "wrong-version", "title": "x"}))

    runner = CliRunner()
    result = runner.invoke(app, ["plan", "file", "--json", str(bad_envelope)])
    assert result.exit_code != 0
    # Error mentions schema or envelope.
    out = result.output + (result.stderr if hasattr(result, "stderr") else "")
    assert "schema" in out.lower() or "envelope" in out.lower()
