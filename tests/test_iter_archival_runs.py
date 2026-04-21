"""Task 8a: iter_archival_runs — one helper replacing six call-site copies."""

from __future__ import annotations

from pathlib import Path

from lore_core.run_reader import iter_archival_runs


def _seed_runs(tmp_path: Path, names: list[str]) -> Path:
    runs_dir = tmp_path / ".lore" / "runs"
    runs_dir.mkdir(parents=True)
    for name in names:
        (runs_dir / name).write_text('{"type":"run-start"}\n')
    return tmp_path


def test_iter_archival_runs_empty_directory_yields_nothing(tmp_path: Path) -> None:
    assert list(iter_archival_runs(tmp_path)) == []


def test_iter_archival_runs_missing_directory_yields_nothing(tmp_path: Path) -> None:
    # No .lore/ directory at all.
    assert list(iter_archival_runs(tmp_path)) == []


def test_iter_archival_runs_skips_trace_companions(tmp_path: Path) -> None:
    lore_root = _seed_runs(
        tmp_path,
        [
            "2026-04-20T10-00-00-aaaaaa.jsonl",
            "2026-04-20T10-00-00-aaaaaa.trace.jsonl",
            "2026-04-20T11-00-00-bbbbbb.jsonl",
            "2026-04-20T11-00-00-bbbbbb.trace.jsonl",
        ],
    )
    paths = list(iter_archival_runs(lore_root))
    assert len(paths) == 2, f"expected 2 archival files, trace excluded; got {paths}"
    assert all(not p.name.endswith(".trace.jsonl") for p in paths)


def test_iter_archival_runs_newest_first(tmp_path: Path) -> None:
    lore_root = _seed_runs(
        tmp_path,
        [
            "2026-04-20T10-00-00-aaaaaa.jsonl",
            "2026-04-20T11-00-00-bbbbbb.jsonl",
            "2026-04-20T12-00-00-cccccc.jsonl",
        ],
    )
    paths = list(iter_archival_runs(lore_root))
    assert [p.stem for p in paths] == [
        "2026-04-20T12-00-00-cccccc",
        "2026-04-20T11-00-00-bbbbbb",
        "2026-04-20T10-00-00-aaaaaa",
    ]


def test_iter_archival_runs_honors_limit(tmp_path: Path) -> None:
    lore_root = _seed_runs(
        tmp_path,
        [f"2026-04-20T10-{i:02d}-00-aaaaaa.jsonl" for i in range(10)],
    )
    paths = list(iter_archival_runs(lore_root, limit=3))
    assert len(paths) == 3
    # Must be the 3 newest.
    assert paths[0].stem == "2026-04-20T10-09-00-aaaaaa"
    assert paths[1].stem == "2026-04-20T10-08-00-aaaaaa"
    assert paths[2].stem == "2026-04-20T10-07-00-aaaaaa"


def test_iter_archival_runs_limit_zero_yields_nothing(tmp_path: Path) -> None:
    lore_root = _seed_runs(tmp_path, ["2026-04-20T10-00-00-aaaaaa.jsonl"])
    assert list(iter_archival_runs(lore_root, limit=0)) == []


def test_iter_archival_runs_limit_larger_than_count_yields_all(tmp_path: Path) -> None:
    lore_root = _seed_runs(
        tmp_path,
        ["2026-04-20T10-00-00-aaaaaa.jsonl", "2026-04-20T11-00-00-bbbbbb.jsonl"],
    )
    assert len(list(iter_archival_runs(lore_root, limit=100))) == 2


def test_iter_archival_runs_stable_order_on_same_mtime(tmp_path: Path) -> None:
    """Ties broken by the suffix portion of the ID; deterministic ordering."""
    lore_root = _seed_runs(
        tmp_path,
        [
            "2026-04-20T10-00-00-bbbbbb.jsonl",
            "2026-04-20T10-00-00-aaaaaa.jsonl",
            "2026-04-20T10-00-00-cccccc.jsonl",
        ],
    )
    paths = list(iter_archival_runs(lore_root))
    # Lexicographic reverse: cccccc, bbbbbb, aaaaaa
    assert [p.stem.split("-")[-1] for p in paths] == ["cccccc", "bbbbbb", "aaaaaa"]


def test_iter_archival_runs_yields_zero_byte_files(tmp_path: Path) -> None:
    runs_dir = tmp_path / ".lore" / "runs"
    runs_dir.mkdir(parents=True)
    (runs_dir / "2026-04-20T10-00-00-aaaaaa.jsonl").write_bytes(b"")
    (runs_dir / "2026-04-20T11-00-00-bbbbbb.jsonl").write_text('{"type":"run-start"}\n')

    paths = list(iter_archival_runs(tmp_path))
    # Helper is a pure enumerator; zero-byte files are a caller concern.
    assert len(paths) == 2
