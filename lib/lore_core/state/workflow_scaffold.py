"""Workflow-scaffold record: which repos have had `lore attach`'s scaffold
step run against them.

Sidecar JSON at ``$LORE_ROOT/.lore/workflow_scaffold.json``, keyed by
resolved repo path. Purely an observability record — the scaffold's own
idempotency comes from filesystem checks (`lore_workflow.scaffold`: file
existence, the CLAUDE.md shim sentinel), never from this file. Deleting it
loses no correctness, only the "when did we last scaffold this repo" answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from lore_core.io import atomic_write_text


@dataclass
class ScaffoldRecord:
    path: Path
    scaffolded_at: datetime


class WorkflowScaffoldFile:
    """Sidecar at ``<lore_root>/.lore/workflow_scaffold.json``."""

    def __init__(self, lore_root: Path) -> None:
        self._path = lore_root / ".lore" / "workflow_scaffold.json"
        self._records: dict[Path, ScaffoldRecord] = {}
        self._loaded = False

    def load(self) -> None:
        self._records = {}
        self._loaded = True
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        for entry in raw.get("scaffolded", []):
            path = Path(entry["path"])
            self._records[path] = ScaffoldRecord(
                path=path,
                scaffolded_at=datetime.fromisoformat(entry["scaffolded_at"]),
            )

    def save(self) -> None:
        raw = {
            "scaffolded": [
                {"path": str(r.path), "scaffolded_at": r.scaffolded_at.isoformat()}
                for r in self._records.values()
            ]
        }
        atomic_write_text(self._path, json.dumps(raw, indent=2))

    def record(self, path: Path) -> None:
        """Record *path* as scaffolded now. Re-recording overwrites the timestamp."""
        resolved = path.resolve()
        self._records[resolved] = ScaffoldRecord(path=resolved, scaffolded_at=datetime.now(UTC))

    def was_scaffolded(self, path: Path) -> bool:
        self._ensure_loaded()
        return path.resolve() in self._records

    def all_paths(self) -> list[Path]:
        self._ensure_loaded()
        return list(self._records.keys())

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()
