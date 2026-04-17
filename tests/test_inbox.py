"""Tests for `lore_core.inbox` and the `lore inbox` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lore_cli import inbox_cmd
from lore_core.inbox import archive, classify


@pytest.fixture
def inbox_vault(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    (vault_root / "inbox").mkdir(parents=True)
    (vault_root / "inbox" / "report.pdf").write_bytes(b"%PDF-fake")
    (vault_root / "inbox" / "scratch.md").write_text("# scratch\n")
    (vault_root / "inbox" / ".processed").mkdir()
    (vault_root / "inbox" / ".processed" / "ignored.md").write_text("nope")
    (vault_root / "inbox" / ".dotfile").write_text("hidden")

    (vault_root / "wiki" / "ccat" / "inbox").mkdir(parents=True)
    (vault_root / "wiki" / "ccat" / "inbox" / "screenshot.png").write_bytes(b"\x89PNG-fake")

    (vault_root / "wiki" / "private" / "inbox").mkdir(parents=True)
    (vault_root / "wiki" / "private" / "inbox" / "thoughts.txt").write_text("idea")

    # LORE_ROOT points at the vault's parent of wiki/, per get_wiki_root()
    # convention (wiki_root = LORE_ROOT/wiki)
    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    return vault_root


def test_classify_walks_all_inboxes(inbox_vault):
    result = classify(vault_root=inbox_vault)
    assert result["total"] == 4
    types = result["by_type"]
    assert types["pdf"] == 1
    assert types["markdown"] == 1
    assert types["image"] == 1
    assert types["text"] == 1
    inboxes = result["by_inbox"]
    assert "(root)" in inboxes
    assert "ccat" in inboxes
    assert "private" in inboxes


def test_classify_root_inbox_needs_triage(inbox_vault):
    result = classify(vault_root=inbox_vault)
    root_files = [f for f in result["files"] if f["target_wiki"] is None]
    assert len(root_files) == 2
    for f in root_files:
        assert f["needs_triage"] is True


def test_classify_per_wiki_inbox_pre_routed(inbox_vault):
    result = classify(vault_root=inbox_vault)
    for f in result["files"]:
        if f["filename"] == "screenshot.png":
            assert f["target_wiki"] == "ccat"
            assert f["needs_triage"] is False
        if f["filename"] == "thoughts.txt":
            assert f["target_wiki"] == "private"


def test_classify_skips_processed_and_hidden(inbox_vault):
    result = classify(vault_root=inbox_vault)
    names = {f["filename"] for f in result["files"]}
    assert ".dotfile" not in names
    assert "ignored.md" not in names


def test_archive_moves_file(inbox_vault):
    src = inbox_vault / "inbox" / "scratch.md"
    assert src.exists()
    result = archive(source=src)
    assert "error" not in result
    assert not src.exists()
    archived = Path(result["archived_to"])
    assert archived.exists()
    assert archived.parent.name == ".processed"
    assert archived.read_text() == "# scratch\n"


def test_archive_no_clobber_with_same_name(inbox_vault):
    src = inbox_vault / "inbox" / "scratch.md"
    archive(source=src)
    # Re-create with same name and archive again — must not clobber
    src.write_text("# scratch v2\n")
    result = archive(source=src)
    assert "error" not in result
    archived = Path(result["archived_to"])
    assert "_01_" in archived.name or "_02_" in archived.name


def test_archive_missing_source(inbox_vault):
    result = archive(source=inbox_vault / "inbox" / "nope.md")
    assert "error" in result


def test_cli_classify_emits_envelope(inbox_vault, capsys, monkeypatch):
    # CLI uses get_wiki_root which strips /wiki to find vault_root,
    # so monkeypatch LORE_ROOT to point at the wiki dir as the fixture does
    rc = inbox_cmd.main(["classify"])
    assert rc == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["schema"] == "lore.inbox.classify/1"
    assert envelope["data"]["total"] == 4


def test_cli_archive(inbox_vault, capsys):
    src = inbox_vault / "inbox" / "scratch.md"
    rc = inbox_cmd.main(["archive", str(src), "--json"])
    assert rc == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["schema"] == "lore.inbox.archive/1"
    assert "archived_to" in envelope["data"]
    assert not src.exists()
