"""`tools/vale_docs.py` — the CI step that runs Vale over `docs/**/*.md` (PRD 0015).

The step fails on error-level alerts only. Docs written before the step
existed hold long sentences, so a baseline file records the error count per
file. A file fails when its count rises above its baseline. A file without a
baseline entry fails on its first error. The count can only go down.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "vale_docs.py"
BASELINE = REPO_ROOT / "tools" / "vale-docs-baseline.json"


def _load():
    spec = importlib.util.spec_from_file_location("vale_docs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["vale_docs"] = module
    spec.loader.exec_module(module)
    return module


def _alert(line: int, level: str = "error") -> dict:
    return {"Line": line, "Severity": level, "Check": "WritingRules.SentenceLength", "Message": "m"}


def test_a_file_at_its_baseline_passes() -> None:
    mod = _load()
    alerts = {"docs/a.md": [_alert(3), _alert(9)]}
    assert mod.over_baseline(alerts, {"docs/a.md": 2}) == {}


def test_a_file_above_its_baseline_fails_with_its_alerts() -> None:
    mod = _load()
    alerts = {"docs/a.md": [_alert(3), _alert(9)]}
    assert mod.over_baseline(alerts, {"docs/a.md": 1}) == {"docs/a.md": alerts["docs/a.md"]}


def test_a_new_file_fails_on_its_first_error() -> None:
    mod = _load()
    alerts = {"docs/new.md": [_alert(1)]}
    assert mod.over_baseline(alerts, {}) == {"docs/new.md": alerts["docs/new.md"]}


def test_warnings_never_count() -> None:
    mod = _load()
    alerts = {"docs/new.md": [_alert(1, "warning"), _alert(2, "suggestion")]}
    assert mod.over_baseline(alerts, {}) == {}


def test_the_baseline_holds_counts_per_docs_file() -> None:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert baseline, "an empty baseline means the tree is clean; delete the file instead"
    for path, count in baseline.items():
        assert path.startswith("docs/") and path.endswith(".md"), path
        assert (REPO_ROOT / path).is_file(), f"{path} is gone; drop it from the baseline"
        assert isinstance(count, int) and count > 0, (path, count)


@pytest.mark.skipif(shutil.which("vale") is None, reason="vale not on PATH")
def test_the_current_docs_pass_the_step(tmp_path: Path) -> None:
    """The CI step is green on the tree it ships with."""
    config = subprocess.run(
        [sys.executable, "-m", "lore_cli", "style", "vale-config"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "LORE_CACHE": str(tmp_path), "PYTHONPATH": str(REPO_ROOT / "lib")},
    ).stdout.strip()
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--config", config],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
