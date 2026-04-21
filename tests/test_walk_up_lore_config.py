"""Task 7: one ancestor-walk for `## Lore` config resolution.

Pre-Task-7: two implementations — `lore_core.session._walk_up_lore_config`
(MAX_ANCESTOR_WALK=12, parsed with a local helper) and
`lore_cli.hooks._find_lore_config` (MAX_ANCESTOR_WALK=20, parsed with
read_attach). Consolidated to the session.py version using
read_attach as the parser, with MAX_ANCESTOR_WALK reconciled to 20.
"""

from __future__ import annotations

from pathlib import Path


def test_max_ancestor_walk_is_20() -> None:
    """Drift guard: reconciled constant from two pre-Task-7 values (12 and 20).
    20 was kept as the safer of the two finite loops.
    """
    from lore_core.session import MAX_ANCESTOR_WALK
    assert MAX_ANCESTOR_WALK == 20


def test_walk_up_finds_lore_config_at_20_levels(tmp_path: Path) -> None:
    """A 20-deep temp tree must find the root CLAUDE.md.

    This is the regression guard for the reconciled constant — if anyone
    lowers MAX_ANCESTOR_WALK below 20, this test catches it.
    """
    current = tmp_path
    # Build a 19-deep descent (root + 19 children = 20 directories total).
    for i in range(19):
        current = current / f"d{i}"
        current.mkdir()

    (tmp_path / "CLAUDE.md").write_text(
        "# proj\n\n## Lore\n\n- wiki: testwiki\n- scope: test\n"
    )

    from lore_core.session import _walk_up_lore_config
    result = _walk_up_lore_config(current)
    assert result is not None, (
        f"walk must find CLAUDE.md 19 levels up from {current} in a 20-depth walk"
    )
    claude_md, block = result
    assert claude_md == tmp_path / "CLAUDE.md"
    assert block.get("wiki") == "testwiki"


def test_walk_up_uses_read_attach_parser(tmp_path: Path) -> None:
    """Verifies consolidation: the canonical walk uses lore_core.attach.read_attach
    as its parser (not a local re-implementation).
    """
    (tmp_path / "CLAUDE.md").write_text(
        "# proj\n\n## Lore\n\n<!-- managed by /lore:attach -->\n\n- wiki: ccat\n- scope: ingest\n"
    )

    from lore_core.session import _walk_up_lore_config
    result = _walk_up_lore_config(tmp_path)
    assert result is not None
    _, block = result
    # read_attach skips the HTML comment and parses cleanly.
    assert block.get("wiki") == "ccat"
    assert block.get("scope") == "ingest"


def test_hooks_no_longer_defines_find_lore_config() -> None:
    """The old duplicate is gone."""
    from lore_cli import hooks
    assert not hasattr(hooks, "_find_lore_config"), (
        "lore_cli.hooks._find_lore_config should have been deleted in Task 7 "
        "in favor of lore_core.session._walk_up_lore_config"
    )


def test_no_duplicate_max_ancestor_walk_constant() -> None:
    """Only lore_core.session defines MAX_ANCESTOR_WALK after Task 7."""
    from lore_cli import hooks
    from lore_core import session

    assert hasattr(session, "MAX_ANCESTOR_WALK")
    assert not hasattr(hooks, "MAX_ANCESTOR_WALK"), (
        "lore_cli.hooks.MAX_ANCESTOR_WALK was a duplicate — should be gone."
    )
