"""Core repo-native pull logic for ADRs/PRDs (`lore_core.repo_docs`).

Reads a connected repo's ratified decisions from their conventional,
hard-coded homes (``docs/adr/``, ``docs/prd/``) instead of extracting
them from session transcripts — repos own decisions, lore owns session
history. Configurable homes are explicitly out of scope.
"""

from __future__ import annotations

from pathlib import Path

from lore_core.repo_docs import HOMES, list_docs, read_doc, resolve_doc


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_homes_are_hard_coded() -> None:
    assert HOMES == {"adr": "docs/adr", "prd": "docs/prd"}


def test_list_docs_empty_when_home_missing(tmp_path: Path) -> None:
    assert list_docs(tmp_path, "adr") == []
    assert list_docs(tmp_path, "prd") == []


def test_list_docs_returns_entries_with_title_and_status(tmp_path: Path) -> None:
    _write(
        tmp_path / "docs/adr/0001-use-sqlite.md",
        "---\ntitle: Use SQLite\nstatus: accepted\n---\nBody text.\n",
    )
    entries = list_docs(tmp_path, "adr")
    assert len(entries) == 1
    assert entries[0]["path"] == "docs/adr/0001-use-sqlite.md"
    assert entries[0]["title"] == "Use SQLite"
    assert entries[0]["status"] == "accepted"
    assert entries[0]["is_index"] is False


def test_list_docs_falls_back_to_h1_then_filename_for_title(tmp_path: Path) -> None:
    _write(tmp_path / "docs/prd/0002-no-frontmatter.md", "# A Plain Title\n\nBody.\n")
    _write(tmp_path / "docs/prd/0003-bare.md", "No heading, no frontmatter.\n")
    entries = {e["path"]: e for e in list_docs(tmp_path, "prd")}
    assert entries["docs/prd/0002-no-frontmatter.md"]["title"] == "A Plain Title"
    assert entries["docs/prd/0003-bare.md"]["title"] == "0003-bare"


def test_list_docs_includes_index_file_and_sorts_it_first(tmp_path: Path) -> None:
    _write(tmp_path / "docs/adr/0001-a.md", "---\ntitle: A\n---\nbody\n")
    _write(tmp_path / "docs/adr/README.md", "# ADR index\n")
    entries = list_docs(tmp_path, "adr")
    assert [e["path"] for e in entries] == ["docs/adr/README.md", "docs/adr/0001-a.md"]
    assert entries[0]["is_index"] is True
    assert entries[1]["is_index"] is False


def test_resolve_doc_accepts_bare_slug_filename_and_full_path(tmp_path: Path) -> None:
    _write(tmp_path / "docs/adr/0001-x.md", "---\ntitle: X\n---\nbody\n")
    expected = (tmp_path / "docs/adr/0001-x.md").resolve()
    assert resolve_doc(tmp_path, "adr", "0001-x") == expected
    assert resolve_doc(tmp_path, "adr", "0001-x.md") == expected
    assert resolve_doc(tmp_path, "adr", "docs/adr/0001-x.md") == expected


def test_resolve_doc_none_for_missing_or_escaping_path(tmp_path: Path) -> None:
    _write(tmp_path / "docs/adr/0001-x.md", "body\n")
    assert resolve_doc(tmp_path, "adr", "does-not-exist") is None
    assert resolve_doc(tmp_path, "adr", "../../../etc/passwd") is None
    assert resolve_doc(tmp_path, "adr", "../prd/0002-y") is None


def test_read_doc_returns_content_title_status(tmp_path: Path) -> None:
    _write(
        tmp_path / "docs/prd/0001-thing.md",
        "---\ntitle: The Thing\nstatus: draft\n---\nBody of the PRD.\n",
    )
    doc = read_doc(tmp_path, "prd", "0001-thing")
    assert doc is not None
    assert doc["path"] == "docs/prd/0001-thing.md"
    assert doc["title"] == "The Thing"
    assert doc["status"] == "draft"
    assert "Body of the PRD." in doc["content"]


def test_read_doc_none_when_not_found(tmp_path: Path) -> None:
    assert read_doc(tmp_path, "adr", "nope") is None
