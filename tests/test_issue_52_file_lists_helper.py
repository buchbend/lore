"""Issue #52: ``file_lists_for_frontmatter`` — single source of truth
for the new schema's ``files_modified`` / ``files_read`` shape."""
from __future__ import annotations

from lore_curator.stub_note import file_lists_for_frontmatter


def test_modified_only_emits_modified_only():
    out = file_lists_for_frontmatter(["/a", "/b"], [])
    assert out == {"files_modified": ["/a", "/b"]}


def test_read_subsumed_by_modified_is_suppressed():
    """The dominant editing-session case: every read was also edited
    → ``files_read`` is omitted to keep frontmatter tidy."""
    out = file_lists_for_frontmatter(["/a", "/b"], ["/a", "/b"])
    assert out == {"files_modified": ["/a", "/b"]}


def test_partial_subset_of_reads_is_suppressed():
    out = file_lists_for_frontmatter(["/a", "/b"], ["/a", "/c"])
    assert out == {"files_modified": ["/a", "/b"], "files_read": ["/c"]}


def test_disjoint_modified_and_read():
    out = file_lists_for_frontmatter(["/a"], ["/b", "/c"])
    assert out == {"files_modified": ["/a"], "files_read": ["/b", "/c"]}


def test_both_empty_returns_empty():
    assert file_lists_for_frontmatter([], []) == {}


def test_read_only_interview_session():
    """No edits, just reads — interview / code-tour shape. ``files_read``
    is the only field, ``files_modified`` is omitted entirely."""
    out = file_lists_for_frontmatter([], ["/a", "/b"])
    assert out == {"files_read": ["/a", "/b"]}


def test_dedup_preserves_order():
    out = file_lists_for_frontmatter(["/a", "/b", "/a"], ["/c", "/c", "/d"])
    assert out == {"files_modified": ["/a", "/b"], "files_read": ["/c", "/d"]}


def test_none_inputs_treated_as_empty():
    assert file_lists_for_frontmatter(None, None) == {}
    assert file_lists_for_frontmatter(None, ["/a"]) == {"files_read": ["/a"]}
    assert file_lists_for_frontmatter(["/a"], None) == {"files_modified": ["/a"]}


def test_files_read_uncapped():
    """Read-side recall on file-name queries can't afford a silent
    truncation. Both lists are uncapped in frontmatter."""
    reads = [f"/file/{i}.py" for i in range(100)]
    out = file_lists_for_frontmatter([], reads)
    assert out == {"files_read": reads}
    assert len(out["files_read"]) == 100
