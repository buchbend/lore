"""User identity + team-mode detection.

`_users.yml` is optional — a wiki without it is in *solo mode*: any
email resolves to its local-part handle, no aliasing, no session
sharding.

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

from pathlib import Path



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


def resolve_display_name(wiki_path: Path, handle: str) -> str:
    """Return the display name for `handle`.

    Vault config, not `$USER` — no environment fallback. Looks up
    `_users.yml` first (team mode); when that yields nothing (solo
    wikis, or a team wiki with no matching user), falls back to the
    personal `user.display_name` set via `lore config set` at the
    person config (`~/.config/lore/config.yml`); `lore_root` comes from
    `wiki_path` as `<lore_root>/wiki/<name>`. Empty/missing handle
    returns `""`.
    """
    if not handle:
        return ""
    data = _load_users_yml(wiki_path)
    for user in data.get("users") or []:
        if user.get("handle") == handle:
            return str(user.get("display_name") or "")
    from lore_core.root_config import load_root_config

    lore_root = wiki_path.parent.parent
    return load_root_config(lore_root).user.display_name
