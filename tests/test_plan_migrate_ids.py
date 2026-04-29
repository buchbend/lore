"""Tests for ``lore plan migrate-ids`` — one-shot legacy ``s<N>`` → canonical ``step-<N>``.

The migration walks every plan in every wiki under ``LORE_ROOT`` and
rewrites:

* Body headings: ``### s<N>: …`` → ``### step-<N>: …``
* Frontmatter ``step_status`` keys: ``s<N>`` → ``step-<N>``

The same logic runs piecemeal during re-capture (covered in
``test_plan_writer.py::test_recapture_migrates_legacy_step_ids_to_canonical``);
this test pins the standalone command path that does NOT require a
re-capture trigger.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from lore_cli.__main__ import app
from lore_core.schema import parse_frontmatter, strip_frontmatter

runner = CliRunner()


def _write_legacy_plan(wiki_root: Path, slug: str, n_steps: int = 3) -> Path:
    """Hand-write a plan in the legacy shape (``### s<N>:`` headings,
    ``step_status: {s<N>: …}`` keys) bypassing the writer (which would
    canonicalize).

    Note: ``step_status_updated`` is a single ISO timestamp string in
    real plans (not a per-step dict), matching what
    :func:`step_status.set_step` writes.
    """
    plans = wiki_root / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    fm = {
        "schema_version": 2,
        "type": "plan",
        "slug": slug,
        "status": "active",
        "created": "2026-04-20",
        "last_reviewed": "2026-04-20",
        "description": "legacy plan",
        "source_adapter": "claude-code-hook",
        "source_hash": "sha256:legacy",
        "step_status": {f"s{i}": "done" for i in range(1, n_steps)},  # all but last
        "step_status_updated": "2026-04-25T10:00:00Z",
    }
    body_lines = ["# Legacy plan", "", "## Steps", ""]
    for i in range(1, n_steps + 1):
        body_lines.append(f"### s{i}: legacy step {i}")
        body_lines.append(f"body for legacy step {i}")
        body_lines.append("")
    text = (
        "---\n"
        + yaml.safe_dump(fm, default_flow_style=False, sort_keys=False).strip()
        + "\n---\n\n"
        + "\n".join(body_lines).rstrip()
        + "\n"
    )
    path = plans / f"{slug}.md"
    path.write_text(text)
    return path


@pytest.fixture
def lore_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "lore"
    (root / "wiki" / "private").mkdir(parents=True)
    (root / ".lore").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(root))
    return root


def test_migrate_ids_rewrites_body_headings(lore_root: Path) -> None:
    wiki = lore_root / "wiki" / "private"
    path = _write_legacy_plan(wiki, "alpha", n_steps=3)

    result = runner.invoke(app, ["plan", "migrate-ids"])
    assert result.exit_code == 0, result.output

    body = strip_frontmatter(path.read_text())
    assert "### step-1: legacy step 1" in body
    assert "### step-2: legacy step 2" in body
    assert "### step-3: legacy step 3" in body
    assert "### s1:" not in body
    assert "### s2:" not in body
    assert "### s3:" not in body


def test_migrate_ids_rewrites_step_status_keys(lore_root: Path) -> None:
    wiki = lore_root / "wiki" / "private"
    path = _write_legacy_plan(wiki, "beta", n_steps=4)

    runner.invoke(app, ["plan", "migrate-ids"])

    fm = parse_frontmatter(path.read_text())
    assert fm["step_status"] == {"step-1": "done", "step-2": "done", "step-3": "done"}
    assert "s1" not in fm["step_status"]
    # step_status_updated is a single timestamp string, not a keyed dict —
    # untouched by migration.
    assert fm["step_status_updated"] == "2026-04-25T10:00:00Z"


def test_migrate_ids_is_idempotent(lore_root: Path) -> None:
    """Running the migration twice on the same vault must be a no-op
    on the second run — no errors, no spurious mtime bumps."""
    wiki = lore_root / "wiki" / "private"
    path = _write_legacy_plan(wiki, "gamma")

    runner.invoke(app, ["plan", "migrate-ids"])
    text_after_first = path.read_text()
    mtime_after_first = path.stat().st_mtime_ns

    result = runner.invoke(app, ["plan", "migrate-ids"])
    assert result.exit_code == 0

    # File contents identical and mtime unchanged.
    assert path.read_text() == text_after_first
    assert path.stat().st_mtime_ns == mtime_after_first


def test_migrate_ids_skips_canonical_plans(lore_root: Path) -> None:
    """A plan that's already canonical must be untouched, including mtime."""
    wiki = lore_root / "wiki" / "private"
    plans = wiki / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    canonical_text = (
        "---\n"
        "schema_version: 2\n"
        "type: plan\n"
        "slug: delta\n"
        "status: active\n"
        "created: '2026-04-28'\n"
        "last_reviewed: '2026-04-28'\n"
        "description: already canonical\n"
        "source_adapter: claude-code-hook\n"
        "source_hash: 'sha256:c'\n"
        "step_status:\n"
        "  step-1: done\n"
        "---\n\n"
        "# Delta\n\n## Steps\n\n### step-1: a\nbody\n"
    )
    path = plans / "delta.md"
    path.write_text(canonical_text)
    mtime_before = path.stat().st_mtime_ns

    result = runner.invoke(app, ["plan", "migrate-ids"])
    assert result.exit_code == 0

    assert path.stat().st_mtime_ns == mtime_before
    assert path.read_text() == canonical_text


