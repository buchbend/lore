"""CI workflow shape — issue #163.

Keeps the workflow file honest without re-implementing a YAML linter:
asserts the three required gates (pytest, ruff check, ruff format
--check) are present and wired to run on push and pull_request.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW_PATH = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text())


def test_workflow_file_exists():
    assert WORKFLOW_PATH.is_file(), f"missing {WORKFLOW_PATH}"


def test_workflow_runs_on_push_and_pull_request():
    workflow = _load_workflow()
    # YAML parses the bare `on:` key as boolean True, not string "on".
    triggers = workflow.get(True, workflow.get("on", {}))
    assert "push" in triggers
    assert "pull_request" in triggers


def test_workflow_runs_pytest_ruff_check_and_ruff_format():
    workflow = _load_workflow()
    steps = [step for job in workflow["jobs"].values() for step in job["steps"] if "run" in step]
    run_commands = " ".join(step["run"] for step in steps)
    assert "pytest" in run_commands
    assert "ruff check" in run_commands
    assert "ruff format --check" in run_commands


def _steps() -> list[dict]:
    workflow = _load_workflow()
    return [step for job in workflow["jobs"].values() for step in job["steps"]]


def test_workflow_runs_the_adr_lint_after_pytest_and_blocks_on_it():
    """ADR 0015: `lore lint adr` gates every PR. A failing step fails the job."""
    runs = [step.get("run", "") for step in _steps()]
    pytest_at = next(i for i, run in enumerate(runs) if run.startswith("pytest"))
    lint_at = next(i for i, run in enumerate(runs) if "lore lint adr" in run)
    assert lint_at > pytest_at
    assert not _steps()[lint_at].get("continue-on-error", False)


def test_workflow_runs_vale_over_the_docs_with_a_pinned_binary_and_blocks_on_it():
    """PRD 0015: CI runs Vale with the Lore config over `docs/**/*.md`."""
    steps = _steps()
    runs = [step.get("run", "") for step in steps]
    install = next(i for i, run in enumerate(runs) if "errata-ai/vale/releases" in run)
    assert "VALE_VERSION" in str(steps[install].get("env", {})) or "v3." in runs[install]
    lint = next(i for i, run in enumerate(runs) if "tools/vale_docs.py" in run)
    assert lint > install
    assert "lore style vale-config" in runs[lint]
    assert not steps[lint].get("continue-on-error", False)
