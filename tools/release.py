#!/usr/bin/env python3
"""Cut a lore release: bump the version in three files, open the release PR.

`pyproject.toml`, `.claude-plugin/plugin.json` and `CHANGELOG.md` all encode
the package version, and `claude plugin update lore@lore` only re-fetches when
the manifest version changes. A merge that ships plugin behaviour without the
bump leaves every installed cache on the old code while `main` moves on.
`tests/test_version_sync.py` guards the three files; this script is what makes
them agree in the first place.

`main` is branch protected, so the bump lands as its own pull request. The
script stops at the open PR — merging stays a human act.

The CHANGELOG section it writes holds the commit subjects that landed since
the last release, which are facts rather than a summary. Pass `--notes` with a
written section body when the release deserves prose, which most do.

Usage:
    python3 tools/release.py                      # minor bump, opens the PR
    python3 tools/release.py --part patch
    python3 tools/release.py --notes notes.md     # notes.md holds the section body
    python3 tools/release.py --dry-run            # print the plan, touch nothing
    python3 tools/release.py --no-pr              # commit locally, push nothing

Stdlib only, and no `lore_core` import: the script has to run in a checkout
whose install is broken, which is a state a release is often cutting a fix for.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

# `^version = ` anchors to the project table's own key. `target-version` and a
# dependency pin both carry a version and both must stay where they are.
_PROJECT_VERSION = re.compile(r'^version = "(\d+\.\d+\.\d+)"$', re.M)
# The manifest is rewritten by regex rather than by a json round trip, which
# would reformat the whole file and bury the one-line bump in noise.
_MANIFEST_VERSION = re.compile(r'^(\s*"version":\s*")\d+\.\d+\.\d+(")', re.M)
_RELEASE_HEADING = re.compile(r"^## \[\d+\.\d+\.\d+\]", re.M)
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# Every release commit carries this subject, so it marks the range of work the
# next release covers.
RELEASE_SUBJECT = "chore: release"


# --- pure transforms --------------------------------------------------------


def read_version(pyproject_text: str) -> str:
    """The version the project table declares."""
    match = _PROJECT_VERSION.search(pyproject_text)
    if not match:
        raise ValueError('pyproject.toml holds no `version = "X.Y.Z"` line')
    return match.group(1)


def next_version(current: str, part: str) -> str:
    """The version after ``current``. A bump zeroes every lower part."""
    match = _SEMVER.match(current)
    if not match:
        raise ValueError(f"not a semver version: {current!r}")
    major, minor, patch = (int(g) for g in match.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"unknown part: {part!r}")


def bump_pyproject(text: str, version: str) -> str:
    out, count = _PROJECT_VERSION.subn(f'version = "{version}"', text, count=1)
    if not count:
        raise ValueError("pyproject.toml holds no version line to bump")
    return out


def bump_manifest(text: str, version: str) -> str:
    out, count = _MANIFEST_VERSION.subn(rf"\g<1>{version}\g<2>", text, count=1)
    if not count:
        raise ValueError("plugin.json holds no version line to bump")
    return out


def insert_section(text: str, version: str, day: str, notes: str) -> str:
    """Put a new release section above the newest one already recorded."""
    if f"## [{version}]" in text:
        raise ValueError(f"CHANGELOG.md already holds a section for {version}")
    section = f"## [{version}] - {day}\n\n{notes.strip()}\n\n"
    match = _RELEASE_HEADING.search(text)
    at = match.start() if match else len(text)
    return text[:at] + section + text[at:]


# --- git and gh -------------------------------------------------------------


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def pick_release_commit(log_output: str) -> str:
    """The newest sha in ``git log --format=%H<NUL>%s`` output whose *subject*
    starts with the release prefix.

    `git log --grep` reads the whole message, and a squash merge carries every
    branch commit message in its body. This file's own history holds a body
    line quoting `chore: release X.Y.Z`, and matching that commit would cut the
    range short and drop released work out of the CHANGELOG.
    """
    for line in log_output.splitlines():
        sha, _, subject = line.partition("\x00")
        if subject.startswith(RELEASE_SUBJECT):
            return sha
    return ""


def landed_subjects() -> list[str]:
    """Commit subjects on `origin/main` since the last release commit."""
    last = pick_release_commit(git("log", "--format=%H%x00%s", "origin/main"))
    span = f"{last}..origin/main" if last else "origin/main"
    log = git("log", "--no-merges", "--format=%s", span)
    return [line for line in log.splitlines() if line.strip()]


def run_version_guard() -> None:
    """Run the three-file guard against the files just written."""
    if shutil.which("pytest") is None:
        print("! pytest is not installed — version-sync guard skipped")
        return
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_version_sync.py"],
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        sys.exit("version-sync guard failed — the branch is left in place to inspect")


# --- entry point ------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--part", choices=("major", "minor", "patch"), default="minor", help="Default: minor."
    )
    parser.add_argument("--notes", type=Path, help="File holding the CHANGELOG section body.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan and stop.")
    parser.add_argument("--no-pr", action="store_true", help="Commit locally; do not push.")
    args = parser.parse_args(argv)

    if git("status", "--porcelain"):
        sys.exit("working tree is dirty — commit or set aside your changes first")
    if not args.no_pr and shutil.which("gh") is None:
        sys.exit("gh is not on PATH — install it, or pass --no-pr")

    git("fetch", "origin")
    current = read_version(PYPROJECT.read_text(encoding="utf-8"))
    version = next_version(current, args.part)
    branch = f"chore/release-{version}"

    subjects = landed_subjects()
    if not subjects:
        sys.exit(f"nothing landed on origin/main since {current} — no release to cut")
    notes = (
        args.notes.read_text(encoding="utf-8")
        if args.notes
        else "### Changed\n\n" + "\n".join(f"- {s}" for s in subjects)
    )

    print(f"{current} → {version} on {branch}, covering {len(subjects)} commit(s):")
    for subject in subjects:
        print(f"  {subject}")
    if args.dry_run:
        print("dry run — nothing written")
        return 0

    git("checkout", "-b", branch, "origin/main")
    PYPROJECT.write_text(
        bump_pyproject(PYPROJECT.read_text(encoding="utf-8"), version), encoding="utf-8"
    )
    MANIFEST.write_text(
        bump_manifest(MANIFEST.read_text(encoding="utf-8"), version), encoding="utf-8"
    )
    CHANGELOG.write_text(
        insert_section(
            CHANGELOG.read_text(encoding="utf-8"), version, date.today().isoformat(), notes
        ),
        encoding="utf-8",
    )
    run_version_guard()

    git("add", "pyproject.toml", ".claude-plugin/plugin.json", "CHANGELOG.md")
    git("commit", "-m", f"{RELEASE_SUBJECT} {version}")
    if args.no_pr:
        print(f"committed on {branch} — push and open the PR when ready")
        return 0

    git("push", "-u", "origin", branch)
    body = (
        f"Bumps `pyproject.toml`, `.claude-plugin/plugin.json` and `CHANGELOG.md` "
        f"to {version}.\n\nAn installed plugin cache re-fetches on this version "
        f"change and on nothing else.\n\n{notes.strip()}\n"
    )
    url = subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--base",
            "main",
            "--head",
            branch,
            "--title",
            f"{RELEASE_SUBJECT} {version}",
            "--body",
            body,
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
