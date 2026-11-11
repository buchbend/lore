from pathlib import Path

import pytest

from lore_core.run_reader import SchemaVersionTooNew, read_run


def _seed(tmp_path: Path, ids: list[str]) -> Path:
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    for rid in ids:
        (runs / f"{rid}.jsonl").write_text(
            '{"type":"run-start","schema_version":1}\n'
            '{"type":"run-end","schema_version":1}\n'
        )
    return runs


def test_read_run_parses_jsonl(tmp_path):
    runs = _seed(tmp_path, ["2026-04-20T10-00-00-aaaaaa"])
    records = read_run(runs / "2026-04-20T10-00-00-aaaaaa.jsonl")
    types = [r["type"] for r in records]
    assert types == ["run-start", "run-end"]


def test_read_run_tolerates_malformed_midfile(tmp_path):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    path = runs / "2026-04-20T10-00-00-aaaaaa.jsonl"
    path.write_text(
        '{"type":"run-start","schema_version":1}\n'
        'not json at all\n'
        '{"type":"noteworthy","verdict":true,"schema_version":1}\n'
        '{"type":"run-end","schema_version":1}\n'
    )
    records = read_run(path)
    types = [r["type"] for r in records]
    assert types == ["run-start", "_malformed", "noteworthy", "run-end"]


def test_read_run_appends_synthetic_truncated_when_last_line_broken_and_no_runend(tmp_path):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    path = runs / "2026-04-20T10-00-00-aaaaaa.jsonl"
    path.write_text(
        '{"type":"run-start","schema_version":1}\n'
        '{"type":"noteworthy","verdict":true,"schema_version":1}\n'
        '{"type":"session-note","action":"fi'  # truncated (no trailing newline, invalid JSON)
    )
    records = read_run(path)
    types = [r["type"] for r in records]
    assert types[-1] == "run-truncated"


def test_read_run_schema_too_new_strict_raises(tmp_path):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    path = runs / "2026-04-20T10-00-00-aaaaaa.jsonl"
    path.write_text(
        '{"type":"run-start","schema_version":2}\n'
        '{"type":"run-end","schema_version":2}\n'
    )
    with pytest.raises(SchemaVersionTooNew):
        read_run(path, strict_schema=True)


def test_read_run_schema_too_new_non_strict_tags(tmp_path):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    path = runs / "2026-04-20T10-00-00-aaaaaa.jsonl"
    path.write_text(
        '{"type":"run-start","schema_version":2}\n'
        '{"type":"run-end","schema_version":2}\n'
    )
    records = read_run(path, strict_schema=False)
    assert all(r.get("_schema_mismatch") for r in records)
