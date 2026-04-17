"""User identity + team-mode detection.

`_users.yml` is optional — a wiki without it is in *solo mode*: any
email resolves to its local-part handle, no aliasing, no session
sharding. When ≥2 distinct authors appear in the wiki's git log and
`_users.yml` is absent, the curator can propose team-mode activation.

Schema (see concepts/lore/identity-aliasing):

    users:
      - handle: buchbend
        display_name: Christof Buchbender
        aliases:
          emails:
            - christof@example.com
            - buchbend@mail.de
          github: [buchbend]
        identity_history:
          - "2025-12-01: email X added"
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_GIT_TIMEOUT = 10


def _load_users_yml(wiki_path: Path) -> dict:
    path = wiki_path / "_users.yml"
    if not path.exists():
        return {}
    try:
        import yaml

        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def team_mode_active(wiki_path: Path) -> bool:
    """True iff the wiki has a `_users.yml` file (team mode opt-in)."""
    return (wiki_path / "_users.yml").exists()


def resolve_handle(wiki_path: Path, email: str) -> str:
    """Return the canonical handle for an email address.

    Resolution order:
      1. Look up `email` in each user's `aliases.emails` list in
         `_users.yml`.
      2. Fallback: email local-part (`foo@bar` → `foo`).

    Empty/missing email returns `""`.
    """
    if not email:
        return ""
    data = _load_users_yml(wiki_path)
    for user in data.get("users") or []:
        emails = (user.get("aliases") or {}).get("emails") or []
        if email in emails:
            return str(user.get("handle") or "")
    # Fallback: local-part
    return email.split("@", 1)[0] if "@" in email else email


def aliased_emails(wiki_path: Path) -> set[str]:
    """Union of every email in `_users.yml` aliases."""
    data = _load_users_yml(wiki_path)
    out: set[str] = set()
    for user in data.get("users") or []:
        for email in (user.get("aliases") or {}).get("emails") or []:
            out.add(email)
    return out


def distinct_git_authors(wiki_path: Path) -> set[str]:
    """Return the set of distinct author emails from the wiki's git log.

    Returns set() on any git failure (no repo, no commits, git absent).
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(wiki_path), "log", "--format=%ae"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def unaliased_authors(wiki_path: Path) -> set[str]:
    """Distinct git authors whose emails are not yet in `_users.yml`."""
    return distinct_git_authors(wiki_path) - aliased_emails(wiki_path)


def team_mode_recommended(wiki_path: Path, threshold: int = 2) -> bool:
    """True when enough distinct unaliased authors warrant team mode.

    Solo wikis (one author) always return False.
    Team wikis already in team mode (has `_users.yml`) also return False —
    the recommendation is only for wikis still in solo mode that have
    grown past the threshold.
    """
    if team_mode_active(wiki_path):
        return False
    return len(distinct_git_authors(wiki_path)) >= threshold


def session_note_dir(wiki_path: Path, handle: str) -> Path:
    """Return the directory where a session note should be written.

    In team mode: `sessions/<handle>/`. In solo mode: `sessions/`.
    Creating the directory is the caller's responsibility.
    """
    base = wiki_path / "sessions"
    if team_mode_active(wiki_path) and handle:
        return base / handle
    return base
