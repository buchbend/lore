import json
from pathlib import Path

from lore_search.query_log import QueryLogger


def test_emit_query_record(tmp_path: Path):
    logger = QueryLogger(tmp_path)
    logger.emit(
        query="curator briefing",
        sanitized_and='"curator" "briefing"',
        sanitized_or='"curator" OR "briefing"',
        wiki="private",
        for_repo=None,
        k=5,
        and_hits=2,
        or_hits=2,
        mode_final="and",
        results=[
            {"path": "projects/curator-briefing.md", "score": 12.34},
            {"path": "decisions/briefing-cadence.md", "score": 9.10},
        ],
    )
    path = tmp_path / "query-log.jsonl"
    assert path.exists()
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["schema_version"] == 1
    assert record["query"] == "curator briefing"
    assert record["mode_final"] == "and"
    assert record["and_hits"] == 2
    assert record["or_hits"] == 2
    assert len(record["results"]) == 2
    assert "ts" in record


def test_emit_event_record(tmp_path: Path):
    """Non-query events (e.g. reindex_skip) share the sink."""
    logger = QueryLogger(tmp_path)
    logger.emit(event="reindex_skip", wiki="private", reason="throttle")
    path = tmp_path / "query-log.jsonl"
    record = json.loads(path.read_text().splitlines()[-1])
    assert record["schema_version"] == 1
    assert record["event"] == "reindex_skip"
    assert record["reason"] == "throttle"


def test_rotation_crosses_threshold(tmp_path: Path):
    logger = QueryLogger(tmp_path, max_size_mb=1)
    path = tmp_path / "query-log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * 1_100_000 + "\n")
    logger.emit(query="post-rotate", k=5, and_hits=0, or_hits=0, mode_final="or", results=[])
    rotated = tmp_path / "query-log.jsonl.1"
    assert rotated.exists()
    assert path.exists()
    assert path.stat().st_size < 2000


def test_write_failure_touches_marker(tmp_path: Path, monkeypatch):
    import os as _os
    logger = QueryLogger(tmp_path)
    real_open = _os.open

    def faulty_open(path, *args, **kwargs):
        if str(path).endswith("query-log.jsonl"):
            raise OSError("disk full")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(_os, "open", faulty_open)
    logger.emit(query="x", k=5, and_hits=0, or_hits=0, mode_final="or", results=[])
    marker = tmp_path / "query-log-failed.marker"
    assert marker.exists()


def test_get_logger_resolves_cache_dir(tmp_path: Path, monkeypatch):
    """get_logger() honours $LORE_CACHE."""
    monkeypatch.setenv("LORE_CACHE", str(tmp_path))
    from lore_search.query_log import get_logger
    logger = get_logger()
    assert logger.path == tmp_path / "query-log.jsonl"