def test_migrate_ids_walks_multiple_wikis(lore_root: Path) -> None:
    wiki_a = lore_root / "wiki" / "private"
    wiki_b = lore_root / "wiki" / "ccat"
    wiki_b.mkdir(parents=True)
    p_a = _write_legacy_plan(wiki_a, "in-private")
    p_b = _write_legacy_plan(wiki_b, "in-ccat")

    runner.invoke(app, ["plan", "migrate-ids"])

    for path in (p_a, p_b):
        body = strip_frontmatter(path.read_text())
        assert "### step-1:" in body
        assert "### s1:" not in body


def test_migrate_ids_skips_non_plan_files(lore_root: Path) -> None:
    """Files in plans/ whose frontmatter ``type != "plan"`` are reported
    as skipped, not migrated. Defensive against accidental other-typed
    notes ending up in the plans/ directory."""
    wiki = lore_root / "wiki" / "private"
    plans = wiki / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    legit = _write_legacy_plan(wiki, "real-plan")
    (plans / "stray.md").write_text(
        "---\ntype: concept\nslug: stray\n---\n\n# Stray\n\n### s1: not a plan step\n"
    )

    result = runner.invoke(app, ["plan", "migrate-ids"])
    assert result.exit_code == 0

    # The stray concept's body MUST NOT have been rewritten — it is not a plan.
    stray_text = (plans / "stray.md").read_text()
    assert "### s1:" in stray_text
    assert "### step-1:" not in stray_text

    # The real plan's body got migrated.
    legit_body = strip_frontmatter(legit.read_text())
    assert "### step-1:" in legit_body


def test_migrate_ids_handles_malformed_yaml_gracefully(lore_root: Path) -> None:
    """A plan with garbage frontmatter must not abort the whole walk."""
    wiki = lore_root / "wiki" / "private"
    plans = wiki / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    legit = _write_legacy_plan(wiki, "good-plan")
    (plans / "broken.md").write_text("---\nnot: valid: yaml: [[\n---\n\n### s1: x\n")

    result = runner.invoke(app, ["plan", "migrate-ids"])
    assert result.exit_code == 0

    # The good plan still got migrated; the broken one was skipped
    # (left as-is) without aborting the run.
    good_body = strip_frontmatter(legit.read_text())
    assert "### step-1:" in good_body
    assert "broken" in result.output  # skip notice present


def test_migrate_ids_preserves_unicode_in_description(lore_root: Path) -> None:
    """``yaml.safe_dump(allow_unicode=True)`` keeps non-ASCII bytes as-is.

    Without it, German umlauts in plan descriptions get backslash-escaped
    on every migration — diff noise + potential consumer breakage.
    """
    wiki = lore_root / "wiki" / "private"
    path = _write_legacy_plan(wiki, "unicode-plan")
    text = path.read_text()
    # Inject a unicode description into the frontmatter.
    text = text.replace(
        "description: legacy plan",
        "description: 'Stoßbericht — Säule (über die Brücke)'",
    )
    path.write_text(text)

    runner.invoke(app, ["plan", "migrate-ids"])

    # After migration, the unicode bytes must still be present literally.
    after = path.read_text()
    assert "Stoßbericht" in after
    assert "Säule" in after
    assert "Brücke" in after
    # The legacy heading was migrated.
    assert "### step-1:" in after


def test_migrate_ids_dry_run_reports_without_writing(lore_root: Path) -> None:
    """``--dry-run`` lists what would change but doesn't write."""
    wiki = lore_root / "wiki" / "private"
    path = _write_legacy_plan(wiki, "epsilon")
    text_before = path.read_text()

    result = runner.invoke(app, ["plan", "migrate-ids", "--dry-run"])
    assert result.exit_code == 0
    # Output mentions the plan and what would change.
    assert "epsilon" in result.output

    # File untouched.
    assert path.read_text() == text_before
