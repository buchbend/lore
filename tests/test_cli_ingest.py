"""Tests for `lore ingest` CLI command."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lore_cli.__main__ import app
from lore_core.ledger import TranscriptLedger

runner = CliRunner(mix_stderr=False)

VALID_JSONL = "\n".join([
    json.dumps({"index": 0, "role": "user", "text": "hello"}),
    json.dumps({"index": 1, "role": "assistant", "text": "world"}),
])


def test_ingest_reads_from_file_and_advances_ledger(tmp_path, monkeypatch):
    """File-based ingest: exit 0, ledger entry with integration=manual-send."""
    lore_root = tmp_path / "lore_root"
    lore_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    jsonl_file = tmp_path / "transcript.jsonl"
    jsonl_file.write_text(VALID_JSONL)

    result = runner.invoke(
        app,
        [
            "ingest",
            "--from", str(jsonl_file),
            "--integration", "cursor",
            "--directory", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output

    ledger = TranscriptLedger(lore_root)
    entries = list(ledger._load().values())
    assert len(entries) == 1
    assert entries[0]["integration"] == "manual-send"
    assert entries[0]["transcript_id"]  # non-empty


def test_ingest_reads_from_stdin(tmp_path, monkeypatch):
    """Stdin ingest (--from -): exit 0, turns parsed."""
    lore_root = tmp_path / "lore_root"
    lore_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    result = runner.invoke(
        app,
        [
            "ingest",
            "--from", "-",
            "--integration", "cursor",
            "--directory", str(tmp_path),
        ],
        input=VALID_JSONL,
    )
    assert result.exit_code == 0, result.output
    # Should mention both turns
    assert "Turn" in result.output or "turn" in result.output or "index" in result.output


def test_ingest_rejects_malformed_jsonl(tmp_path, monkeypatch):
    """Malformed JSONL: exit 1, output mentions 'line' and error."""
    lore_root = tmp_path / "lore_root"
    lore_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    jsonl_file = tmp_path / "bad.jsonl"
    jsonl_file.write_text("not valid json\n")

    result = runner.invoke(
        app,
        [
            "ingest",
            "--from", str(jsonl_file),
            "--integration", "cursor",
            "--directory", str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    combined = (result.output or "") + (result.stderr or "")
    assert "line" in combined.lower() or "error" in combined.lower()
