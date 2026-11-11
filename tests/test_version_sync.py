"""Version-sync guard.

`pyproject.toml`, `.claude-plugin/plugin.json`, and `CHANGELOG.md` all
encode the package version. They must agree — `claude plugin update
lore@lore` only re-fetches when `plugin.json:version` changes, so silent
drift between sources means installed clients keep running cached code.

This test fails fast if the three sources disagree, and is the canonical
"CI guard" referenced by the release process in `CONTRIBUTING.md`.

The repo ships one plugin, `lore`, with one manifest (ADR 0021, Lore
ships as one plugin).
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
PLUGIN_MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"

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


def test_marketplace_lists_one_plugin():
    plugins = _marketplace_plugins()
    assert set(plugins) == {"lore"}, (
        f"marketplace.json plugins are {sorted(plugins)!r}; expected only 'lore' (ADR 0021)."
    )


def test_repo_holds_one_plugin_manifest_and_no_workflow_folder():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    manifests = [p for p in tracked if p.endswith("plugin.json")]
    assert manifests == [".claude-plugin/plugin.json"], manifests
    assert not (REPO_ROOT / "lore-workflow").exists()


def test_marketplace_sources_resolve_to_a_plugin_manifest():
    for name, entry in _marketplace_plugins().items():
        source_dir = (REPO_ROOT / entry["source"]).resolve()
        manifest = source_dir / ".claude-plugin" / "plugin.json"
        assert manifest.is_file(), (
            f"marketplace entry {name!r} has source={entry['source']!r}, "
            f"but no manifest at {manifest} — plugin isn't installable."
        )
