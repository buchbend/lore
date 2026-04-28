"""Tests for the linter's wikilink-discipline exemptions and the
cross-folder index-detection fix.

Cleanup pass driven by the historical lint report: the wikilink
discipline (see ``lore_core/session_templates/standard.md``) forbids
``[[file/path.py]]``, ``[[PR #N]]``, etc. in session-note bodies.
The linter mirrors that discipline by suppressing ``broken_link``
warnings for targets it can prove are not vault-note candidates,
exempts ``papers/*`` from the ``oversized`` split-candidate hint
(papers are long by nature), and tightens the hierarchy check so a
folder named ``lore/`` in ``concepts/`` does not pool its notes with
an unrelated ``decisions/lore/`` folder.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lore_core.lint import (
    NoteInfo,
    _is_non_note_link_target,
    check_hierarchy,
    check_wikilinks,
)


# ---------------------------------------------------------------------------
# _is_non_note_link_target — wikilink-discipline predicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        # File / dir paths
        "lib/lore_cli/hooks.py",
        ".claude-plugin/plugin.json",
        "/home/buchbend/git/orgs/ccatobs",
        "$LORE_CACHE/sessions/",
        "~/.claude/plans/foo.md",
        # Repo / branch refs
        "ccatobs/system-integration",
        "feature/process-compose branch",
        # PR / issue refs
        "PR #77",
        "issue #29",
        "#42",
        "ops-db#66",
        "ccatobs/obs_implementation#13",
        # URLs
        "https://example.com/x",
        # Version strings
        "v0.13.1",
        "1.100.0",
        # File-extension suffixes (no slash)
        "CHANGELOG.md",
        "hooks.py",
        # Env vars / shouty identifiers
        "CLAUDE_SESSION_ID",
        "ENV_FOR_DYNACONF",
    ],
)
def test_non_note_targets_are_skipped(target):
    assert _is_non_note_link_target(target) is True


@pytest.mark.parametrize(
    "target",
    [
        # Bare slugs that COULD resolve to a vault note — the linter
        # should still flag these as broken_link if missing.
        "lore-thesis",
        "curator-b",
        "session-note-schema-v2",
        # Concept-style names with spaces (legitimate vault note titles
        # before the discipline tightened — still worth flagging so the
        # author can either promote or demote them).
        "Curator B",
        "CCAT Data Center",
        "Dynaconf",
    ],
)
def test_concept_targets_still_flag(target):
    assert _is_non_note_link_target(target) is False


# ---------------------------------------------------------------------------
# check_wikilinks suppresses broken_link for non-note targets
# ---------------------------------------------------------------------------


def _note(filename: str, *, links_out=None, wiki: str = "private", path: str | None = None) -> NoteInfo:
    return NoteInfo(
        path=path or f"sessions/2026/04/{filename}.md",
        filename=filename,
        wiki=wiki,
        note_type="session",
        links_out=list(links_out or []),
    )


def test_check_wikilinks_skips_file_path_and_pr_targets():
    note = _note(
        "28-some-session",
        links_out=[
            "lib/lore_cli/hooks.py",   # file path → suppressed
            "PR #77",                  # PR ref → suppressed
            "missing-concept",         # bare slug → still flagged
        ],
    )
    issues = check_wikilinks({"28-some-session": note})
    broken = [i for i in issues if i.check == "broken_link"]
    assert len(broken) == 1
    assert "[[missing-concept]]" in broken[0].message


def test_check_wikilinks_resolves_existing_targets():
    """Pre-existing behaviour preserved: links to known notes don't flag."""
    target = _note("real-target", path="concepts/foo/real-target.md")
    note = _note("28-some-session", links_out=["real-target"])
    issues = check_wikilinks({"28-some-session": note, "real-target": target})
    assert not any(i.check == "broken_link" for i in issues)


# ---------------------------------------------------------------------------
# check_hierarchy: oversized exempts papers; cross-folder bleed fixed
# ---------------------------------------------------------------------------


def _make_paper(tmp_path: Path, name: str, lines: int) -> NoteInfo:
    """Materialise a paper note on disk and return its NoteInfo."""
    wiki_path = tmp_path / "wiki"
    papers = wiki_path / "papers"
    papers.mkdir(parents=True, exist_ok=True)
    (papers / f"{name}.md").write_text("body\n" * lines)
    return NoteInfo(
        path=f"papers/{name}.md",
        filename=name,
        wiki="science",
        note_type="paper",
        lines=lines,
        parent_folder=None,
    )


def test_papers_are_exempt_from_oversized(tmp_path):
    wiki_path = tmp_path / "wiki"
    wiki_path.mkdir()
    (wiki_path / "papers").mkdir()
    (wiki_path / "concepts").mkdir()

    long_paper = _make_paper(tmp_path, "Kennicutt2012", lines=400)
    issues = check_hierarchy(
        {"science": [long_paper]},
        "science",
        wiki_path,
    )
    assert not any(i.check == "oversized" for i in issues)


