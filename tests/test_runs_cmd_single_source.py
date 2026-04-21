"""Task 12c: lore runs list uses the shared iter_archival_runs helper.

Architect decision (2026-04-20 plan review): runs list --hooks stays a
row-per-event history view and does NOT render CaptureState — that's
the `lore status` summary niche. This test guards the boundary.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_runs_cmd_has_no_direct_archival_globs() -> None:
    """runs_cmd.py must not re-glob .lore/runs/*.jsonl directly.

    After Task 8, all enumeration goes through iter_archival_runs /
    list_archival_runs. Direct globs in this file would re-introduce
    the duplication Task 8 eliminated.
    """
    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [
            "grep", "-nE",
            r"\.lore/runs|runs_dir\.glob|runs\.glob",
            str(repo / "lib" / "lore_cli" / "runs_cmd.py"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    # Allow mentions of ".lore/runs" in comments / docstrings; ban
    # call-site globs.
    offenders = [
        line
        for line in result.stdout.splitlines()
        if "glob" in line.lower()
    ]
    assert not offenders, (
        f"runs_cmd.py should use list_archival_runs / iter_archival_runs, "
        f"not inline globs; found:\n{chr(10).join(offenders)}"
    )


def test_runs_cmd_does_not_import_capture_state() -> None:
    """runs list --hooks is a history view, not a CaptureState summary.

    If this test starts failing, reconsider whether a new signal
    belongs in CaptureState (pin it in the architect decision doc)
    before adding it to runs_cmd.
    """
    repo = Path(__file__).resolve().parent.parent
    src = (repo / "lib" / "lore_cli" / "runs_cmd.py").read_text()
    assert "capture_state" not in src, (
        "runs_cmd must stay a history view; do not import CaptureState."
    )
