import json
from pathlib import Path

import pytest

from lore_core.run_reader import (
    RunIdAmbiguous, RunIdNotFound, SchemaVersionTooNew,
    read_run, resolve_run_id,
)


def _seed(tmp_path: Path, ids: list[str]) -> Path:
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    for rid in ids:
        (runs / f"{rid}.jsonl").write_text(
            '{"type":"run-start","schema_version":1}\n'
            '{"type":"run-end","schema_version":1}\n'
        )
    return runs


def _seed_spine(tmp_path: Path, ids: list[str]) -> None:
    """Seed curator run-start/run-end envelopes for the given run_ids."""
    from lore_core.spine import SpineWriter

    w = SpineWriter(tmp_path)
    for rid in ids:
        w.emit(source="curator", event="run-start", run_id=rid, data={"trigger": "hook"})
        w.emit(source="curator", event="run-end", run_id=rid, data={"errors": 0})


def test_resolve_latest(tmp_path):
    _seed_spine(tmp_path, [
        "2026-04-20T10-00-00-aaaaaa",
        "2026-04-20T14-00-00-bbbbbb",
        "2026-04-20T18-00-00-cccccc",
    ])
    assert resolve_run_id(tmp_path, "latest") == "2026-04-20T18-00-00-cccccc"


def test_resolve_caret_N(tmp_path):
    _seed_spine(tmp_path, [
        "2026-04-20T10-00-00-aaaaaa",
        "2026-04-20T14-00-00-bbbbbb",
        "2026-04-20T18-00-00-cccccc",
    ])
    assert resolve_run_id(tmp_path, "^1") == "2026-04-20T18-00-00-cccccc"
    assert resolve_run_id(tmp_path, "^2") == "2026-04-20T14-00-00-bbbbbb"


def test_resolve_short_suffix(tmp_path):
    _seed_spine(tmp_path, [
        "2026-04-20T10-00-00-aaaaaa",
        "2026-04-20T14-00-00-bbbbbb",
    ])
    assert resolve_run_id(tmp_path, "bbbbbb").endswith("bbbbbb")


def test_resolve_prefix(tmp_path):
    _seed_spine(tmp_path, [
        "2026-04-20T10-00-00-aaaaaa",
        "2026-04-20T14-00-00-bbbbbb",
    ])
    assert resolve_run_id(tmp_path, "2026-04-20T14").endswith("bbbbbb")


def test_resolve_ambiguous_prefix_raises(tmp_path):
    _seed_spine(tmp_path, [
        "2026-04-20T10-00-00-aaaaaa",
        "2026-04-20T10-00-00-aaaaab",
    ])
    with pytest.raises(RunIdAmbiguous):
        resolve_run_id(tmp_path, "2026-04-20T10-00-00-aaaa")


def test_resolve_not_found(tmp_path):
    _seed_spine(tmp_path, ["2026-04-20T10-00-00-aaaaaa"])
    with pytest.raises(RunIdNotFound):
        resolve_run_id(tmp_path, "zzzzzz")


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