def test_oversized_still_fires_for_non_paper_dirs(tmp_path):
    wiki_path = tmp_path / "wiki"
    (wiki_path / "concepts").mkdir(parents=True)
    long_concept_path = wiki_path / "concepts" / "long-thing.md"
    long_concept_path.write_text("body\n" * 200)

    note = NoteInfo(
        path="concepts/long-thing.md",
        filename="long-thing",
        wiki="private",
        note_type="concept",
        lines=200,
        parent_folder=None,
    )
    issues = check_hierarchy({"private": [note]}, "private", wiki_path)
    oversized = [i for i in issues if i.check == "oversized"]
    assert len(oversized) == 1
    assert "long-thing" in oversized[0].file


def test_cross_folder_index_does_not_pool_children(tmp_path):
    """Two folders named ``lore/`` under different knowledge dirs must
    not share an index — historically ``decisions/lore/lore-thesis.md``
    was being treated as the index for ``concepts/lore/`` siblings,
    producing dozens of bogus ``unlinked_subnote`` warnings.
    """
    wiki_path = tmp_path / "wiki"
    (wiki_path / "concepts" / "lore").mkdir(parents=True)
    (wiki_path / "decisions" / "lore").mkdir(parents=True)
    (wiki_path / "concepts" / "lore" / "claude-md.md").write_text("# x")
    (wiki_path / "concepts" / "lore" / "lore-handoff.md").write_text("# y")
    (wiki_path / "decisions" / "lore" / "lore-thesis.md").write_text("# z" * 5)

    concepts_child = NoteInfo(
        path="concepts/lore/claude-md.md",
        filename="claude-md",
        wiki="private",
        parent_folder="lore",
        lines=10,
        links_out=[],
    )
    concepts_prefix_match = NoteInfo(
        path="concepts/lore/lore-handoff.md",
        filename="lore-handoff",
        wiki="private",
        parent_folder="lore",
        lines=10,
        links_out=[],
    )
    decisions_thesis = NoteInfo(
        path="decisions/lore/lore-thesis.md",
        filename="lore-thesis",
        wiki="private",
        parent_folder="lore",
        lines=300,
        links_out=[],  # thesis links nothing; not a real index
    )
    notes = [concepts_child, concepts_prefix_match, decisions_thesis]

    issues = check_hierarchy({"private": notes}, "private", wiki_path)

    # The bogus cross-folder backlink demand must be gone: the concepts
    # child should never be told to backlink to ``[[lore-thesis]]``.
    cross_folder = [
        i for i in issues
        if i.check == "unlinked_subnote"
        and i.file == "concepts/lore/claude-md.md"
        and "lore-thesis" in i.message
    ]
    assert not cross_folder, (
        f"concepts/lore/ child should not be forced to backlink "
        f"decisions/lore/lore-thesis: {cross_folder}"
    )


def test_no_unlinked_subnote_when_index_is_missing(tmp_path):
    """A folder with no real index should produce a single
    ``missing_index`` warning, not an ``unlinked_subnote`` for each
    sibling demanding a backlink to a phantom target."""
    wiki_path = tmp_path / "wiki"
    (wiki_path / "concepts" / "lore").mkdir(parents=True)
    for name in ("foo", "bar", "baz"):
        (wiki_path / "concepts" / "lore" / f"{name}.md").write_text("body")

    siblings = [
        NoteInfo(
            path=f"concepts/lore/{name}.md",
            filename=name,
            wiki="private",
            parent_folder="lore",
            lines=10,
            links_out=[],
        )
        for name in ("foo", "bar", "baz")
    ]

    issues = check_hierarchy({"private": siblings}, "private", wiki_path)

    assert any(i.check == "missing_index" for i in issues)
    assert not any(i.check == "unlinked_subnote" for i in issues)


def test_thesis_style_note_not_promoted_to_index(tmp_path):
    """A long topical note that just shares a prefix with its folder name
    but doesn't navigate (no sibling links) is no longer auto-promoted to
    "index" — so no ``index_too_large`` warning fires for what is really
    a thesis document."""
    wiki_path = tmp_path / "wiki"
    (wiki_path / "decisions" / "lore").mkdir(parents=True)
    for name in ("lore-thesis", "lore-orthogonality", "lore-plugin",
                 "claude-md-boundary", "team-capable", "vault-hierarchy"):
        (wiki_path / "decisions" / "lore" / f"{name}.md").write_text("x")

    siblings = [
        NoteInfo(
            path=f"decisions/lore/{name}.md",
            filename=name,
            wiki="private",
            parent_folder="lore",
            lines=20,
            links_out=[],
        )
        for name in ("lore-orthogonality", "lore-plugin",
                     "claude-md-boundary", "team-capable",
                     "vault-hierarchy")
    ]
    thesis = NoteInfo(
        path="decisions/lore/lore-thesis.md",
        filename="lore-thesis",
        wiki="private",
        parent_folder="lore",
        lines=300,
        links_out=[],  # zero sibling links — not a navigational index
    )

    issues = check_hierarchy(
        {"private": [thesis] + siblings},
        "private",
        wiki_path,
    )
    assert not any(i.check == "index_too_large" for i in issues)
    # No subnote should be told to backlink to the thesis either.
    assert not any(
        i.check == "unlinked_subnote" and "lore-thesis" in i.message
        for i in issues
    )
