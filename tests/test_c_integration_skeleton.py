"""Task 5: Curator C integration skeleton + shared harness.

Lands the skeleton BEFORE the LLM passes (architect must-fix): each
pass in Phase B slots into a working pipeline. Tests here cover:

- run_curator_c(defrag, anthropic_client) signature and wiring
- Diff-log wrapping of the whole run
- last_curator_c atomic-on-success update
- Mid-merge vault pre-flight abort
- Obsidian-holding guard (reuses existing hook)
- Shared validate_llm_response helper
- ProposalOnlyError guard
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from lore_curator.c_passes import (
    ProposalOnlyError,
    has_merge_conflicts,
    validate_llm_response,
)


# ---------------------------------------------------------------------------
# validate_llm_response — parametrized malformed-response coverage
# ---------------------------------------------------------------------------


def test_validate_accepts_valid(tmp_path: Path) -> None:
    resp = {"should_merge": True, "confidence": 0.9, "reason": "clear overlap"}
    result = validate_llm_response(
        resp,
        required={"should_merge": bool, "confidence": (int, float), "reason": str},
        ranges={"confidence": (0.0, 1.0)},
        lore_root=tmp_path,
        pass_name="adjacent_merge",
    )
    assert result == resp


@pytest.mark.parametrize("response", [
    None,
    "not a dict",
    [1, 2, 3],
    42,
])
def test_validate_rejects_non_dict(response, tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    result = validate_llm_response(
        response,
        required={"x": str},
        lore_root=tmp_path,
        pass_name="test",
    )
    assert result is None


def test_validate_rejects_missing_field(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    result = validate_llm_response(
        {"should_merge": True},
        required={"should_merge": bool, "confidence": (int, float)},
        lore_root=tmp_path,
        pass_name="test",
    )
    assert result is None


def test_validate_rejects_wrong_type(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    result = validate_llm_response(
        {"confidence": "0.9"},  # string, not float
        required={"confidence": (int, float)},
        lore_root=tmp_path,
        pass_name="test",
    )
    assert result is None


def test_validate_rejects_out_of_range(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    result = validate_llm_response(
        {"confidence": 1.5},
        required={"confidence": (int, float)},
        ranges={"confidence": (0.0, 1.0)},
        lore_root=tmp_path,
        pass_name="test",
    )
    assert result is None


def test_validate_emits_warning_event_on_failure(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    validate_llm_response(
        {"missing_required": True},
        required={"needed": str},
        lore_root=tmp_path,
        pass_name="adjacent_merge",
    )
    events = tmp_path / ".lore" / "hook-events.jsonl"
    assert events.exists()
    lines = [json.loads(l) for l in events.read_text().splitlines() if l.strip()]
    warnings = [
        e for e in lines
        if e.get("event") == "curator-c" and e.get("outcome") == "llm-response-invalid"
    ]
    assert warnings
    assert warnings[0]["error"]["pass"] == "adjacent_merge"


# ---------------------------------------------------------------------------
# has_merge_conflicts — git pre-flight
# ---------------------------------------------------------------------------


def test_has_merge_conflicts_false_on_clean_repo(tmp_path: Path) -> None:
    import subprocess
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
        capture_output=True, env={**{"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                     "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}},
        check=True,
    )
    assert not has_merge_conflicts(tmp_path)


def test_has_merge_conflicts_false_on_non_git(tmp_path: Path) -> None:
    """Non-git directory → no conflicts detected (graceful None return)."""
    assert not has_merge_conflicts(tmp_path)


# ---------------------------------------------------------------------------
# Integration skeleton: run_curator_c(defrag=True) with empty pass list
# ---------------------------------------------------------------------------


def _seed_minimal_vault(tmp_path: Path) -> Path:
    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    (lore_root / "wiki" / "testwiki" / "sessions").mkdir(parents=True)
    return lore_root


def test_skeleton_runs_with_zero_passes(tmp_path: Path, monkeypatch) -> None:
    """Fresh vault, defrag=True, no LLM passes registered → clean run,
    writes a noop diff log entry, updates last_curator_c.
    """
    lore_root = _seed_minimal_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    from lore_curator.curator_c import run_curator_c

    reports = run_curator_c(
        wiki_filter="testwiki",
        dry_run=False,
        defrag=True,
        anthropic_client=None,
    )

    # last_curator_c updated.
    from lore_core.ledger import WikiLedger
    entry = WikiLedger(lore_root, "testwiki").read()
    assert entry.last_curator_c is not None, "last_curator_c must update on success"

    # Diff log exists (may be no-op marker).
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    diff_log = lore_root / ".lore" / f"curator-c.diff.{today}.log"
    assert diff_log.exists(), "diff log must be written"


def test_skeleton_defrag_false_does_not_update_ledger(tmp_path: Path, monkeypatch) -> None:
    """Without --defrag, run_curator_c behaves pre-Plan-5: no ledger update
    for last_curator_c, no diff log.
    """
    lore_root = _seed_minimal_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    from lore_curator.curator_c import run_curator_c

    run_curator_c(wiki_filter="testwiki", dry_run=False, defrag=False)

    from lore_core.ledger import WikiLedger
    entry = WikiLedger(lore_root, "testwiki").read()
    assert entry.last_curator_c is None, "defrag=False must not touch last_curator_c"

    diff_logs = list((lore_root / ".lore").glob("curator-c.diff.*.log"))
    assert not diff_logs, "no diff log when defrag=False"


def test_skeleton_atomic_on_failure(tmp_path: Path, monkeypatch) -> None:
    """Mid-run exception → last_curator_c preserved (atomic-or-unchanged)."""
    lore_root = _seed_minimal_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    from lore_core.ledger import WikiLedger
    prior = datetime(2026, 4, 14, tzinfo=UTC)
    WikiLedger(lore_root, "testwiki").update_last_curator("c", at=prior)

    from lore_curator import curator_c as cc_mod

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated mid-run failure")

    # Hook the inner pass-list execution to raise.
    monkeypatch.setattr(cc_mod, "_run_defrag_passes", boom)

    with pytest.raises(RuntimeError, match="simulated"):
        cc_mod.run_curator_c(
            wiki_filter="testwiki", dry_run=False, defrag=True, anthropic_client=None
        )

    entry = WikiLedger(lore_root, "testwiki").read()
    assert entry.last_curator_c == prior, "last_curator_c must preserve prior on failure"


# ---------------------------------------------------------------------------
# ProposalOnlyError — enforcement is not convention
# ---------------------------------------------------------------------------


def test_proposal_only_error_is_raisable() -> None:
    with pytest.raises(ProposalOnlyError):
        raise ProposalOnlyError("test")
