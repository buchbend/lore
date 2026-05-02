"""Integration test for the agent-guidance drift check in `lore lint`."""

from __future__ import annotations

from pathlib import Path

import pytest

from lore_core.lint import check_agent_guidance_sync


def _setup_attached_project(
    tmp_path: Path, slug: str, *, repo_content: str, orientation_section: str,
) -> tuple[Path, Path]:
    """Create a vault layout with one attached project + corresponding repo."""
    lore_root = tmp_path / "lore_root"
    wiki_root = lore_root / "wiki"
    wiki_path = wiki_root / "private"
    project_dir = wiki_path / "projects" / slug
    project_dir.mkdir(parents=True)
    orientation = project_dir / f"{slug}.md"
    orientation.write_text(
        "---\ntype: project\nscope: " + slug + "\n---\n\n"
        f"# Project: {slug}\n\n"
        f"## Agent guidance\n\n{orientation_section}\n"
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text(repo_content)

    # attachments.json
    (lore_root / ".lore").mkdir(parents=True)
    import json
    (lore_root / ".lore" / "attachments.json").write_text(
        json.dumps({
            "attachments": [
                {
                    "path": str(repo),
                    "wiki": "private",
                    "scope": slug,
                    "attached_at": "2026-05-01T00:00:00+00:00",
                    "source": "manual",
                },
            ],
            "declined": [],
        })
    )
    return lore_root, wiki_path


def test_lint_reports_drift(tmp_path, monkeypatch):
    lore_root, wiki_path = _setup_attached_project(
        tmp_path,
        slug="lore",
        repo_content="# repo\n\nDifferent content.\n",
        orientation_section="Use TDD.\n",
    )
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    issues = check_agent_guidance_sync(wiki_path, "private")
    drift = [i for i in issues if i.check == "agent_guidance_drift"]
    assert len(drift) == 1
    assert drift[0].severity == "WARNING"
    assert "lore project sync lore" in drift[0].message


def test_lint_silent_when_in_sync(tmp_path, monkeypatch):
    """No drift → no issue."""
    lore_root, wiki_path = _setup_attached_project(
        tmp_path,
        slug="lore",
        repo_content="# repo\n\nUse TDD.\n",
        orientation_section="Use TDD.\n",
    )
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    issues = check_agent_guidance_sync(wiki_path, "private")
    drift = [i for i in issues if i.check == "agent_guidance_drift"]
    assert drift == []


def test_lint_silent_when_no_section(tmp_path, monkeypatch):
    """Orientation has no `## Agent guidance` → check skips, no drift."""
    lore_root = tmp_path / "lore_root"
    wiki_root = lore_root / "wiki"
    wiki_path = wiki_root / "private"
    project_dir = wiki_path / "projects" / "lore"
    project_dir.mkdir(parents=True)
    (project_dir / "lore.md").write_text(
        "---\ntype: project\nscope: lore\n---\n\n"
        "# Project: lore\n\n## Overview\n\nproject.\n"
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# repo\n\nrepo content.\n")

    (lore_root / ".lore").mkdir(parents=True)
    import json
    (lore_root / ".lore" / "attachments.json").write_text(
        json.dumps({
            "attachments": [
                {
                    "path": str(repo),
                    "wiki": "private",
                    "scope": "lore",
                    "attached_at": "2026-05-01T00:00:00+00:00",
                    "source": "manual",
                },
            ],
            "declined": [],
        })
    )
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    issues = check_agent_guidance_sync(wiki_path, "private")
    drift = [i for i in issues if i.check == "agent_guidance_drift"]
    assert drift == []


def test_lint_silent_when_repo_path_missing(tmp_path, monkeypatch):
    """Attached repo path doesn't exist on this host → silent."""
    lore_root = tmp_path / "lore_root"
    wiki_root = lore_root / "wiki"
    wiki_path = wiki_root / "private"
    project_dir = wiki_path / "projects" / "lore"
    project_dir.mkdir(parents=True)
    (project_dir / "lore.md").write_text(
        "---\ntype: project\nscope: lore\n---\n\n"
        "# Project: lore\n\n## Agent guidance\n\nguidance.\n"
    )

    (lore_root / ".lore").mkdir(parents=True)
    import json
    (lore_root / ".lore" / "attachments.json").write_text(
        json.dumps({
            "attachments": [
                {
                    "path": str(tmp_path / "nonexistent"),
                    "wiki": "private",
                    "scope": "lore",
                    "attached_at": "2026-05-01T00:00:00+00:00",
                    "source": "manual",
                },
            ],
            "declined": [],
        })
    )
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    issues = check_agent_guidance_sync(wiki_path, "private")
    drift = [i for i in issues if i.check == "agent_guidance_drift"]
    assert drift == []


def test_lint_handles_legacy_flat_orientation(tmp_path, monkeypatch):
    """Legacy flat ``projects/<slug>.md`` is still checked for drift."""
    lore_root = tmp_path / "lore_root"
    wiki_root = lore_root / "wiki"
    wiki_path = wiki_root / "private"
    (wiki_path / "projects").mkdir(parents=True)
    (wiki_path / "projects" / "lore.md").write_text(
        "---\ntype: project\nscope: lore\n---\n\n"
        "# Project: lore\n\n## Agent guidance\n\nGuidance v1.\n"
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# repo\n\nGuidance v2 (different).\n")

    (lore_root / ".lore").mkdir(parents=True)
    import json
    (lore_root / ".lore" / "attachments.json").write_text(
        json.dumps({
            "attachments": [
                {
                    "path": str(repo),
                    "wiki": "private",
                    "scope": "lore",
                    "attached_at": "2026-05-01T00:00:00+00:00",
                    "source": "manual",
                },
            ],
            "declined": [],
        })
    )
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    issues = check_agent_guidance_sync(wiki_path, "private")
    drift = [i for i in issues if i.check == "agent_guidance_drift"]
    assert len(drift) == 1
