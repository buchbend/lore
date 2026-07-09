"""Version-sync guard.

`pyproject.toml`, `.claude-plugin/plugin.json`, and `CHANGELOG.md` all
encode the package version. They must agree — `claude plugin update
lore@lore` only re-fetches when `plugin.json:version` changes, so silent
drift between sources means installed clients keep running cached code.

This test fails fast if the three sources disagree, and is the canonical
"CI guard" referenced by the release process in `CONTRIBUTING.md`.

The repo also ships a second plugin, `lore-workflow` (its own manifest at
`lore-workflow/.claude-plugin/plugin.json`), on an independent version
axis — it has no pyproject/CHANGELOG counterpart yet, so it's checked
only for internal validity and marketplace wiring, not cross-file sync.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
PLUGIN_MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"
WORKFLOW_MANIFEST = REPO_ROOT / "lore-workflow" / ".claude-plugin" / "plugin.json"

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
CHANGELOG_HEADING_RE = re.compile(r"^##\s+\[(?P<version>\d+\.\d+\.\d+)\]")


def _pyproject_version() -> str:
    return tomllib.loads(PYPROJECT.read_text())["project"]["version"]


def _plugin_version() -> str:
    return json.loads(PLUGIN_MANIFEST.read_text())["version"]


def _latest_changelog_version() -> str | None:
    """First `## [X.Y.Z]` heading after the `[Unreleased]` sentinel."""
    for line in CHANGELOG.read_text().splitlines():
        m = CHANGELOG_HEADING_RE.match(line)
        if m:
            return m.group("version")
    return None


def test_pyproject_and_plugin_manifest_agree():
    py = _pyproject_version()
    plugin = _plugin_version()
    assert py == plugin, (
        f"version drift: pyproject.toml={py!r} vs "
        f".claude-plugin/plugin.json={plugin!r}. Bump both in lockstep — "
        f"see CONTRIBUTING.md 'Releasing a new version'."
    )


def test_changelog_has_entry_for_current_version():
    py = _pyproject_version()
    latest = _latest_changelog_version()
    assert latest == py, (
        f"CHANGELOG.md latest release heading is {latest!r} but "
        f"pyproject.toml is {py!r}. Add a `## [{py}] — YYYY-MM-DD` "
        f"section under `## [Unreleased]`."
    )


def _marketplace_plugins() -> dict:
    data = json.loads(MARKETPLACE.read_text())
    return {entry["name"]: entry for entry in data["plugins"]}


def test_marketplace_lists_both_plugins():
    plugins = _marketplace_plugins()
    assert set(plugins) == {"lore", "lore-workflow"}, (
        f"marketplace.json plugins are {sorted(plugins)!r}; expected both "
        f"'lore' and 'lore-workflow' entries (PRD 0003 two-plugin monorepo)."
    )


def test_marketplace_sources_resolve_to_a_plugin_manifest():
    for name, entry in _marketplace_plugins().items():
        source_dir = (REPO_ROOT / entry["source"]).resolve()
        manifest = source_dir / ".claude-plugin" / "plugin.json"
        assert manifest.is_file(), (
            f"marketplace entry {name!r} has source={entry['source']!r}, "
            f"but no manifest at {manifest} — plugin isn't installable."
        )


def test_lore_workflow_manifest_has_independent_version():
    assert WORKFLOW_MANIFEST.is_file(), (
        f"expected a lore-workflow plugin manifest at {WORKFLOW_MANIFEST}"
    )
    manifest = json.loads(WORKFLOW_MANIFEST.read_text())
    assert manifest["name"] == "lore-workflow"
    version = manifest.get("version")
    assert version and SEMVER_RE.match(version), (
        f"lore-workflow plugin.json version={version!r} must be a "
        f"standalone X.Y.Z, independent of the lore plugin's version."
    )
