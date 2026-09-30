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


def test_merge_baseline_lowers_counts_and_keeps_files_not_linted() -> None:
    mod = _load()
    old = {"docs/a.md": 3, "docs/b.md": 2, "other/c.md": 5}
    current = {"docs/a.md": 1, "docs/new.md": 4}
    merged, raised = mod.merge_baseline(old, current, ["docs"])
    # a went down, b is clean now and drops out, c was not linted, new is recorded.
    assert merged == {"docs/a.md": 1, "docs/new.md": 4, "other/c.md": 5}
    assert raised == []


def test_merge_baseline_never_raises_a_count() -> None:
    mod = _load()
    merged, raised = mod.merge_baseline({"docs/a.md": 1}, {"docs/a.md": 3}, ["docs/a.md"])
    assert merged == {"docs/a.md": 1}
    assert raised == ["docs/a.md"]


def _write_baseline_run(mod, monkeypatch, tmp_path, old, alerts, paths):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(old), encoding="utf-8")
    monkeypatch.setattr(mod, "run_vale", lambda config, p: alerts)
    code = mod.main(["--config", "x.ini", "--baseline", str(baseline), "--write-baseline", *paths])
    return code, json.loads(baseline.read_text(encoding="utf-8"))


def test_write_baseline_on_a_subset_keeps_the_other_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load()
    old = {"docs/a.md": 3, "docs/b.md": 2}
    code, written = _write_baseline_run(
        mod, monkeypatch, tmp_path, old, {"docs/a.md": [_alert(1)]}, ["docs/a.md"]
    )
    assert code == 0
    assert written == {"docs/a.md": 1, "docs/b.md": 2}


def test_write_baseline_refuses_to_raise_a_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _load()
    old = {"docs/a.md": 1, "docs/b.md": 2}
    alerts = {"docs/a.md": [_alert(1), _alert(2), _alert(3)], "docs/b.md": [_alert(1)]}
    code, written = _write_baseline_run(mod, monkeypatch, tmp_path, old, alerts, ["docs"])
    assert code == 1
    assert written == {"docs/a.md": 1, "docs/b.md": 1}
    assert "docs/a.md" in capsys.readouterr().out


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
