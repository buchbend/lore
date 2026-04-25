"""Version-sync guard.

`pyproject.toml`, `.claude-plugin/plugin.json`, and `CHANGELOG.md` all
encode the package version. They must agree — `claude plugin update
lore@lore` only re-fetches when `plugin.json:version` changes, so silent
drift between sources means installed clients keep running cached code.

This test fails fast if the three sources disagree, and is the canonical
"CI guard" referenced by the release process in `CONTRIBUTING.md`.
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
