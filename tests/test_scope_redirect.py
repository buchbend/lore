"""Tests for cross-scope bleed guard — _extract_tool_file_paths + _detect_scope_override."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_core.types import Scope, ToolCall, Turn

from lore_curator.curator_a import (
    _detect_scope_override,
    _extract_tool_file_paths,
)


_NOW = datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)


def _tool_turn(name: str, input_: dict, index: int = 0) -> Turn:
    return Turn(
        index=index,
        timestamp=_NOW,
        role="assistant",
        tool_call=ToolCall(name=name, input=input_),
    )


def _text_turn(text: str, index: int = 0) -> Turn:
    return Turn(index=index, timestamp=_NOW, role="assistant", text=text)


# ---------------------------------------------------------------------------
# _extract_tool_file_paths
# ---------------------------------------------------------------------------


class TestExtractToolFilePaths:
    def test_basic_read_write_edit(self):
        turns = [
            _tool_turn("Read", {"file_path": "/home/u/git/repo/foo.py"}, 0),
            _tool_turn("Write", {"file_path": "/home/u/git/repo/bar.py"}, 1),
            _tool_turn("Edit", {"file_path": "/home/u/git/other/baz.py"}, 2),
        ]
        paths = _extract_tool_file_paths(turns)
        assert len(paths) == 3
        assert paths[0] == Path("/home/u/git/repo/foo.py")
        assert paths[2] == Path("/home/u/git/other/baz.py")

    def test_ignores_non_file_tools(self):
        turns = [
            _tool_turn("Bash", {"command": "ls /home/u/git/repo"}, 0),
            _tool_turn("Skill", {"skill": "review"}, 1),
            _tool_turn("Agent", {"prompt": "do stuff"}, 2),
        ]
        assert _extract_tool_file_paths(turns) == []

    def test_ignores_relative_paths(self):
        turns = [
            _tool_turn("Read", {"file_path": "relative/path.py"}, 0),
            _tool_turn("Write", {"file_path": "./also/relative.py"}, 1),
        ]
        assert _extract_tool_file_paths(turns) == []

    def test_ignores_temp_and_dev_paths(self):
        turns = [
            _tool_turn("Read", {"file_path": "/tmp/scratch.py"}, 0),
            _tool_turn("Read", {"file_path": "/dev/null"}, 1),
            _tool_turn("Read", {"file_path": "/proc/self/status"}, 2),
        ]
        assert _extract_tool_file_paths(turns) == []

    def test_skips_text_turns(self):
        turns = [
            _text_turn("hello", 0),
            _tool_turn("Read", {"file_path": "/home/u/foo.py"}, 1),
        ]
        paths = _extract_tool_file_paths(turns)
        assert len(paths) == 1

    def test_missing_file_path_key(self):
        turns = [_tool_turn("Read", {"other_key": "value"}, 0)]
        assert _extract_tool_file_paths(turns) == []

    def test_non_string_file_path(self):
        turns = [_tool_turn("Write", {"file_path": 42}, 0)]
        assert _extract_tool_file_paths(turns) == []


# ---------------------------------------------------------------------------
# _detect_scope_override
# ---------------------------------------------------------------------------


_SCOPE_A = Scope(wiki="private", scope="lore", backend="none", claude_md_path=Path("/a/CLAUDE.md"))
_SCOPE_B = Scope(wiki="ccat", scope="ccat:data", backend="none", claude_md_path=Path("/b/CLAUDE.md"))


def _fake_resolver(mapping: dict[str, Scope | None]):
    """Return a resolver that maps path prefixes to scopes."""
    def resolver(p: Path) -> Scope | None:
        s = str(p)
        for prefix, scope in sorted(mapping.items(), key=lambda x: -len(x[0])):
            if s.startswith(prefix):
                return scope
        return None
    return resolver


class TestDetectScopeOverride:
    def test_majority_redirect(self):
        """4/5 files in wiki B → redirect to B."""
        paths = [
            Path("/home/u/git/ccat/a.py"),
            Path("/home/u/git/ccat/b.py"),
            Path("/home/u/git/ccat/c.py"),
            Path("/home/u/git/ccat/d.py"),
            Path("/home/u/git/lore/e.py"),
        ]
        resolver = _fake_resolver({
            "/home/u/git/ccat": _SCOPE_B,
            "/home/u/git/lore": _SCOPE_A,
        })
        result = _detect_scope_override(paths, _SCOPE_A, resolver)
        assert result is not None
        assert result.wiki == "ccat"

    def test_no_redirect_when_below_threshold(self):
        """50/50 split → no redirect."""
        paths = [
            Path("/home/u/git/ccat/a.py"),
            Path("/home/u/git/lore/b.py"),
        ]
        resolver = _fake_resolver({
            "/home/u/git/ccat": _SCOPE_B,
            "/home/u/git/lore": _SCOPE_A,
        })
        result = _detect_scope_override(paths, _SCOPE_A, resolver)
        assert result is None

    def test_no_redirect_when_all_same_wiki(self):
        """All files in launch wiki → no redirect."""
        paths = [
            Path("/home/u/git/lore/a.py"),
            Path("/home/u/git/lore/b.py"),
        ]
        resolver = _fake_resolver({"/home/u/git/lore": _SCOPE_A})
        result = _detect_scope_override(paths, _SCOPE_A, resolver)
        assert result is None

    def test_no_redirect_when_no_files(self):
        resolver = _fake_resolver({})
        assert _detect_scope_override([], _SCOPE_A, resolver) is None

    def test_no_redirect_when_all_unresolvable(self):
        paths = [Path("/unknown/dir/foo.py")]
        resolver = _fake_resolver({})
        assert _detect_scope_override(paths, _SCOPE_A, resolver) is None

    def test_keeps_most_specific_scope(self):
        """When multiple files resolve to same wiki with different scope depth, keep longest."""
        scope_shallow = Scope(wiki="ccat", scope="ccat", backend="none", claude_md_path=Path("/b/CLAUDE.md"))
        scope_deep = Scope(wiki="ccat", scope="ccat:data:transfer", backend="none", claude_md_path=Path("/b/sub/CLAUDE.md"))
        paths = [
            Path("/home/u/git/ccat/top.py"),
            Path("/home/u/git/ccat/sub/deep.py"),
            Path("/home/u/git/ccat/sub/deep2.py"),
        ]

        def resolver(p: Path) -> Scope | None:
            s = str(p)
            if "/sub/" in s:
                return scope_deep
            if "/ccat/" in s:
                return scope_shallow
            return None

        result = _detect_scope_override(paths, _SCOPE_A, resolver)
        assert result is not None
        assert result.scope == "ccat:data:transfer"

    def test_exactly_at_threshold(self):
        """3/5 = 0.6 → exactly at threshold → redirect."""
        paths = [
            Path("/home/u/git/ccat/a.py"),
            Path("/home/u/git/ccat/b.py"),
            Path("/home/u/git/ccat/c.py"),
            Path("/home/u/git/lore/d.py"),
            Path("/home/u/git/lore/e.py"),
        ]
        resolver = _fake_resolver({
            "/home/u/git/ccat": _SCOPE_B,
            "/home/u/git/lore": _SCOPE_A,
        })
        result = _detect_scope_override(paths, _SCOPE_A, resolver)
        assert result is not None
        assert result.wiki == "ccat"


# ---------------------------------------------------------------------------
# Integration: scope_redirected_from in frontmatter
# ---------------------------------------------------------------------------


class TestScopeRedirectFrontmatter:
    def test_new_note_has_scope_redirected_from(self, tmp_path):
        from lore_core.schema import parse_frontmatter
        from lore_curator.noteworthy import NoteworthyResult
        from lore_curator.session_filer import file_session_note
        from lore_core.types import TranscriptHandle

        wiki_dir = tmp_path / "wiki" / "ccat"
        (wiki_dir / "sessions").mkdir(parents=True)

        handle = TranscriptHandle(
            host="fake", id="txn-001",
            path=tmp_path / "t.jsonl", cwd=tmp_path,
            mtime=_NOW,
        )
        noteworthy = NoteworthyResult(
            noteworthy=True, reason="test", title="Test Note",
            summary="A test", bullets=["did stuff"], files_touched=[],
            entities=[], decisions=[],
        )
        scope = Scope(wiki="ccat", scope="ccat:data", backend="none",
                       claude_md_path=tmp_path / "CLAUDE.md")
        turns = [Turn(index=0, timestamp=_NOW, role="user", text="hi")]

        filed = file_session_note(
            scope=scope, handle=handle, noteworthy=noteworthy, turns=turns,
            wiki_root=wiki_dir, now=_NOW, work_time=_NOW,
            scope_redirected_from="lore",
        )

        fm = parse_frontmatter(filed.path.read_text())
        assert fm["scope"] == "ccat:data"
        assert fm["scope_redirected_from"] == "lore"

    def test_new_note_without_redirect_has_no_field(self, tmp_path):
        from lore_core.schema import parse_frontmatter
        from lore_curator.noteworthy import NoteworthyResult
        from lore_curator.session_filer import file_session_note
        from lore_core.types import TranscriptHandle

        wiki_dir = tmp_path / "wiki" / "private"
        (wiki_dir / "sessions").mkdir(parents=True)

        handle = TranscriptHandle(
            host="fake", id="txn-002",
            path=tmp_path / "t.jsonl", cwd=tmp_path,
            mtime=_NOW,
        )
        noteworthy = NoteworthyResult(
            noteworthy=True, reason="test", title="Normal Note",
            summary="Normal", bullets=["stuff"], files_touched=[],
            entities=[], decisions=[],
        )
        scope = Scope(wiki="private", scope="lore", backend="none",
                       claude_md_path=tmp_path / "CLAUDE.md")
        turns = [Turn(index=0, timestamp=_NOW, role="user", text="hi")]

        filed = file_session_note(
            scope=scope, handle=handle, noteworthy=noteworthy, turns=turns,
            wiki_root=wiki_dir, now=_NOW, work_time=_NOW,
        )

        fm = parse_frontmatter(filed.path.read_text())
        assert "scope_redirected_from" not in fm
