"""Tests for the cap-trip Part-N split orchestrator."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lore_core.types import Turn
from lore_core.wiki_config import WikiConfig
from lore_curator.buffer_append import append_chunk
from lore_curator.session_curator import _resolve_active_part


def _make_turns(n: int) -> list[Turn]:
    return [
        Turn(index=i, timestamp=None, role="user" if i % 2 == 0 else "assistant", text="x" * 30)
        for i in range(n)
    ]


@pytest.fixture
def lore_root(tmp_path: Path) -> Path:
    (tmp_path / ".lore" / "buffers").mkdir(parents=True)
    return tmp_path


@pytest.fixture(autouse=True)
def patch_collectors(monkeypatch):
    monkeypatch.setattr("lore_curator.session_activity.collect_commits_by_sha", lambda *a, **kw: [])
    monkeypatch.setattr("lore_curator.session_activity.collect_issues_in_window", lambda *a, **kw: ([], []))
    monkeypatch.setattr("lore_curator.session_activity.collect_projects_for_session", lambda **kw: [])
    monkeypatch.setattr("lore_core.git.git_repo_root", lambda cwd: None)
    monkeypatch.setattr("lore_core.git.current_repo", lambda cwd: "")


def _make_cfg(*, cap_turns: int) -> WikiConfig:
    cfg = WikiConfig()
    cfg.curator.synthesis_buffer_cap_turns = cap_turns
    return cfg


def test_resolve_active_part_fresh_pair_returns_one(lore_root):
    part_index, prev = _resolve_active_part(
        lore_root, transcript_id="abc", local_date="2026-05-01",
    )
    assert part_index == 1
    assert prev is None


def test_resolve_active_part_during_accumulating(lore_root):
    append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(2), local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=WikiConfig(),
    )
    part_index, prev = _resolve_active_part(
        lore_root, transcript_id="abc", local_date="2026-05-01",
    )
    assert part_index == 1
    assert prev is None


def test_resolve_active_part_after_cap_trip_returns_part2(lore_root):
    # cap=2 -> first heartbeat trips immediately.
    cfg = _make_cfg(cap_turns=2)
    o1 = append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(2), local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=cfg,
    )
    assert o1.cap_tripped is True
    sidecar = o1.buffer.read_sidecar()
    assert sidecar.state == "ready"

    part_index, prev = _resolve_active_part(
        lore_root, transcript_id="abc", local_date="2026-05-01",
    )
    assert part_index == 2
    assert prev == o1.buffer.stem


def test_part2_carries_continuation_of(lore_root):
    cfg = _make_cfg(cap_turns=2)
    o1 = append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(2), local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=cfg,
    )
    o2 = append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(2), local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=cfg,
        part_index=2, continuation_of=o1.buffer.stem,
    )
    sidecar2 = o2.buffer.read_sidecar()
    assert sidecar2.part_index == 2
    assert sidecar2.continuation_of == o1.buffer.stem
    assert o2.buffer.stem.endswith("__part2")
    # Distinct buffer files.
    assert o1.buffer.stem != o2.buffer.stem


def test_resolve_after_part2_accumulating_returns_part2_active(lore_root):
    cfg = _make_cfg(cap_turns=2)
    o1 = append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(2), local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=cfg,
    )
    # Open Part-2 manually (the orchestrator does this on next heartbeat).
    append_chunk(
        lore_root=lore_root, chunk_turns=_make_turns(1), local_date="2026-05-01",
        transcript_id="abc", integration="claude-code", wiki="private", scope="proj:x",
        cwd=lore_root, wiki_root=lore_root / "wiki" / "private", cfg=WikiConfig(),
        part_index=2, continuation_of=o1.buffer.stem,
    )
    part_index, prev = _resolve_active_part(
        lore_root, transcript_id="abc", local_date="2026-05-01",
    )
    # Part 1 is ready, Part 2 is accumulating -> resolver picks Part 2.
    assert part_index == 2
    assert prev is None
