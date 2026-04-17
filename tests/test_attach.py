"""Tests for `lore attach` / `lore detach` CLAUDE.md managed-block logic.

Contract (from concepts/lore/claude-md-as-scope-anchor.md):
  - The `## Lore` heading is the boundary. Nothing outside is ever touched.
  - Lore-owned keys (wiki/scope/backend/issues/prs) are upserted.
  - User-added bullets and non-bullet content are preserved verbatim.
  - Re-running is idempotent.
  - `/lore:detach` removes the section only, cleanly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_cli.attach_cmd import (
    LORE_KEYS,
    MANAGED_COMMENT,
    SECTION_HEADING,
    find_section,
    parse_section_body,
    read_attach,
    remove_section,
    write_attach,
)

SPEC = {
    "wiki": "ccat",
    "scope": "ccat:data-center:data-transfer",
    "backend": "github",
    "issues": "--assignee @me --state open",
    "prs": "--author @me",
}


@pytest.fixture
def claude_md(tmp_path: Path) -> Path:
    return tmp_path / "CLAUDE.md"


# ---------- parse helpers ----------


def test_find_section_absent():
    assert find_section(["# Project", "", "Some docs."]) is None


def test_find_section_until_next_heading():
    lines = [
        "# Title",
        "",
        "## Lore",
        "",
        "- wiki: ccat",
        "",
        "## Next section",
        "body",
    ]
    bounds = find_section(lines)
    assert bounds == (2, 6)


def test_find_section_to_eof():
    lines = ["## Lore", "", "- wiki: ccat"]
    assert find_section(lines) == (0, 3)


def test_parse_section_body():
    body = [
        "",
        MANAGED_COMMENT,
        "",
        "- wiki: ccat",
        "- scope: ccat:foo",
        "- custom: hello world",
        "",
    ]
    parsed = parse_section_body(body)
    assert parsed == {
        "wiki": "ccat",
        "scope": "ccat:foo",
        "custom": "hello world",
    }


# ---------- write: three CLAUDE.md cases ----------


def test_write_creates_file_when_missing(claude_md):
    write_attach(claude_md, SPEC)
    assert claude_md.exists()
    assert read_attach(claude_md) == SPEC
    text = claude_md.read_text()
    assert text.startswith(SECTION_HEADING)
    assert MANAGED_COMMENT in text
    assert text.endswith("\n")


def test_write_appends_when_file_exists_without_section(claude_md):
    original = "# Project\n\nExisting docs.\nMore prose.\n"
    claude_md.write_text(original)
    write_attach(claude_md, SPEC)
    text = claude_md.read_text()
    assert text.startswith("# Project\n\nExisting docs.\nMore prose.\n")
    assert SECTION_HEADING in text
    assert read_attach(claude_md) == SPEC


def test_write_upserts_when_section_exists(claude_md):
    original = (
        "# Project\n\n"
        "## Lore\n\n"
        f"{MANAGED_COMMENT}\n\n"
        "- wiki: old-wiki\n"
        "- scope: old:scope\n"
        "- backend: github\n"
        "- issues: --state all\n"
        "- prs: --author @me\n"
    )
    claude_md.write_text(original)
    write_attach(claude_md, {"wiki": "ccat", "scope": "ccat:new"})
    parsed = read_attach(claude_md)
    assert parsed["wiki"] == "ccat"
    assert parsed["scope"] == "ccat:new"
    # Keys not in the update payload stay put.
    assert parsed["backend"] == "github"
    assert parsed["issues"] == "--state all"
    assert parsed["prs"] == "--author @me"


# ---------- idempotency ----------


def test_write_twice_is_idempotent(claude_md):
    claude_md.write_text("# Hi\n\nprose\n")
    write_attach(claude_md, SPEC)
    first = claude_md.read_text()
    write_attach(claude_md, SPEC)
    second = claude_md.read_text()
    assert first == second


# ---------- preservation ----------


def test_content_outside_section_never_touched(claude_md):
    before_section = "# Project\n\nProse with **formatting**.\n\n```python\ncode_block = 1\n```\n\n"
    after_section = "\n## After\n\nMore prose.\n- not a lore bullet\n"
    claude_md.write_text(before_section + SECTION_HEADING + "\n\n" + after_section)
    write_attach(claude_md, SPEC)
    text = claude_md.read_text()
    assert text.startswith(before_section)
    assert after_section.rstrip("\n") in text


def test_user_added_bullets_preserved(claude_md):
    original = (
        "## Lore\n\n"
        f"{MANAGED_COMMENT}\n\n"
        "- wiki: ccat\n"
        "- scope: ccat:foo\n"
        "- custom-key: user-value\n"
        "- another: thing\n"
    )
    claude_md.write_text(original)
    write_attach(claude_md, {"wiki": "ccat", "scope": "ccat:bar"})
    parsed = read_attach(claude_md)
    assert parsed["scope"] == "ccat:bar"
    assert parsed["custom-key"] == "user-value"
    assert parsed["another"] == "thing"


def test_write_rejects_non_lore_keys_in_update(claude_md):
    """User-added keys come via editing the file, not the CLI."""
    write_attach(claude_md, {**SPEC, "custom": "x"})
    parsed = read_attach(claude_md)
    assert "custom" not in parsed
    assert parsed["wiki"] == SPEC["wiki"]


def test_only_lore_keys_on_initial_write(claude_md):
    write_attach(claude_md, SPEC)
    parsed = read_attach(claude_md)
    assert set(parsed.keys()) == set(LORE_KEYS)


# ---------- detach ----------


def test_detach_removes_section_only(claude_md):
    original_head = "# Project\n\nProse.\n\n"
    original_tail = "\n## After\n\nTail content.\n"
    claude_md.write_text(original_head + SECTION_HEADING + "\n\n- wiki: ccat\n" + original_tail)
    assert remove_section(claude_md) is True
    text = claude_md.read_text()
    assert SECTION_HEADING not in text
    assert "# Project" in text
    assert "## After" in text
    assert "Tail content." in text


def test_detach_is_noop_when_absent(claude_md):
    claude_md.write_text("# Project\n\nNothing here.\n")
    assert remove_section(claude_md) is False
    assert claude_md.read_text() == "# Project\n\nNothing here.\n"


def test_detach_missing_file_is_noop(tmp_path):
    assert remove_section(tmp_path / "does-not-exist.md") is False


# ---------- round trip ----------


def test_attach_detach_reattach_round_trip(claude_md):
    seed = "# Project\n\nProse.\n"
    claude_md.write_text(seed)

    write_attach(claude_md, SPEC)
    assert read_attach(claude_md) == SPEC

    remove_section(claude_md)
    assert read_attach(claude_md) == {}
    assert "# Project" in claude_md.read_text()

    write_attach(claude_md, SPEC)
    assert read_attach(claude_md) == SPEC


# ---------- boundary edge cases ----------


def test_section_with_only_comment_no_bullets(claude_md):
    original = f"# P\n\n## Lore\n\n{MANAGED_COMMENT}\n"
    claude_md.write_text(original)
    write_attach(claude_md, {"wiki": "ccat", "scope": "ccat:x"})
    parsed = read_attach(claude_md)
    assert parsed == {"wiki": "ccat", "scope": "ccat:x"}
    # Comment survived.
    assert MANAGED_COMMENT in claude_md.read_text()


def test_read_absent_returns_empty_dict(claude_md):
    claude_md.write_text("# Project\n")
    assert read_attach(claude_md) == {}


def test_read_missing_file_returns_empty_dict(tmp_path):
    assert read_attach(tmp_path / "nope.md") == {}
