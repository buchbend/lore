# Auto Session Writer Diagnostics — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give users an inspectable view of the passive-capture pipeline. Today the pipeline runs in a hook hot-path and a detached background process, both effectively invisible. This plan adds structured logs on disk plus `lore runs`, an extended `lore doctor`, and richer banner surfacing — so users can answer *did it run, what did it do, and if nothing happened, why.*

**Architecture:** Two append-only JSONL streams (`hook-events.jsonl` for plumbing, `runs/<id>.jsonl` + `runs-live.jsonl` for curator decisions), a read-only command layer (`lore runs {list, show, tail}`), an extended `lore doctor` Capture-pipeline panel, and three new SessionStart banner signals. Writers are best-effort and never break capture; readers are tolerant of malformed records. Retention is lazy (cleanup at `run-end`) with configurable caps at `$LORE_ROOT/.lore/config.yml`.

**Tech Stack:** Python 3.11+, typer + rich (existing), pyyaml (existing), pytest (existing). No new dependencies.

**Spec reference:** `docs/superpowers/specs/2026-04-20-auto-session-diagnostics-design.md`

**Non-blocker items deferred from this plan** (sketched in the spec; ship in follow-up plans):
1. **Shell completion script** (`lore completions {bash,zsh,fish}`) — spec mentions; not critical for Phase 1 debugging value.
2. **SessionEnd breadcrumb** — spec sketches it; Task 13's all-skips SessionStart hint already closes the critical Scenario-A gap. Revisit once the SessionEnd hook output channel is verified.
3. **Live TUI dashboard** (`lore watch`) — `lore runs tail` + `runs-live.jsonl` is the Phase 1 live-observation path; TUI is Pillar 3 polish.

**Phases:**
- **A. Foundations** (Tasks 1–4): RootConfig, HookEventLogger, RunLogger writer, ID/suffix utilities.
- **B. Pipeline wiring** (Tasks 5–7): wrap `capture()`, thread RunLogger through Curator A, add `--trace-llm`.
- **C. Reader layer** (Tasks 8–12): JSONL reader with truncation detection, renderer (icons/ASCII/responsive), `lore runs show|list|tail`.
- **D. Discovery** (Tasks 13–15): extended breadcrumb, doctor panel, retention cleanup.
- **E. Integration + docs** (Tasks 16–17): end-to-end tests, README Observability section.

Each task is independently committable. Run `pytest -q` after every commit.

---

## File structure

**New files:**
- `lib/lore_core/root_config.py` — `RootConfig` + `ObservabilityConfig` dataclasses, YAML loader for `$LORE_ROOT/.lore/config.yml`
- `lib/lore_core/hook_log.py` — `HookEventLogger` (append, rotation with flock, sentinel marker)
- `lib/lore_core/run_log.py` — `RunLogger` context manager (archival + live-tee), record-type constants, ID generator
- `lib/lore_core/run_reader.py` — parse JSONL, resolve IDs (`latest`, `^N`, suffix, prefix), detect truncation
- `lib/lore_cli/runs_cmd.py` — `lore runs list|show|tail` typer app
- `lib/lore_cli/run_render.py` — pure renderer (flat log + summary panel), icon table, TTY/NO_COLOR/ASCII detection

**Modified files:**
- `lib/lore_cli/hooks.py` — wrap `capture()` body in try/except, call `HookEventLogger`
- `lib/lore_curator/curator_a.py` — construct `RunLogger` at entry, thread through to session_filer / noteworthy / merge-check (add `logger` kwarg to `_process_entry` and below)
- `lib/lore_curator/core.py` — add `--trace-llm` option to `lore curator run`
- `lib/lore_curator/noteworthy.py` — accept optional `logger` kwarg; emit `noteworthy` and (when tracing) `llm-prompt` / `llm-response` records
- `lib/lore_curator/session_filer.py` — accept optional `logger` kwarg; emit `session-note`, `merge-check`, `skip` records
- `lib/lore_cli/doctor_cmd.py` — append Capture-pipeline panel
- `lib/lore_cli/breadcrumb.py` — add all-skips hint, last-run-error prefix, hook-error trailing segment
- `lib/lore_cli/__main__.py` — register `runs_cmd.app` under name `runs`

---

## Phase A — Foundations

### Task 1: Root-level observability config

**Files:**
- Create: `lib/lore_core/root_config.py`
- Test: `tests/test_root_config.py`

**Goal:** Load `$LORE_ROOT/.lore/config.yml` with an `observability:` block. Global (not per-wiki) because the log streams live at `$LORE_ROOT/.lore/` and are shared across wikis. Missing file / missing section / unknown keys all fall back to safe defaults with a warning — never crash.

- [ ] **Step 1: Write the dataclass + defaults test**

```python
# tests/test_root_config.py
from pathlib import Path
from lore_core.root_config import RootConfig, ObservabilityConfig, load_root_config


def test_defaults_when_file_absent(tmp_path: Path):
    cfg = load_root_config(tmp_path)
    assert cfg.observability.hook_events.max_size_mb == 10
    assert cfg.observability.hook_events.keep_rotations == 1
    assert cfg.observability.runs.keep == 200
    assert cfg.observability.runs.max_total_mb == 100
    assert cfg.observability.runs.keep_trace == 30
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `pytest tests/test_root_config.py::test_defaults_when_file_absent -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lore_core.root_config'`

- [ ] **Step 3: Implement the module**

```python
# lib/lore_core/root_config.py
"""Root-level Lore config at $LORE_ROOT/.lore/config.yml.

Observability settings are global (not per-wiki) because the log
streams they govern live at $LORE_ROOT/.lore/ and are shared across
wikis.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class HookEventsConfig:
    max_size_mb: int = 10
    keep_rotations: int = 1


@dataclass
class RunsConfig:
    keep: int = 200
    max_total_mb: int = 100
    keep_trace: int = 30


@dataclass
class ObservabilityConfig:
    hook_events: HookEventsConfig = field(default_factory=HookEventsConfig)
    runs: RunsConfig = field(default_factory=RunsConfig)


@dataclass
class RootConfig:
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)


def _merge(target: Any, raw: dict[str, Any], path: str) -> None:
    """Merge raw into target dataclass in place; warn on unknown keys."""
    valid = {f.name for f in fields(target)}
    for key, value in raw.items():
        if key not in valid:
            warnings.warn(f"root_config: unknown key {path}.{key!r}", stacklevel=3)
            continue
        current = getattr(target, key)
        if is_dataclass(current) and isinstance(value, dict):
            _merge(current, value, f"{path}.{key}")
        else:
            setattr(target, key, value)


def load_root_config(lore_root: Path) -> RootConfig:
    """Load $LORE_ROOT/.lore/config.yml over defaults.

    Missing file / missing section / unknown keys → defaults + warning.
    Malformed YAML → defaults + warning (no crash).
    """
    cfg = RootConfig()
    path = lore_root / ".lore" / "config.yml"
    if not path.exists():
        return cfg
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        warnings.warn(f"root_config: malformed YAML at {path}: {e}", stacklevel=2)
        return cfg
    if not isinstance(raw, dict):
        warnings.warn(f"root_config: top-level must be a mapping at {path}", stacklevel=2)
        return cfg
    _merge(cfg, raw, "")
    return cfg
```

- [ ] **Step 4: Run test to confirm it passes**

Run: `pytest tests/test_root_config.py::test_defaults_when_file_absent -v`
Expected: PASS

- [ ] **Step 5: Add partial-override test**

```python
# Append to tests/test_root_config.py
def test_partial_override(tmp_path: Path):
    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir()
    (lore_dir / "config.yml").write_text(
        "observability:\n"
        "  runs:\n"
        "    keep: 50\n"
    )
    cfg = load_root_config(tmp_path)
    assert cfg.observability.runs.keep == 50            # overridden
    assert cfg.observability.runs.max_total_mb == 100   # default preserved
    assert cfg.observability.hook_events.max_size_mb == 10  # default preserved
```

- [ ] **Step 6: Run and confirm pass**

Run: `pytest tests/test_root_config.py -v`
Expected: both tests PASS.

- [ ] **Step 7: Add malformed-YAML test**

```python
# Append
def test_malformed_yaml_warns(tmp_path: Path, recwarn):
    lore_dir = tmp_path / ".lore"
    lore_dir.mkdir()
    (lore_dir / "config.yml").write_text("this: is: not: valid\n")
    cfg = load_root_config(tmp_path)
    assert cfg.observability.runs.keep == 200  # defaults
    assert any("malformed YAML" in str(w.message) for w in recwarn)
```

- [ ] **Step 8: Run and confirm all three pass**

Run: `pytest tests/test_root_config.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 9: Commit**

```bash
git add lib/lore_core/root_config.py tests/test_root_config.py
git commit -m "feat(core): add root-level observability config loader"
```

---

### Task 2: HookEventLogger

**Files:**
- Create: `lib/lore_core/hook_log.py`
- Test: `tests/test_hook_log.py`

**Goal:** Append-only JSONL writer for `hook-events.jsonl`. One emit() call per hook invocation. Rotation at size threshold, guarded by a non-blocking flock to avoid two concurrent hooks both renaming the file and losing data. A write failure touches a sentinel marker so `lore doctor` can surface it even when the log itself is unwritable.

- [ ] **Step 1: Write test for happy-path emit**

```python
# tests/test_hook_log.py
import json
from pathlib import Path

from lore_core.hook_log import HookEventLogger


def test_emit_appends_one_line(tmp_path: Path):
    logger = HookEventLogger(tmp_path)
    logger.emit(
        event="session-end",
        host="saiyajin",
        transcript_id="t-abc",
        scope={"wiki": "private", "scope": "lore"},
        duration_ms=47,
        outcome="spawned-curator",
        pending_after=3,
        run_id="2026-04-20T14-32-05-a1b2c3",
    )
    path = tmp_path / ".lore" / "hook-events.jsonl"
    assert path.exists()
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["schema_version"] == 1
    assert record["event"] == "session-end"
    assert record["outcome"] == "spawned-curator"
    assert record["run_id"] == "2026-04-20T14-32-05-a1b2c3"
    assert record["error"] is None
    assert "ts" in record
```

- [ ] **Step 2: Run to confirm fail**

Run: `pytest tests/test_hook_log.py::test_emit_appends_one_line -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# lib/lore_core/hook_log.py
"""Append-only hook-event log at $LORE_ROOT/.lore/hook-events.jsonl.

One record per hook invocation. Hot-path, must not raise. Rotation
is guarded by a non-blocking flock on a sibling lock file — two
concurrent hooks both seeing size > threshold would otherwise race
on rename() and lose records.
"""

from __future__ import annotations

import fcntl
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class HookEventLogger:
    """Single-record appender for hook-events.jsonl.

    I/O-free at construction time — no file is opened until emit().
    """

    def __init__(self, lore_root: Path, *, max_size_mb: int = 10):
        self._dir = lore_root / ".lore"
        self._path = self._dir / "hook-events.jsonl"
        self._rotated = self._dir / "hook-events.jsonl.1"
        self._rotate_lock = self._dir / "hook-events.rotate.lock"
        self._marker = self._dir / "hook-log-failed.marker"
        self._max_size = max_size_mb * 1024 * 1024

    def emit(self, **record: Any) -> None:
        """Append one record. Never raises."""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._maybe_rotate()
            payload = {
                "schema_version": 1,
                "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                **record,
            }
            # Ensure default fields exist.
            payload.setdefault("error", None)
            with self._path.open("a") as f:
                f.write(json.dumps(payload) + "\n")
        except OSError:
            self._touch_marker()

    def _maybe_rotate(self) -> None:
        if not self._path.exists():
            return
        try:
            size = self._path.stat().st_size
        except OSError:
            return
        if size < self._max_size:
            return
        # Non-blocking flock — loser skips rotation this cycle.
        self._rotate_lock.touch(exist_ok=True)
        try:
            with self._rotate_lock.open("r") as lock_f:
                try:
                    fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    return  # another process is rotating; skip
                # Re-check under lock (size may have been rotated already).
                try:
                    size_after_lock = self._path.stat().st_size
                except OSError:
                    return
                if size_after_lock < self._max_size:
                    return
                os.replace(self._path, self._rotated)
        except OSError:
            pass

    def _touch_marker(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._marker.touch(exist_ok=True)
            os.utime(self._marker, None)  # refresh mtime
        except OSError:
            pass
```

- [ ] **Step 4: Run test to confirm pass**

Run: `pytest tests/test_hook_log.py::test_emit_appends_one_line -v`
Expected: PASS.

- [ ] **Step 5: Add rotation test**

```python
# Append to tests/test_hook_log.py
def test_rotation_crosses_threshold(tmp_path: Path):
    logger = HookEventLogger(tmp_path, max_size_mb=1)  # 1 MB threshold
    # Pre-seed with a 1.1 MB existing log.
    path = tmp_path / ".lore" / "hook-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * (1_100_000) + "\n")
    logger.emit(event="session-end", outcome="ledger-advanced")
    rotated = tmp_path / ".lore" / "hook-events.jsonl.1"
    assert rotated.exists(), "old file should have been rotated to .1"
    assert path.exists()
    assert path.stat().st_size < 2000, "fresh file should have only the new record"
```

- [ ] **Step 6: Run and confirm pass**

Run: `pytest tests/test_hook_log.py -v`
Expected: both tests PASS.

- [ ] **Step 7: Add write-failure test (marker touched)**

```python
# Append
def test_write_failure_touches_marker(tmp_path: Path, monkeypatch):
    logger = HookEventLogger(tmp_path)
    # Force open() to raise OSError on the log file.
    real_open = Path.open

    def faulty_open(self, *args, **kwargs):
        if self.name == "hook-events.jsonl":
            raise OSError("disk full")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", faulty_open)
    logger.emit(event="session-end", outcome="ledger-advanced")  # must not raise
    marker = tmp_path / ".lore" / "hook-log-failed.marker"
    assert marker.exists(), "sentinel marker should be touched on write failure"
```

- [ ] **Step 8: Run and confirm pass**

Run: `pytest tests/test_hook_log.py -v`
Expected: 3 tests PASS.

- [ ] **Step 9: Add rotation race test**

```python
# Append
def test_rotation_race_no_data_loss(tmp_path: Path):
    """Two concurrent emits both past threshold should not lose records."""
    import threading

    logger = HookEventLogger(tmp_path, max_size_mb=1)
    path = tmp_path / ".lore" / "hook-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * 1_100_000 + "\n")  # pre-seed over threshold

    errors: list[Exception] = []

    def emit_one(event_name: str):
        try:
            logger.emit(event=event_name, outcome="ledger-advanced")
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=emit_one, args=("session-end",))
    t2 = threading.Thread(target=emit_one, args=("session-start",))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert not errors
    rotated = tmp_path / ".lore" / "hook-events.jsonl.1"
    # Combined content of rotated + fresh file must contain both events.
    all_text = (rotated.read_text() if rotated.exists() else "") + path.read_text()
    assert "session-end" in all_text
    assert "session-start" in all_text
```

- [ ] **Step 10: Run and confirm pass**

Run: `pytest tests/test_hook_log.py -v`
Expected: 4 tests PASS.

- [ ] **Step 11: Commit**

```bash
git add lib/lore_core/hook_log.py tests/test_hook_log.py
git commit -m "feat(core): add HookEventLogger with rotation and sentinel marker"
```

---

### Task 3: Run ID generator

**Files:**
- Create: `lib/lore_core/run_log.py` (initial — will grow in Task 4)
- Test: `tests/test_run_log.py`

**Goal:** Generate run IDs in the form `<ISO-timestamp>-<6-char-random-suffix>`. Six chars of [a-z0-9] gives 36^6 ≈ 2 billion — suffix collisions inside a 200-run retention window are astronomically unlikely, but we still assert path-doesn't-exist in `RunLogger.__init__` as defense in depth.

- [ ] **Step 1: Write test**

```python
# tests/test_run_log.py
import re
from datetime import UTC, datetime

from lore_core.run_log import generate_run_id


def test_run_id_format():
    ts = datetime(2026, 4, 20, 14, 32, 5, tzinfo=UTC)
    run_id = generate_run_id(now=ts)
    # Format: YYYY-MM-DDTHH-MM-SS-<6 lowercase alphanum>
    assert re.fullmatch(r"2026-04-20T14-32-05-[a-z0-9]{6}", run_id), run_id


def test_run_id_uniqueness():
    ids = {generate_run_id() for _ in range(1000)}
    assert len(ids) == 1000, "1000 calls should yield 1000 distinct IDs"
```

- [ ] **Step 2: Confirm fail**

Run: `pytest tests/test_run_log.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement generator**

```python
# lib/lore_core/run_log.py
"""Run-log writer for Curator A invocations.

Two output files per run:
  - runs/<id>.jsonl            archival
  - runs-live.jsonl            tee of active run (truncated at run-start)

Plus an optional LLM-trace companion runs/<id>.trace.jsonl when
LORE_TRACE_LLM=1 or --trace-llm is set.
"""

from __future__ import annotations

import secrets
import string
from datetime import UTC, datetime


_ID_ALPHABET = string.ascii_lowercase + string.digits  # 36 chars


def generate_run_id(*, now: datetime | None = None) -> str:
    """Return `<ISO-timestamp>-<6-char-random-suffix>` for a run.

    Timestamp is formatted filename-safe (hyphens, no colons). Suffix
    is 6 chars from [a-z0-9] — 36^6 ≈ 2 billion, so collisions inside
    the retention window are astronomically unlikely.
    """
    ts = now or datetime.now(UTC)
    stamp = ts.strftime("%Y-%m-%dT%H-%M-%S")
    suffix = "".join(secrets.choice(_ID_ALPHABET) for _ in range(6))
    return f"{stamp}-{suffix}"
```

- [ ] **Step 4: Confirm pass**

Run: `pytest tests/test_run_log.py -v`
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/lore_core/run_log.py tests/test_run_log.py
git commit -m "feat(core): add run ID generator"
```

---

### Task 4: RunLogger context manager

**Files:**
- Modify: `lib/lore_core/run_log.py`
- Modify: `tests/test_run_log.py`

**Goal:** Context-manager writer for `runs/<id>.jsonl` + `runs-live.jsonl` + optional `runs/<id>.trace.jsonl`. Emits records with `schema_version: 1`, stamps `ts`, writes to archival + live-tee on every call, truncates live-tee at run-start, and flushes `run-end` even on exception. All writes are best-effort: OSError during emit increments a counter and is never re-raised. Init raises on path-already-exists (defense against suffix collision).

- [ ] **Step 1: Test that __enter__ creates archival file, truncates live-tee, emits run-start**

```python
# Append to tests/test_run_log.py
import json
from pathlib import Path

from lore_core.run_log import RunLogger


def test_run_start_written_on_enter(tmp_path: Path):
    with RunLogger(tmp_path, trigger="manual", pending_count=2) as logger:
        pass  # just open+close
    archival_files = list((tmp_path / ".lore" / "runs").glob("*.jsonl"))
    archival = [p for p in archival_files if not p.name.endswith(".trace.jsonl")]
    assert len(archival) == 1
    lines = archival[0].read_text().splitlines()
    records = [json.loads(l) for l in lines]
    assert records[0]["type"] == "run-start"
    assert records[0]["trigger"] == "manual"
    assert records[0]["pending_count"] == 2
    assert records[-1]["type"] == "run-end"
    # live-tee also contains the same records plus run_id on each line
    live = tmp_path / ".lore" / "runs-live.jsonl"
    live_records = [json.loads(l) for l in live.read_text().splitlines()]
    assert all("run_id" in r for r in live_records)
    assert live_records[0]["type"] == "run-start"
```

- [ ] **Step 2: Confirm fail**

Run: `pytest tests/test_run_log.py::test_run_start_written_on_enter -v`
Expected: FAIL — `ImportError: cannot import name 'RunLogger'`.

- [ ] **Step 3: Extend the module**

```python
# Append to lib/lore_core/run_log.py
import json
import os
from pathlib import Path
from types import TracebackType
from typing import Any


class RunLogger:
    """Write a Curator A run's decision trace.

    Context-manager usage:

        with RunLogger(lore_root, trigger="hook") as logger:
            logger.emit("transcript-start", transcript_id=..., new_turns=...)
            logger.emit("noteworthy", verdict=True, reason=..., tier=...)
            ...

    Opens `runs/<id>.jsonl` and truncates `runs-live.jsonl` at start;
    emits run-start. On exit (normal or exception) emits run-end with
    duration and counts, then closes files.

    Writes are best-effort: OSError during emit increments
    `_write_failures` and is swallowed.
    """

    RECORD_TYPES = frozenset({
        "run-start", "transcript-start", "redaction", "noteworthy",
        "merge-check", "session-note", "skip", "warning", "error",
        "run-end", "llm-prompt", "llm-response",
    })

    def __init__(
        self,
        lore_root: Path,
        *,
        trigger: str = "hook",
        pending_count: int = 0,
        config_snapshot: dict[str, Any] | None = None,
        dry_run: bool = False,
        trace_llm: bool = False,
        ledger_snapshot_hash: str | None = None,
        run_id: str | None = None,
    ):
        self._lore_root = lore_root
        self._dir = lore_root / ".lore"
        self._runs_dir = self._dir / "runs"
        self._trigger = trigger
        self._pending_count = pending_count
        self._config_snapshot = config_snapshot or {}
        self._dry_run = dry_run
        self._trace_llm = trace_llm
        self._ledger_snapshot_hash = ledger_snapshot_hash
        self.run_id = run_id or generate_run_id()
        self._archival = self._runs_dir / f"{self.run_id}.jsonl"
        self._trace = self._runs_dir / f"{self.run_id}.trace.jsonl"
        self._live = self._dir / "runs-live.jsonl"
        self._write_failures = 0
        self._counts = {"notes_new": 0, "notes_merged": 0, "skipped": 0, "errors": 0}
        self._opened_at: datetime | None = None

    def __enter__(self) -> "RunLogger":
        # Init invariant — suffix collision guard.
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        if self._archival.exists():
            # Regenerate once; raise on second collision.
            self.run_id = generate_run_id()
            self._archival = self._runs_dir / f"{self.run_id}.jsonl"
            self._trace = self._runs_dir / f"{self.run_id}.trace.jsonl"
            if self._archival.exists():
                raise RuntimeError(
                    f"run ID collision after retry: {self.run_id} already exists"
                )
        # Truncate live-tee.
        try:
            self._live.parent.mkdir(parents=True, exist_ok=True)
            self._live.write_text("")
        except OSError:
            self._write_failures += 1
        self._opened_at = datetime.now(UTC)
        self.emit(
            "run-start",
            run_id=self.run_id,
            trigger=self._trigger,
            pending_count=self._pending_count,
            config=self._config_snapshot,
            dry_run=self._dry_run,
            ledger_snapshot_hash=self._ledger_snapshot_hash,
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc is not None:
            self.emit(
                "error",
                exception=type(exc).__name__,
                message=str(exc),
            )
            self._counts["errors"] += 1
        duration_ms = 0
        if self._opened_at is not None:
            duration_ms = int((datetime.now(UTC) - self._opened_at).total_seconds() * 1000)
        self.emit(
            "run-end",
            duration_ms=duration_ms,
            notes_new=self._counts["notes_new"],
            notes_merged=self._counts["notes_merged"],
            skipped=self._counts["skipped"],
            errors=self._counts["errors"],
            dry_run=self._dry_run,
            log_write_failures=self._write_failures,
        )

    def emit(self, record_type: str, **fields: Any) -> None:
        """Emit one decision record. Never raises."""
        if record_type not in self.RECORD_TYPES:
            # Unknown type — emit as 'warning' to stay debuggable.
            fields = {"unknown_type": record_type, **fields}
            record_type = "warning"
        payload = {
            "type": record_type,
            "schema_version": 1,
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            **fields,
        }
        self._counters_bookkeeping(record_type, fields)
        self._write(self._archival, payload, mode="a")
        self._write(self._live, {"run_id": self.run_id, **payload}, mode="a")
        if self._trace_llm and record_type in ("llm-prompt", "llm-response"):
            # Trace file gets the same payload (without run_id echo; the
            # file's name already identifies the run).
            self._write(self._trace, payload, mode="a")

    def _counters_bookkeeping(self, record_type: str, fields: dict[str, Any]) -> None:
        if record_type == "session-note":
            action = fields.get("action")
            if action == "filed":
                self._counts["notes_new"] += 1
            elif action == "merged":
                self._counts["notes_merged"] += 1
        elif record_type == "skip":
            self._counts["skipped"] += 1
        elif record_type == "error":
            self._counts["errors"] += 1

    def _write(self, path: Path, payload: dict[str, Any], *, mode: str) -> None:
        try:
            with path.open(mode) as f:
                f.write(json.dumps(payload) + "\n")
        except OSError:
            self._write_failures += 1

    @property
    def trace_enabled(self) -> bool:
        return self._trace_llm
```

- [ ] **Step 4: Confirm test passes**

Run: `pytest tests/test_run_log.py::test_run_start_written_on_enter -v`
Expected: PASS.

- [ ] **Step 5: Add emit-ordering + counter-bookkeeping test**

```python
# Append
def test_emit_counters_and_ordering(tmp_path: Path):
    with RunLogger(tmp_path, trigger="hook", pending_count=3) as logger:
        logger.emit("transcript-start", transcript_id="t1", new_turns=10)
        logger.emit("noteworthy", transcript_id="t1", verdict=True, reason="x", tier="middle")
        logger.emit("session-note", transcript_id="t1", action="filed",
                    path="p.md", wikilink="[[p]]")
        logger.emit("transcript-start", transcript_id="t2", new_turns=5)
        logger.emit("skip", transcript_id="t2", reason="noteworthy-false")
    archival = next((tmp_path / ".lore" / "runs").glob("*.jsonl"))
    records = [json.loads(l) for l in archival.read_text().splitlines()]
    # run-start is first, run-end is last, and run-end has counters
    assert records[0]["type"] == "run-start"
    assert records[-1]["type"] == "run-end"
    assert records[-1]["notes_new"] == 1
    assert records[-1]["notes_merged"] == 0
    assert records[-1]["skipped"] == 1
    assert records[-1]["errors"] == 0
    # Records in between preserve insertion order
    kinds = [r["type"] for r in records[1:-1]]
    assert kinds == ["transcript-start", "noteworthy", "session-note",
                     "transcript-start", "skip"]
```

- [ ] **Step 6: Confirm pass**

Run: `pytest tests/test_run_log.py -v`
Expected: 3+ tests PASS.

- [ ] **Step 7: Add exception-propagation test (error record + run-end emitted before propagation)**

```python
# Append
def test_exception_emits_error_and_runend_then_propagates(tmp_path: Path):
    with pytest.raises(ValueError, match="boom"):
        with RunLogger(tmp_path, trigger="hook") as logger:
            logger.emit("transcript-start", transcript_id="t1", new_turns=5)
            raise ValueError("boom")
    archival = next((tmp_path / ".lore" / "runs").glob("*.jsonl"))
    records = [json.loads(l) for l in archival.read_text().splitlines()]
    types = [r["type"] for r in records]
    assert "error" in types
    assert types[-1] == "run-end"
    assert records[-1]["errors"] >= 1
```

Remember to `import pytest` at the top of the test file if not already imported.

- [ ] **Step 8: Confirm pass**

Run: `pytest tests/test_run_log.py -v`
Expected: 4+ tests PASS.

- [ ] **Step 9: Add write-failure counter test**

```python
# Append
def test_write_failure_increments_counter(tmp_path: Path, monkeypatch):
    real_open = Path.open

    def faulty_open(self, *args, **kwargs):
        if "runs" in str(self) and not str(self).endswith("runs"):
            raise OSError("disk full")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", faulty_open)
    # Should not raise despite every write failing.
    with RunLogger(tmp_path, trigger="hook") as logger:
        logger.emit("transcript-start", transcript_id="t1", new_turns=5)
    # No file was written, but the run completes cleanly.
```

- [ ] **Step 10: Confirm pass**

Run: `pytest tests/test_run_log.py -v`
Expected: 5+ tests PASS.

- [ ] **Step 11: Add LLM trace test (only written with trace_llm=True)**

```python
# Append
def test_trace_llm_writes_companion(tmp_path: Path):
    with RunLogger(tmp_path, trigger="dry-run", trace_llm=True) as logger:
        logger.emit("llm-prompt", call="noteworthy", tier="middle",
                    token_count=100, messages=[{"role": "user", "content": "hi"}])
        logger.emit("llm-response", call="noteworthy", token_count=5, body="yes")
    trace_files = list((tmp_path / ".lore" / "runs").glob("*.trace.jsonl"))
    assert len(trace_files) == 1
    lines = trace_files[0].read_text().splitlines()
    types = [json.loads(l)["type"] for l in lines]
    assert "llm-prompt" in types
    assert "llm-response" in types


def test_trace_llm_off_no_companion(tmp_path: Path):
    with RunLogger(tmp_path, trigger="hook", trace_llm=False) as logger:
        logger.emit("llm-prompt", call="noteworthy", tier="middle",
                    token_count=100, messages=[])
    assert not list((tmp_path / ".lore" / "runs").glob("*.trace.jsonl"))
```

- [ ] **Step 12: Confirm pass**

Run: `pytest tests/test_run_log.py -v`
Expected: 7+ tests PASS.

- [ ] **Step 13: Commit**

```bash
git add lib/lore_core/run_log.py tests/test_run_log.py
git commit -m "feat(core): add RunLogger context manager with archival+live-tee"
```

---

## Phase B — Pipeline wiring

### Task 5: Wrap capture() with HookEventLogger

**Files:**
- Modify: `lib/lore_cli/hooks.py`
- Modify: `tests/test_hooks_capture.py`

**Goal:** Every hook invocation lands in `hook-events.jsonl`. Exceptions are captured (error outcome logged) AND re-raised so Claude Code still sees the non-zero exit. The hot-path guarantee (<100 ms) is preserved because the logger only appends a ~300 B line.

- [ ] **Step 1: Add test: happy path emits ledger-advanced**

```python
# tests/test_hooks_capture.py — append a new test
import json
from pathlib import Path


def test_capture_emits_hook_event_ledger_advanced(tmp_path, monkeypatch):
    """After a successful hook call, hook-events.jsonl has one line."""
    # Arrange: attach tmp_path as a wiki; empty ledger; no pending transcripts.
    # (Reuse whatever fixture setup the existing test file uses.)
    from lore_cli import hooks
    from typer.testing import CliRunner

    # Skip if fixture machinery isn't set up here — use the existing helper.
    # The key assertion:
    #   a hook-events.jsonl file appears at $LORE_ROOT/.lore/ with
    #   outcome='unattached' or 'ledger-advanced' (depending on scope).
    # Implementation: call `lore hook capture --event session-end --cwd <path>`
    # and read hook-events.jsonl.
    ...
```

NOTE: this test needs to reuse the fixture infrastructure in `tests/test_hooks_capture.py`. The implementer MUST read the existing file to find the `_attached_repo` fixture (or equivalent) and adapt. The test above is a skeleton; fill in with the existing pattern.

- [ ] **Step 2: Read `tests/test_hooks_capture.py` for fixtures**

Run: `cat tests/test_hooks_capture.py | head -80`

Adapt the new test to use the existing fixture that sets up a scoped wiki with an attached `CLAUDE.md`. The test should invoke `capture(event="session-end", cwd_override=<path>)` and assert a single line in `hook-events.jsonl`.

- [ ] **Step 3: Confirm the new test fails**

Run: `pytest tests/test_hooks_capture.py -v -k "hook_event"`
Expected: FAIL (no `hook-events.jsonl` written).

- [ ] **Step 4: Modify `capture()` in `lib/lore_cli/hooks.py`**

Replace the body of `capture(...)` with a try/except that calls `HookEventLogger`. Pattern:

```python
# lib/lore_cli/hooks.py (replace capture() body)
from lore_core.hook_log import HookEventLogger  # new import near top

@hook_app.command("capture")
def capture(
    event: str = typer.Option(...),
    transcript: Path | None = typer.Option(None),
    cwd_override: Path | None = typer.Option(None, "--cwd"),
    host: str = typer.Option("claude-code"),
) -> None:
    import time as _time
    from lore_adapters import UnknownHostError

    start = _time.monotonic()
    cwd = cwd_override or _resolve_cwd_capture()
    scope = resolve_scope(cwd)
    logger: HookEventLogger | None = None
    outcome = "unattached"
    run_id = None
    pending_after = 0
    try:
        if scope is None:
            return  # no lore_root → no logger; outcome stays "unattached"
        lore_root = _infer_lore_root(scope.claude_md_path)
        logger = HookEventLogger(lore_root)
        tledger = TranscriptLedger(lore_root)
        try:
            adapter = get_adapter(host)
        except UnknownHostError:
            outcome = "error"
            raise typer.Exit(code=1)

        if transcript is not None:
            handles = [h for h in adapter.list_transcripts(cwd) if h.path == transcript]
        else:
            handles = adapter.list_transcripts(cwd)

        for h in handles:
            entry = tledger.get(h.host, h.id)
            if entry is None:
                entry = TranscriptLedgerEntry(
                    host=h.host, transcript_id=h.id, path=h.path, directory=h.cwd,
                    digested_hash=None, digested_index_hint=None, synthesised_hash=None,
                    last_mtime=h.mtime, curator_a_run=None, noteworthy=None,
                    session_note=None,
                )
                tledger.upsert(entry)
            elif entry.last_mtime != h.mtime:
                entry.last_mtime = h.mtime
                tledger.upsert(entry)

        pending = tledger.pending()
        pending_after = len(pending)
        cfg = _load_wiki_cfg_from_scope(scope, lore_root)
        if pending_after >= cfg.curator.threshold_pending:
            _spawn_detached_curator_a(lore_root)
            outcome = "spawned-curator"
        elif pending_after > 0:
            outcome = "below-threshold"
        else:
            outcome = "no-new-turns"
    except typer.Exit:
        raise
    except Exception as exc:
        outcome = "error"
        if logger is not None:
            logger.emit(
                event=event,
                host=host,
                scope=_scope_dict(scope) if scope else None,
                duration_ms=int((_time.monotonic() - start) * 1000),
                outcome="error",
                pending_after=pending_after,
                run_id=None,
                error={"type": type(exc).__name__, "message": str(exc)},
            )
        raise
    finally:
        if logger is not None and outcome != "error":
            logger.emit(
                event=event,
                host=host,
                scope=_scope_dict(scope) if scope else None,
                duration_ms=int((_time.monotonic() - start) * 1000),
                outcome=outcome,
                pending_after=pending_after,
                run_id=run_id,
            )


def _scope_dict(scope):
    if scope is None:
        return None
    return {"wiki": scope.wiki, "scope": scope.scope}
```

Key properties:
- Unattached cwd returns before logger creation (no log dir to write to).
- `outcome` is set before the finally clause, ensuring the right value emits.
- `typer.Exit` (deliberate exits) skip the error-emit.

- [ ] **Step 5: Run the test to confirm it passes**

Run: `pytest tests/test_hooks_capture.py -v`
Expected: all previous tests still PASS; new test PASSES.

- [ ] **Step 6: Add test for error path (hook raises → outcome=error in log)**

```python
def test_capture_error_path_logs_and_reraises(tmp_path, monkeypatch):
    """A raised adapter error should produce outcome=error in hook-events.jsonl."""
    # Attach, then monkeypatch `get_adapter` to raise a generic exception
    # (not UnknownHostError or typer.Exit).
    from lore_cli import hooks

    def boom(*a, **kw):
        raise RuntimeError("adapter boom")
    monkeypatch.setattr(hooks, "get_adapter", boom)
    # invoke capture and expect exception
    with pytest.raises(RuntimeError, match="boom"):
        hooks.capture(event="session-end", cwd_override=<attached_cwd>, host="claude-code")
    log = Path("<lore_root>") / ".lore" / "hook-events.jsonl"
    records = [json.loads(l) for l in log.read_text().splitlines()]
    assert any(r["outcome"] == "error" for r in records)
```

Adapt placeholders for the fixture's attached_cwd / lore_root.

- [ ] **Step 7: Run**

Run: `pytest tests/test_hooks_capture.py -v`
Expected: new test PASSES.

- [ ] **Step 8: Commit**

```bash
git add lib/lore_cli/hooks.py tests/test_hooks_capture.py
git commit -m "feat(hooks): wrap capture() with HookEventLogger and try/except"
```

---

### Task 6: Thread RunLogger through Curator A

**Files:**
- Modify: `lib/lore_curator/curator_a.py`
- Modify: `lib/lore_curator/noteworthy.py` (add optional `logger=` kwarg)
- Modify: `lib/lore_curator/session_filer.py` (add optional `logger=` kwarg)
- Modify: `tests/test_curator_a.py`

**Goal:** Every curator run writes a full decision trace to `runs/<id>.jsonl`. The existing pipeline shape is preserved; decision callsites acquire an optional `logger` and emit records where appropriate. `dry_run=True` propagates via `RunLogger.dry_run`.

- [ ] **Step 1: Add test: RunLogger is opened on run_curator_a call**

```python
# tests/test_curator_a.py — append
def test_run_curator_a_creates_run_log(tmp_path, fake_anthropic_noteworthy_true):
    """After run_curator_a finishes, a runs/<id>.jsonl file exists with run-start/run-end."""
    from lore_curator.curator_a import run_curator_a
    # Use existing fixture that seeds one pending transcript.
    # ...
    result = run_curator_a(
        lore_root=tmp_path, anthropic_client=fake_anthropic_noteworthy_true
    )
    runs = list((tmp_path / ".lore" / "runs").glob("*.jsonl"))
    assert len(runs) == 1
    records = [json.loads(l) for l in runs[0].read_text().splitlines()]
    types = [r["type"] for r in records]
    assert types[0] == "run-start"
    assert types[-1] == "run-end"
```

Adapt to the test file's existing fixture-setup pattern.

- [ ] **Step 2: Confirm fail**

Run: `pytest tests/test_curator_a.py -v -k "create_run_log"`
Expected: FAIL — no file in `runs/`.

- [ ] **Step 3: Modify `run_curator_a` to open a RunLogger**

```python
# lib/lore_curator/curator_a.py — replace the top of run_curator_a
from lore_core.run_log import RunLogger  # new import at top

def run_curator_a(
    *,
    lore_root: Path,
    scope: Scope | None = None,
    anthropic_client: Any = None,
    adapter_lookup: Callable[[str], Adapter] | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
    trigger: str = "hook",               # NEW: "hook" | "manual" | "dry-run"
    trace_llm: bool = False,              # NEW
) -> CuratorAResult:
    start = time.monotonic()
    now = now or datetime.now(UTC)
    result = CuratorAResult()

    lookup = adapter_lookup or get_adapter
    tledger = TranscriptLedger(lore_root)
    pending_snapshot = tledger.pending()

    config_snapshot = {"noteworthy_tier": "middle"}
    effective_trigger = "dry-run" if dry_run else trigger

    # Snapshot the ledger hash at dry-run start so divergent output can be
    # debugged later. Cheap sha256 over the sorted (host, id, digested_hash)
    # tuples of pending entries.
    ledger_snapshot_hash = None
    if dry_run:
        import hashlib
        h = hashlib.sha256()
        for e in sorted(pending_snapshot, key=lambda x: (x.host, x.transcript_id)):
            h.update(f"{e.host}:{e.transcript_id}:{e.digested_hash or ''}\n".encode())
        ledger_snapshot_hash = h.hexdigest()[:16]

    with RunLogger(
        lore_root,
        trigger=effective_trigger,
        pending_count=len(pending_snapshot),
        config_snapshot=config_snapshot,
        dry_run=dry_run,
        trace_llm=trace_llm,
        ledger_snapshot_hash=ledger_snapshot_hash,  # NEW
    ) as logger:
        try:
            # Dry-run bypasses the lockfile per spec — dry-run must not
            # block on a real run in progress, and it writes nothing
            # anyway so there's no risk of corruption.
            if dry_run:
                pending = tledger.pending()
                for entry in pending:
                    result.transcripts_considered += 1
                    outcome = _process_entry(
                        entry, tledger=tledger, requested_scope=scope,
                        lore_root=lore_root, lookup=lookup,
                        anthropic_client=anthropic_client, dry_run=True,
                        now=now, logger=logger,
                    )
                    _record_outcome(result, outcome)
                result.duration_seconds = time.monotonic() - start
                return result

            with curator_lock(lore_root, timeout=0.0):
                pending = tledger.pending()
                for entry in pending:
                    result.transcripts_considered += 1
                    outcome = _process_entry(
                        entry,
                        tledger=tledger,
                        requested_scope=scope,
                        lore_root=lore_root,
                        lookup=lookup,
                        anthropic_client=anthropic_client,
                        dry_run=dry_run,
                        now=now,
                        logger=logger,              # NEW
                    )
                    _record_outcome(result, outcome)
        except LockContendedError:
            result.skipped_reasons["lock_contended"] = (
                result.skipped_reasons.get("lock_contended", 0) + 1
            )
            logger.emit("skip", reason="lock-held")

    result.duration_seconds = time.monotonic() - start
    return result
```

- [ ] **Step 4: Thread `logger` into `_process_entry`**

Add `logger: RunLogger | None = None` to `_process_entry`'s signature. At the top of the body, emit `transcript-start`:

```python
def _process_entry(
    entry: TranscriptLedgerEntry,
    *,
    tledger: TranscriptLedger,
    requested_scope: Scope | None,
    lore_root: Path,
    lookup: Callable[[str], Adapter],
    anthropic_client: Any,
    dry_run: bool,
    now: datetime,
    logger: RunLogger | None = None,   # NEW
) -> _Outcome:
    if logger is not None:
        logger.emit(
            "transcript-start",
            transcript_id=entry.transcript_id,
            hash_before=entry.digested_hash,
            new_turns=0,  # filled by the slice-reader when available; 0 is fine for the stub
        )
    # ... rest of existing body, threading logger down to noteworthy / session_filer calls
```

Then in the call to `classify_slice(...)` add `logger=logger`, and in the call to `file_session_note(...)` add `logger=logger`.

Emit `skip` records at each skip decision (noteworthy-false, lock-held, etc.) — find the existing skip branches and add:

```python
if logger is not None:
    logger.emit("skip", transcript_id=entry.transcript_id, reason="noteworthy-false")
```

- [ ] **Step 5: Add optional `logger` to `classify_slice` in `lib/lore_curator/noteworthy.py`**

Update the signature to accept `logger: RunLogger | None = None`. After the LLM call, if `logger` is set, emit a `noteworthy` record with verdict, reason, tier, latency_ms. If `logger.trace_enabled` is True, also emit `llm-prompt` / `llm-response` with the full message bodies.

Concrete edit:

```python
# lib/lore_curator/noteworthy.py
from lore_core.run_log import RunLogger  # new import

def classify_slice(
    turns,
    *,
    anthropic_client,
    tier: str = "middle",
    logger: RunLogger | None = None,
    transcript_id: str | None = None,
) -> NoteworthyResult:
    # ... existing prompt-building code ...
    if logger is not None and logger.trace_enabled:
        logger.emit(
            "llm-prompt",
            call="noteworthy",
            tier=tier,
            token_count=len(prompt_messages),  # approximate
            messages=prompt_messages,
        )
    t_before = time.monotonic()
    response = anthropic_client.messages.create(...)  # existing
    latency_ms = int((time.monotonic() - t_before) * 1000)
    # ... existing parsing into NoteworthyResult ...
    if logger is not None and logger.trace_enabled:
        logger.emit(
            "llm-response",
            call="noteworthy",
            token_count=len(response.content[0].text) if response.content else 0,
            body=response.content[0].text if response.content else "",
        )
    if logger is not None:
        logger.emit(
            "noteworthy",
            transcript_id=transcript_id,
            verdict=result.noteworthy,
            reason=result.reason,
            tier=tier,
            latency_ms=latency_ms,
        )
    return result
```

(The `trace_enabled` property is already on `RunLogger` from Task 4.)

- [ ] **Step 6: Add optional `logger` to `file_session_note` in `lib/lore_curator/session_filer.py`**

Signature extension: `logger: RunLogger | None = None`. On successful file/merge, emit `session-note` with `action` ∈ {`filed`, `merged`}, `path`, `wikilink`. If the filer runs a merge-check, emit a `merge-check` record first with target wikilink and similarity.

- [ ] **Step 7: Run test**

Run: `pytest tests/test_curator_a.py -v -k "create_run_log"`
Expected: PASS.

- [ ] **Step 8: Run full curator_a test suite**

Run: `pytest tests/test_curator_a.py -v`
Expected: all existing tests PASS (adding `logger=None` is backward-compatible).

- [ ] **Step 9: Commit**

```bash
git add lib/lore_core/run_log.py lib/lore_curator/curator_a.py \
        lib/lore_curator/noteworthy.py lib/lore_curator/session_filer.py \
        tests/test_curator_a.py
git commit -m "feat(curator): emit decision trace via RunLogger"
```

---

### Task 7: Add `--trace-llm` flag to `lore curator run`

**Files:**
- Modify: `lib/lore_curator/core.py`
- Test: `tests/test_cli_curator_run.py`

**Goal:** `lore curator run --dry-run --trace-llm` writes an LLM-trace companion file. `LORE_TRACE_LLM=1` env var achieves the same effect and is the documented path for hook-triggered runs (which can't receive a CLI flag).

- [ ] **Step 1: Write the test**

```python
# tests/test_cli_curator_run.py — append
def test_curator_run_trace_llm_flag(tmp_path, ...):
    """lore curator run --dry-run --trace-llm writes a .trace.jsonl file."""
    # Use existing fixture to seed a pending transcript.
    result = runner.invoke(app, ["run", "--dry-run", "--trace-llm"])
    assert result.exit_code == 0
    trace_files = list((tmp_path / ".lore" / "runs").glob("*.trace.jsonl"))
    assert len(trace_files) == 1


def test_curator_run_env_var_equivalent(tmp_path, monkeypatch, ...):
    monkeypatch.setenv("LORE_TRACE_LLM", "1")
    result = runner.invoke(app, ["run", "--dry-run"])
    assert result.exit_code == 0
    trace_files = list((tmp_path / ".lore" / "runs").glob("*.trace.jsonl"))
    assert len(trace_files) == 1
```

Adapt to the fixture pattern in the existing test file.

- [ ] **Step 2: Confirm fail**

Run: `pytest tests/test_cli_curator_run.py -v -k "trace_llm"`
Expected: FAIL — flag not recognized.

- [ ] **Step 3: Add the option to `run_command` in `lib/lore_curator/core.py`**

Locate `@app.command("run")` around line 817. Add:

```python
@app.command("run")
def run_command(
    scope: str = typer.Option(None, "--scope", ...),
    dry_run: bool = typer.Option(False, "--dry-run", ...),
    trace_llm: bool = typer.Option(                                      # NEW
        False, "--trace-llm",
        help="Capture full LLM prompts/responses to runs/<id>.trace.jsonl.",
    ),
    abstract: bool = typer.Option(False, "--abstract", ...),
    # ... existing options ...
) -> None:
    import os
    effective_trace = trace_llm or os.environ.get("LORE_TRACE_LLM") == "1"
    # Pass `trace_llm=effective_trace` and `trigger="manual"` to run_curator_a.
    from lore_curator.curator_a import run_curator_a
    # ... existing body, threading trace_llm through ...
    result = run_curator_a(
        lore_root=lore_root,
        dry_run=dry_run,
        trigger="manual",
        trace_llm=effective_trace,
        # ... other existing kwargs ...
    )
    # ... existing output rendering ...
```

- [ ] **Step 4: Confirm pass**

Run: `pytest tests/test_cli_curator_run.py -v -k "trace_llm"`
Expected: both PASS.

- [ ] **Step 5: Run the full file to confirm no regressions**

Run: `pytest tests/test_cli_curator_run.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add lib/lore_curator/core.py tests/test_cli_curator_run.py
git commit -m "feat(curator): add --trace-llm option and LORE_TRACE_LLM env var"
```

---

## Phase C — Reader layer

### Task 8: Run reader (parse, resolve ID, detect truncation)

**Files:**
- Create: `lib/lore_core/run_reader.py`
- Test: `tests/test_run_reader.py`

**Goal:** Pure module that reads JSONL run files, resolves user-supplied identifiers (`latest`, `^1`..`^N`, short 6-char suffix, or full-ID prefix) to a file path, and detects truncated-at-last-line runs (no `run-end` + unparseable tail → synthetic `run-truncated` record).

- [ ] **Step 1: Write resolve-id test**

```python
# tests/test_run_reader.py
from pathlib import Path

from lore_core.run_reader import resolve_run_id, RunIdNotFound, RunIdAmbiguous


def _seed(tmp_path: Path, ids: list[str]):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    for i, rid in enumerate(ids):
        # Give each file a unique mtime by writing in sequence.
        (runs / f"{rid}.jsonl").write_text('{"type":"run-start"}\n{"type":"run-end"}\n')
    return runs


def test_resolve_latest(tmp_path):
    _seed(tmp_path, [
        "2026-04-20T10-00-00-aaaaaa",
        "2026-04-20T14-00-00-bbbbbb",
        "2026-04-20T18-00-00-cccccc",
    ])
    assert resolve_run_id(tmp_path, "latest").name == "2026-04-20T18-00-00-cccccc.jsonl"


def test_resolve_caret_N(tmp_path):
    _seed(tmp_path, [
        "2026-04-20T10-00-00-aaaaaa",
        "2026-04-20T14-00-00-bbbbbb",
        "2026-04-20T18-00-00-cccccc",
    ])
    assert resolve_run_id(tmp_path, "^1").name == "2026-04-20T18-00-00-cccccc.jsonl"
    assert resolve_run_id(tmp_path, "^2").name == "2026-04-20T14-00-00-bbbbbb.jsonl"


def test_resolve_short_suffix(tmp_path):
    _seed(tmp_path, [
        "2026-04-20T10-00-00-aaaaaa",
        "2026-04-20T14-00-00-bbbbbb",
    ])
    assert resolve_run_id(tmp_path, "bbbbbb").name.endswith("bbbbbb.jsonl")


def test_resolve_prefix(tmp_path):
    _seed(tmp_path, [
        "2026-04-20T10-00-00-aaaaaa",
        "2026-04-20T14-00-00-bbbbbb",
    ])
    assert resolve_run_id(tmp_path, "2026-04-20T14").name.endswith("bbbbbb.jsonl")


def test_resolve_ambiguous_prefix_raises(tmp_path):
    _seed(tmp_path, [
        "2026-04-20T10-00-00-aaaaaa",
        "2026-04-20T10-00-00-aaaaab",
    ])
    import pytest
    with pytest.raises(RunIdAmbiguous):
        resolve_run_id(tmp_path, "2026-04-20T10-00-00-aaaa")


def test_resolve_not_found(tmp_path):
    _seed(tmp_path, ["2026-04-20T10-00-00-aaaaaa"])
    import pytest
    with pytest.raises(RunIdNotFound):
        resolve_run_id(tmp_path, "zzzzzz")
```

- [ ] **Step 2: Confirm fail**

Run: `pytest tests/test_run_reader.py -v`
Expected: ImportError on all tests.

- [ ] **Step 3: Implement resolver**

```python
# lib/lore_core/run_reader.py
"""Read-side of the run-log subsystem.

- resolve_run_id():    user identifier → archival file Path
- read_run():          JSONL → list[dict] with tolerant parsing
- detect_truncation(): last line unparseable + no run-end → synthetic record
"""

from __future__ import annotations

import json
import re
from pathlib import Path


class RunIdNotFound(ValueError):
    pass


class RunIdAmbiguous(ValueError):
    def __init__(self, matches: list[str]):
        super().__init__(f"ambiguous run ID, matches: {matches!r}")
        self.matches = matches


_CARET_RE = re.compile(r"^\^(\d+)$")


def _list_runs(lore_root: Path) -> list[Path]:
    runs_dir = lore_root / ".lore" / "runs"
    if not runs_dir.exists():
        return []
    return sorted(
        (p for p in runs_dir.glob("*.jsonl") if not p.name.endswith(".trace.jsonl")),
        key=lambda p: p.name,  # timestamp-prefixed, so lexicographic = chronological
    )


def resolve_run_id(lore_root: Path, identifier: str) -> Path:
    """Resolve a user identifier to a run file path.

    Accepts:
      - 'latest'       → most recent run
      - '^1', '^2', …  → N-th most recent (^1 == latest)
      - full ID, prefix, or 6-char suffix
    """
    runs = _list_runs(lore_root)
    if not runs:
        raise RunIdNotFound("no runs on disk")
    if identifier == "latest":
        return runs[-1]
    m = _CARET_RE.match(identifier)
    if m:
        n = int(m.group(1))
        if n < 1 or n > len(runs):
            raise RunIdNotFound(f"^{n} out of range (have {len(runs)} runs)")
        return runs[-n]
    # suffix match (6 chars, alnum lowercase)
    if re.fullmatch(r"[a-z0-9]{6}", identifier):
        matches = [p for p in runs if p.stem.endswith(f"-{identifier}")]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RunIdAmbiguous([p.stem for p in matches])
    # prefix match
    matches = [p for p in runs if p.stem.startswith(identifier)]
    if not matches:
        raise RunIdNotFound(identifier)
    if len(matches) > 1:
        raise RunIdAmbiguous([p.stem for p in matches])
    return matches[0]
```

- [ ] **Step 4: Confirm resolver tests pass**

Run: `pytest tests/test_run_reader.py -v`
Expected: 6 tests PASS.

- [ ] **Step 5: Add read_run + truncation-detection tests**

```python
# tests/test_run_reader.py — append
from lore_core.run_reader import read_run


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
        '{"type":"run-start"}\n'
        'not json at all\n'
        '{"type":"noteworthy","verdict":true}\n'
        '{"type":"run-end"}\n'
    )
    records = read_run(path)
    types = [r["type"] for r in records]
    assert types == ["run-start", "_malformed", "noteworthy", "run-end"]


def test_read_run_appends_synthetic_truncated_when_last_line_broken_and_no_runend(tmp_path):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    path = runs / "2026-04-20T10-00-00-aaaaaa.jsonl"
    path.write_text(
        '{"type":"run-start"}\n'
        '{"type":"noteworthy","verdict":true}\n'
        '{"type":"session-note","action":"fi'  # truncated
    )
    records = read_run(path)
    types = [r["type"] for r in records]
    # No run-end, and last parseable line is session-note, and there IS a
    # broken tail. Reader appends synthetic 'run-truncated'.
    assert types[-1] == "run-truncated"
```

- [ ] **Step 6: Confirm these fail**

Run: `pytest tests/test_run_reader.py -v`
Expected: 3 new tests FAIL (read_run not defined).

- [ ] **Step 7: Implement read_run**

```python
# lib/lore_core/run_reader.py — append
CURRENT_SCHEMA_VERSION = 1


class SchemaVersionTooNew(ValueError):
    """Raised by read_run (strict mode) when a record has schema_version > 1."""
    def __init__(self, version: int):
        super().__init__(f"run written by newer lore (schema v{version}). Upgrade CLI to read.")
        self.version = version


def read_run(path: Path, *, strict_schema: bool = True) -> list[dict]:
    """Return records from a run JSONL, tolerant of corruption.

    - Malformed JSON lines → {'type': '_malformed', 'raw': <line>}
    - Unparseable last line AND no 'run-end' → append synthetic
      {'type': 'run-truncated', 'note': 'run appears to have been interrupted'}
    - If strict_schema=True (default) and any record carries
      schema_version > CURRENT_SCHEMA_VERSION, raise SchemaVersionTooNew.
    - If strict_schema=False (used by `lore runs list`), unknown-schema
      records are tagged with `_schema_mismatch: True` so the caller can
      dim or mark them instead of refusing to render.
    """
    raw_lines = path.read_text().splitlines(keepends=True)
    records: list[dict] = []
    last_line_broken = False
    max_schema_seen = 0
    for i, line in enumerate(raw_lines):
        stripped = line.rstrip("\n")
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            if i == len(raw_lines) - 1 and not stripped.endswith("}"):
                last_line_broken = True
            records.append({"type": "_malformed", "raw": stripped})
            continue
        sv = record.get("schema_version", 1)
        if sv > CURRENT_SCHEMA_VERSION:
            max_schema_seen = max(max_schema_seen, sv)
            if not strict_schema:
                record["_schema_mismatch"] = True
        records.append(record)
    if strict_schema and max_schema_seen > CURRENT_SCHEMA_VERSION:
        raise SchemaVersionTooNew(max_schema_seen)
    saw_run_end = any(r.get("type") == "run-end" for r in records)
    if last_line_broken and not saw_run_end:
        if records and records[-1].get("type") == "_malformed":
            records.pop()
        records.append({
            "type": "run-truncated",
            "schema_version": 1,
            "note": "run appears to have been interrupted (last bytes unparseable)",
        })
    return records
```

Schema version policy per spec: `lore runs show <id>` uses `strict_schema=True` (refuses with a clear message); `lore runs list` uses `strict_schema=False` (dims the row with a marker).

- [ ] **Step 8: Confirm pass**

Run: `pytest tests/test_run_reader.py -v`
Expected: 9 tests PASS.

- [ ] **Step 9: Commit**

```bash
git add lib/lore_core/run_reader.py tests/test_run_reader.py
git commit -m "feat(core): add run-reader with ID resolution and truncation detection"
```

---

### Task 9: Render layer (flat log + summary + TTY/NO_COLOR/ASCII)

**Files:**
- Create: `lib/lore_cli/run_render.py`
- Test: `tests/test_run_render.py`

**Goal:** Pure renderer that takes a list of records and produces the flat-log string and the summary panel. Detects TTY / `NO_COLOR` / `LORE_ASCII=1` / `sys.stdout.encoding` and switches icon set accordingly. Terminal-width-aware (panel collapses wikilinks to basename on narrow terminals).

- [ ] **Step 1: Write tests for icon + color policy**

```python
# tests/test_run_render.py
import os
from io import StringIO
from unittest.mock import patch

from lore_core.run_reader import read_run as _read_run  # referenced later
from lore_cli.run_render import render_flat_log, render_summary_panel, IconSet, pick_icon_set


def test_pick_iconset_default_unicode(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("LORE_ASCII", raising=False)
    with patch("sys.stdout.isatty", return_value=True):
        assert pick_icon_set().kind == "unicode"


def test_pick_iconset_ascii_on_env(monkeypatch):
    monkeypatch.setenv("LORE_ASCII", "1")
    assert pick_icon_set().kind == "ascii"


def test_pick_iconset_ascii_on_non_utf8_encoding(monkeypatch):
    # Simulate an encoding that can't represent Unicode icons.
    class _FakeStdout:
        encoding = "ascii"
        def isatty(self): return True
    monkeypatch.setattr("sys.stdout", _FakeStdout())
    assert pick_icon_set().kind == "ascii"


def test_render_flat_log_basic_unicode():
    records = [
        {"type": "run-start", "ts": "2026-04-20T14:32:05Z"},
        {"type": "transcript-start", "ts": "2026-04-20T14:32:06Z",
         "transcript_id": "t1", "new_turns": 10, "hash_before": "abc1"},
        {"type": "noteworthy", "ts": "2026-04-20T14:32:07Z",
         "transcript_id": "t1", "verdict": True, "reason": "important",
         "tier": "middle", "latency_ms": 842},
        {"type": "run-end", "ts": "2026-04-20T14:32:08Z",
         "duration_ms": 3000, "notes_new": 1, "notes_merged": 0,
         "skipped": 0, "errors": 0},
    ]
    out = render_flat_log(records, icons=IconSet.unicode(), use_color=False)
    assert "▶" in out          # transcript-start
    assert "↑" in out          # noteworthy=true
    assert "═" in out          # run-end
    assert "important" in out


def test_render_flat_log_ascii_fallback():
    records = [
        {"type": "transcript-start", "ts": "2026-04-20T14:32:06Z",
         "transcript_id": "t1", "new_turns": 10, "hash_before": "abc1"},
        {"type": "noteworthy", "ts": "2026-04-20T14:32:07Z",
         "transcript_id": "t1", "verdict": False, "reason": "brief",
         "tier": "middle", "latency_ms": 300},
    ]
    out = render_flat_log(records, icons=IconSet.ascii(), use_color=False)
    assert ">" in out          # transcript-start (ASCII)
    assert "x" in out          # noteworthy=false (ASCII)
    assert "▶" not in out      # no Unicode
    assert "⊘" not in out
```

- [ ] **Step 2: Confirm fail**

Run: `pytest tests/test_run_render.py -v`
Expected: ImportError on all.

- [ ] **Step 3: Implement renderer**

```python
# lib/lore_cli/run_render.py
"""Pure renderers for run logs — no I/O.

Callers pass records (dicts from run_reader.read_run) + an IconSet +
a use_color flag; get back a string ready for print.

TTY / NO_COLOR / LORE_ASCII detection lives in `pick_icon_set()` and
`should_use_color()` so tests can inject explicitly.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class IconSet:
    kind: str
    transcript_start: str
    low_signal: str
    kept: str
    skipped: str
    filed: str
    warning: str
    error: str
    unknown: str
    run_end: str

    @classmethod
    def unicode(cls) -> "IconSet":
        return cls("unicode", "▶", "·", "↑", "⊘", "✓", "!", "✗", "?", "═")

    @classmethod
    def ascii(cls) -> "IconSet":
        return cls("ascii", ">", ".", "+", "x", "+", "!", "X", "?", "=")


def pick_icon_set() -> IconSet:
    if os.environ.get("LORE_ASCII") == "1":
        return IconSet.ascii()
    enc = getattr(sys.stdout, "encoding", "") or ""
    if "utf" not in enc.lower():
        return IconSet.ascii()
    return IconSet.unicode()


def should_use_color() -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    isatty = getattr(sys.stdout, "isatty", lambda: False)
    return bool(isatty())


def render_flat_log(records: list[dict], *, icons: IconSet, use_color: bool) -> str:
    lines: list[str] = []
    for r in records:
        lines.append(_render_record(r, icons, use_color))
    return "\n".join(lines)


def _render_record(r: dict, icons: IconSet, use_color: bool) -> str:
    t = r.get("type", "unknown")
    ts = _short_time(r.get("ts", ""))
    icon, kind_label, message = _icon_and_message(r, icons)
    return f"{ts} {icon} {kind_label:<14} {message}"


def _short_time(ts: str) -> str:
    # 2026-04-20T14:32:05.421Z -> 14:32:05
    if "T" in ts:
        tail = ts.split("T", 1)[1]
        return tail[:8]
    return ts


def _icon_and_message(r: dict, icons: IconSet) -> tuple[str, str, str]:
    t = r.get("type")
    if t == "run-start":
        return icons.low_signal, "start-run", f"trigger={r.get('trigger', '?')}"
    if t == "transcript-start":
        tid = r.get("transcript_id", "?")
        hb = r.get("hash_before") or "∅"
        turns = r.get("new_turns", 0)
        return icons.transcript_start, "start", f"transcript {tid} (hash {hb}, {turns} new turns)"
    if t == "redaction":
        kinds = ", ".join(r.get("kinds") or [])
        return icons.low_signal, "redacted", f"{r.get('hits', 0)} hits ({kinds})"
    if t == "noteworthy":
        verdict = r.get("verdict")
        icon = icons.kept if verdict else icons.skipped
        reason = _truncate(r.get("reason", ""), 80)
        latency = r.get("latency_ms", 0)
        return icon, "noteworthy", f"{verdict} — {reason!r} ({latency}ms)"
    if t == "merge-check":
        target = r.get("target", "?")
        sim = r.get("similarity", 0)
        decision = r.get("decision", "?")
        return icons.low_signal, "merge-check", f"{target} similarity={sim} → {decision}"
    if t == "session-note":
        action = r.get("action", "?")
        wikilink = r.get("wikilink", "?")
        if action == "filed":
            return icons.filed, "filed", wikilink
        return icons.filed, "merged", f"into {wikilink}"
    if t == "skip":
        return icons.skipped, "skipped", r.get("reason", "?")
    if t == "warning":
        return icons.warning, "warning", r.get("message", "")
    if t == "error":
        return icons.error, "error", f"{r.get('exception', 'Error')}: {r.get('message', '')}"
    if t == "run-end":
        dur = r.get("duration_ms", 0) / 1000.0
        nn = r.get("notes_new", 0); nm = r.get("notes_merged", 0)
        sk = r.get("skipped", 0); er = r.get("errors", 0)
        return icons.run_end, "end", f"{dur:.1f}s · {nn} new, {nm} merged, {sk} skipped · {er} errors"
    if t == "run-truncated":
        return icons.error, "run-truncated", r.get("note", "run interrupted")
    if t == "_malformed":
        return icons.error, "malformed", "<line unparseable>"
    # Unknown record type
    return icons.unknown, "unknown", f"type={t!r}"


def _truncate(s: str, maxlen: int) -> str:
    if len(s) <= maxlen:
        return s
    return s[: maxlen - 1] + "…"
```

For the summary panel (simpler path — build a plain list of lines; Rich decoration in the command layer):

```python
def render_summary_panel(records: list[dict], *, term_width: int = 80) -> list[str]:
    """Return the summary-panel content as a list of lines.

    Caller wraps in a Rich panel (or plain text on non-TTY).
    Wikilinks collapse to basename ellipsis when `term_width < 60`.
    """
    start = next((r for r in records if r.get("type") == "run-start"), {})
    end = next((r for r in reversed(records) if r.get("type") == "run-end"), {})
    filed = [r for r in records if r.get("type") == "session-note" and r.get("action") == "filed"]
    merged = [r for r in records if r.get("type") == "session-note" and r.get("action") == "merged"]

    def fmt_link(link: str) -> str:
        if term_width >= 60:
            return link
        # Collapse [[2026-04-20-some-slug]] → [[...some-slug]]
        inner = link.strip("[]")
        return f"[[...{inner[-40:]}]]" if len(inner) > 40 else link

    lines: list[str] = []
    lines.append(f"Started   {start.get('ts', '?')}")
    dur_ms = end.get("duration_ms", 0)
    lines.append(f"Duration  {dur_ms / 1000:.1f}s")
    lines.append(f"Trigger   {start.get('trigger', '?')}")
    errors = end.get("errors", 0)
    new = end.get("notes_new", 0)
    merged_ct = end.get("notes_merged", 0)
    skipped = end.get("skipped", 0)
    lines.append(
        f"Outcome   {new} new, {merged_ct} merged, {skipped} skipped · {errors} errors"
    )
    links = [fmt_link(r.get("wikilink", "")) for r in filed]
    links += [f"{fmt_link(r.get('wikilink', ''))} (merged)" for r in merged]
    if links:
        lines.append("Notes     " + links[0])
        for l in links[1:]:
            lines.append("          " + l)
    return lines
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_run_render.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Add summary-panel basename-collapse test**

```python
# tests/test_run_render.py — append
def test_summary_panel_collapses_wikilinks_on_narrow_terminal():
    records = [
        {"type": "run-start", "ts": "2026-04-20T14:32:05Z", "trigger": "hook"},
        {"type": "session-note", "ts": "2026-04-20T14:32:07Z",
         "action": "filed", "wikilink": "[[2026-04-20-very-long-descriptive-note-slug]]"},
        {"type": "run-end", "ts": "2026-04-20T14:32:08Z",
         "duration_ms": 3000, "notes_new": 1, "notes_merged": 0,
         "skipped": 0, "errors": 0},
    ]
    narrow = render_summary_panel(records, term_width=40)
    wide = render_summary_panel(records, term_width=120)
    assert any("..." in line for line in narrow), "narrow should collapse"
    assert any("very-long-descriptive-note-slug" in line for line in wide)
```

- [ ] **Step 6: Confirm pass**

Run: `pytest tests/test_run_render.py -v`
Expected: 6 PASS.

- [ ] **Step 7: Commit**

```bash
git add lib/lore_cli/run_render.py tests/test_run_render.py
git commit -m "feat(cli): add run-log renderer with Unicode/ASCII icons and responsive panel"
```

---

### Task 10: `lore runs show <id>`

**Files:**
- Create: `lib/lore_cli/runs_cmd.py` (initial — list/tail come next)
- Modify: `lib/lore_cli/__main__.py` (register the app)
- Test: `tests/test_cli_runs.py`

**Goal:** Invoking `lore runs show latest` prints the summary panel followed by the flat log. `--verbose` adds LLM-trace records inline when the companion exists; a clear message if not. `--json` passes through raw JSONL. Non-TTY falls back to plain text (no panel box).

- [ ] **Step 1: Write show-happy-path test**

```python
# tests/test_cli_runs.py
import json
from pathlib import Path
from typer.testing import CliRunner


def _seed_run(tmp_path: Path):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    path = runs / "2026-04-20T14-32-05-a1b2c3.jsonl"
    path.write_text(
        json.dumps({"type": "run-start", "ts": "2026-04-20T14:32:05Z",
                    "run_id": "2026-04-20T14-32-05-a1b2c3", "trigger": "hook"}) + "\n" +
        json.dumps({"type": "transcript-start", "ts": "2026-04-20T14:32:06Z",
                    "transcript_id": "t1", "new_turns": 10, "hash_before": "abc"}) + "\n" +
        json.dumps({"type": "noteworthy", "ts": "2026-04-20T14:32:07Z",
                    "transcript_id": "t1", "verdict": True, "reason": "worthy",
                    "tier": "middle", "latency_ms": 500}) + "\n" +
        json.dumps({"type": "session-note", "ts": "2026-04-20T14:32:08Z",
                    "transcript_id": "t1", "action": "filed",
                    "path": "p.md", "wikilink": "[[2026-04-20-test]]"}) + "\n" +
        json.dumps({"type": "run-end", "ts": "2026-04-20T14:32:09Z",
                    "duration_ms": 4000, "notes_new": 1, "notes_merged": 0,
                    "skipped": 0, "errors": 0}) + "\n"
    )
    return path


def test_runs_show_latest(tmp_path, monkeypatch):
    from lore_cli import runs_cmd
    _seed_run(tmp_path)
    runner = CliRunner()
    # Monkeypatch lore_root lookup.
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["show", "latest"])
    assert result.exit_code == 0
    assert "2026-04-20-test" in result.stdout           # wikilink rendered
    assert "1 new" in result.stdout                     # summary
    assert "worthy" in result.stdout                    # reason in flat log


def test_runs_show_json_mode(tmp_path, monkeypatch):
    from lore_cli import runs_cmd
    _seed_run(tmp_path)
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["show", "a1b2c3", "--json"])
    assert result.exit_code == 0
    # Each line is valid JSON.
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    for l in lines:
        json.loads(l)


def test_runs_show_verbose_without_companion_prints_clear_message(tmp_path, monkeypatch):
    from lore_cli import runs_cmd
    _seed_run(tmp_path)
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["show", "latest", "--verbose"])
    assert result.exit_code == 0
    assert "LORE_TRACE_LLM" in result.stdout
```

- [ ] **Step 2: Confirm fail**

Run: `pytest tests/test_cli_runs.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `runs_cmd.py`**

```python
# lib/lore_cli/runs_cmd.py
"""`lore runs` — inspect Curator A run logs."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from lore_core.run_reader import (
    RunIdAmbiguous, RunIdNotFound, read_run, resolve_run_id,
)
from lore_cli.run_render import (
    IconSet, pick_icon_set, render_flat_log, render_summary_panel,
    should_use_color,
)

app = typer.Typer(
    add_completion=False,
    help="Inspect curator run logs. Scenarios:\n\n"
         "  no note appeared?         lore runs show latest\n"
         "  hook plumbing feels off?  lore doctor\n"
         "  tuning config?            lore curator run --dry-run --trace-llm",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()


def _get_lore_root() -> Path:
    from lore_core.config import get_lore_root
    return get_lore_root()


@app.command("show")
def show(
    run_id: str = typer.Argument(..., help="latest | ^N | short suffix | full ID | prefix"),
    verbose: bool = typer.Option(False, "--verbose", help="Include LLM prompts/responses"),
    raw: bool = typer.Option(False, "--raw", help="Disable 3-line trace truncation (requires --verbose)"),
    json_out: bool = typer.Option(False, "--json", help="Print raw JSONL"),
) -> None:
    lore_root = _get_lore_root()
    try:
        path = resolve_run_id(lore_root, run_id)
    except RunIdNotFound as e:
        console.print(f"[red]Run not found: {e}. Try `lore runs list`.[/red]")
        raise typer.Exit(code=1)
    except RunIdAmbiguous as e:
        console.print(f"[yellow]Ambiguous — matches:[/yellow] {', '.join(e.matches)}")
        raise typer.Exit(code=1)

    if json_out:
        sys.stdout.write(path.read_text())
        return

    try:
        records = read_run(path, strict_schema=True)
    except Exception as e:
        from lore_core.run_reader import SchemaVersionTooNew
        if isinstance(e, SchemaVersionTooNew):
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=1)
        raise
    if verbose:
        trace_path = path.with_suffix(".trace.jsonl")
        if not trace_path.exists():
            console.print(
                "[yellow]LLM trace not captured for this run. "
                "Re-run with [bold]LORE_TRACE_LLM=1 lore curator run --dry-run[/bold] "
                "to capture.[/yellow]"
            )
        else:
            # Merge trace records into the main stream by ts (simple stable sort).
            trace_records = read_run(trace_path)
            records = sorted(records + trace_records, key=lambda r: r.get("ts", ""))

    term_width = shutil.get_terminal_size((80, 20)).columns
    icons = pick_icon_set()
    use_color = should_use_color()

    # Summary panel
    panel_lines = render_summary_panel(records, term_width=term_width)
    short_id = path.stem.split("-")[-1]
    header = f"Run {short_id} ({path.stem})"
    if use_color and sys.stdout.isatty():
        console.print(Panel("\n".join(panel_lines), title=header, expand=False))
    else:
        # Plain text fallback — no box drawing.
        console.print(header, no_wrap=True)
        for l in panel_lines:
            console.print(l, no_wrap=True)

    # Flat decision log
    flat = render_flat_log(records, icons=icons, use_color=use_color)
    console.print(flat)
```

- [ ] **Step 4: Register the app in `__main__.py`**

Edit `lib/lore_cli/__main__.py`: add `runs_cmd` to the imports (alphabetical) and `app.add_typer(runs_cmd.app, name="runs")` alongside the others.

```python
# in imports block
from lore_cli import (
    attach_cmd,
    briefing_cmd,
    detach_cmd,
    doctor_cmd,
    hooks,
    inbox_cmd,
    ingest_cmd,
    init_cmd,
    install_cmd,
    new_wiki_cmd,
    registry_cmd,
    resume_cmd,
    runs_cmd,          # NEW
    session_cmd,
    surface_cmd,
)
```

```python
# near the other add_typer calls, alphabetical:
app.add_typer(runs_cmd.app, name="runs")
```

- [ ] **Step 5: Run**

Run: `pytest tests/test_cli_runs.py -v`
Expected: 3 PASS.

- [ ] **Step 6: Manual smoke test**

Run: `lore runs --help`
Expected: shows the scenarios epilog.

Run: `lore runs show nothing`
Expected: `Run not found...`.

- [ ] **Step 7: Commit**

```bash
git add lib/lore_cli/runs_cmd.py lib/lore_cli/__main__.py tests/test_cli_runs.py
git commit -m "feat(cli): add \`lore runs show\`"
```

---

### Task 11: `lore runs list [--hooks]`

**Files:**
- Modify: `lib/lore_cli/runs_cmd.py`
- Modify: `tests/test_cli_runs.py`

**Goal:** Table of recent runs (default 20). `--hooks` interleaves hook events. `--json` prints raw JSONL.

- [ ] **Step 1: Write the test**

```python
# tests/test_cli_runs.py — append
def test_runs_list_empty(tmp_path, monkeypatch):
    from lore_cli import runs_cmd
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["list"])
    assert result.exit_code == 0
    assert "No" in result.stdout or "no" in result.stdout  # empty state


def test_runs_list_shows_seeded_run(tmp_path, monkeypatch):
    from lore_cli import runs_cmd
    _seed_run(tmp_path)
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["list"])
    assert result.exit_code == 0
    assert "a1b2c3" in result.stdout
    assert "1 new" in result.stdout
```

- [ ] **Step 2: Confirm fail**

Run: `pytest tests/test_cli_runs.py -v -k "runs_list"`
Expected: FAIL — `list` command not defined.

- [ ] **Step 3: Add the command**

```python
# lib/lore_cli/runs_cmd.py — append
from rich.table import Table


@app.command("list")
def list_runs(
    limit: int = typer.Option(20, "--limit", help="Maximum runs to show."),
    hooks: bool = typer.Option(False, "--hooks", help="Interleave hook events."),
    json_out: bool = typer.Option(False, "--json", help="Print raw JSONL."),
) -> None:
    lore_root = _get_lore_root()
    runs_dir = lore_root / ".lore" / "runs"
    if not runs_dir.exists():
        console.print("[dim]No capture activity yet.[/dim]")
        return

    archival = sorted(
        (p for p in runs_dir.glob("*.jsonl") if not p.name.endswith(".trace.jsonl")),
        key=lambda p: p.name,
        reverse=True,
    )[:limit]

    if json_out:
        for p in archival:
            sys.stdout.write(p.read_text())
        return

    table = Table(title=None)
    table.add_column("ID"); table.add_column("Started")
    table.add_column("Duration"); table.add_column("Transcripts")
    table.add_column("Notes"); table.add_column("Reason")
    table.add_column("Errors")

    for p in archival:
        # list() uses strict_schema=False so newer-schema rows dim rather than crash the table.
        records = read_run(p, strict_schema=False)
        schema_mismatch = any(r.get("_schema_mismatch") for r in records)
        start = next((r for r in records if r.get("type") == "run-start"), {})
        end = next((r for r in reversed(records) if r.get("type") == "run-end"), {})
        short_id = p.stem.split("-")[-1]
        started = _relative_time_cli(start.get("ts", ""))
        dur = f"{end.get('duration_ms', 0) / 1000:.1f}s"
        t_count = sum(1 for r in records if r.get("type") == "transcript-start")
        notes_new = end.get("notes_new", 0)
        notes_merged = end.get("notes_merged", 0)
        notes_cell = f"{notes_new} new" + (f"+{notes_merged}m" if notes_merged else "")
        if notes_new == 0 and notes_merged == 0:
            notes_cell = "0"
            skipped = end.get("skipped", 0)
            reason = f"all skipped ({skipped})" if skipped else "—"
        else:
            reason = "—"
        errors = end.get("errors", 0)
        if schema_mismatch:
            short_id = f"[dim]{short_id}[/dim]"
            reason = f"[dim]{reason} (schema v? · upgrade lore)[/dim]"
        table.add_row(short_id, started, dur, str(t_count), notes_cell, reason, str(errors))

    console.print(table)


def _relative_time_cli(ts_iso: str) -> str:
    from datetime import UTC, datetime
    if not ts_iso:
        return "?"
    try:
        ts = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    except ValueError:
        return ts_iso
    delta = datetime.now(UTC) - ts
    s = delta.total_seconds()
    if s < 60: return "just now"
    if s < 3600: return f"{int(s//60)}m ago"
    if s < 86400: return f"{int(s//3600)}h ago"
    return f"{int(s//86400)}d ago"
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_cli_runs.py -v -k "runs_list"`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/lore_cli/runs_cmd.py tests/test_cli_runs.py
git commit -m "feat(cli): add \`lore runs list\`"
```

---

### Task 12: `lore runs tail`

**Files:**
- Modify: `lib/lore_cli/runs_cmd.py`
- Modify: `tests/test_cli_runs.py`

**Goal:** Follow `runs-live.jsonl`. Default is follow-forever (`tail -F` muscle memory). `--once` exits on the first `run-end` record. On `FileNotFoundError`, exit cleanly. 30-min idle timeout in `--once` mode with clear message.

- [ ] **Step 1: Write a test with `--once` semantics (using a pre-populated live file)**

```python
# tests/test_cli_runs.py — append
def test_runs_tail_once_reads_to_run_end(tmp_path, monkeypatch):
    from lore_cli import runs_cmd
    live = tmp_path / ".lore" / "runs-live.jsonl"
    live.parent.mkdir(parents=True)
    live.write_text(
        json.dumps({"type": "run-start", "ts": "2026-04-20T14:32:05Z",
                    "run_id": "r", "trigger": "hook"}) + "\n" +
        json.dumps({"type": "run-end", "ts": "2026-04-20T14:32:09Z",
                    "duration_ms": 4000, "notes_new": 1, "notes_merged": 0,
                    "skipped": 0, "errors": 0}) + "\n"
    )
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    # Speed up poll for test.
    monkeypatch.setattr(runs_cmd, "_POLL_INTERVAL_S", 0.01)
    result = runner.invoke(runs_cmd.app, ["tail", "--once"])
    assert result.exit_code == 0
    assert "start" in result.stdout
    assert "end" in result.stdout


def test_runs_tail_missing_live_file(tmp_path, monkeypatch):
    from lore_cli import runs_cmd
    runner = CliRunner()
    monkeypatch.setattr(runs_cmd, "_get_lore_root", lambda: tmp_path)
    result = runner.invoke(runs_cmd.app, ["tail", "--once"])
    assert result.exit_code == 0
    assert "No active run" in result.stdout or "no active" in result.stdout.lower()
```

- [ ] **Step 2: Confirm fail**

Run: `pytest tests/test_cli_runs.py -v -k "runs_tail"`
Expected: FAIL.

- [ ] **Step 3: Implement tail**

```python
# lib/lore_cli/runs_cmd.py — append
import time

_POLL_INTERVAL_S = 0.2
_IDLE_TIMEOUT_S = 30 * 60  # 30 min


@app.command("tail")
def tail(
    once: bool = typer.Option(False, "--once", help="Exit on run-end (don't wait for next run)."),
) -> None:
    lore_root = _get_lore_root()
    live = lore_root / ".lore" / "runs-live.jsonl"
    if not live.exists():
        console.print("[dim]No active run. Use `lore runs show latest` for the last completed run.[/dim]")
        return

    icons = pick_icon_set()
    use_color = should_use_color()
    pos = 0
    idle_since = time.monotonic()
    saw_run_end = False

    while True:
        try:
            size = live.stat().st_size
        except FileNotFoundError:
            console.print("[dim]live log disappeared — exiting.[/dim]")
            return

        if size > pos:
            with live.open("r") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            for line in chunk.splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                console.print(render_flat_log([record], icons=icons, use_color=use_color))
                if record.get("type") == "run-end":
                    saw_run_end = True
            idle_since = time.monotonic()

        if once and saw_run_end:
            return
        if once and time.monotonic() - idle_since > _IDLE_TIMEOUT_S:
            console.print(
                "[yellow]no new output for 30min — use `lore runs show <id>` "
                "or check for stale lockfile.[/yellow]"
            )
            return

        time.sleep(_POLL_INTERVAL_S)
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_cli_runs.py -v -k "runs_tail"`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/lore_cli/runs_cmd.py tests/test_cli_runs.py
git commit -m "feat(cli): add \`lore runs tail\`"
```

---

## Phase D — Discovery

### Task 13: Extend SessionStart banner

**Files:**
- Modify: `lib/lore_cli/breadcrumb.py`
- Modify: `tests/test_breadcrumb.py`

**Goal:** The banner grows three new signals:
1. **Last-run error prefix** (`lore!: last run had N errors · lore runs show <short>`) when most recent run had `errors > 0`.
2. **Hook-error trailing segment** when any hook in last 24h had `outcome=error`.
3. **All-skips hint** (`lore: last run filed 0 notes (N skipped) · lore runs show latest`) when most recent run had `errors=0` and `notes_new + notes_merged == 0`.

- [ ] **Step 1: Write tests**

```python
# tests/test_breadcrumb.py — append
def test_banner_all_skips_hint(tmp_path):
    # Seed a run with errors=0, notes_new=0, notes_merged=0, skipped=3
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    path = runs / "2026-04-20T14-32-05-xxxxxx.jsonl"
    path.write_text(
        json.dumps({"type": "run-start", "ts": "2026-04-20T14:32:05Z",
                    "trigger": "hook"}) + "\n" +
        json.dumps({"type": "run-end", "ts": "2026-04-20T14:32:09Z",
                    "duration_ms": 4000, "notes_new": 0, "notes_merged": 0,
                    "skipped": 3, "errors": 0}) + "\n"
    )
    from lore_cli.breadcrumb import BannerContext, render_banner
    # Construct a minimal context (reuse an existing helper if available).
    ctx = _make_banner_context(tmp_path)
    banner = render_banner(ctx)
    assert "0 notes" in banner
    assert "3 skipped" in banner
    assert "lore runs show latest" in banner


def test_banner_last_run_error_prefix(tmp_path):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    path = runs / "2026-04-20T14-32-05-errrun.jsonl"
    path.write_text(
        json.dumps({"type": "run-start", "ts": "2026-04-20T14:32:05Z",
                    "trigger": "hook"}) + "\n" +
        json.dumps({"type": "run-end", "ts": "2026-04-20T14:32:09Z",
                    "duration_ms": 4000, "notes_new": 0, "notes_merged": 0,
                    "skipped": 0, "errors": 2}) + "\n"
    )
    from lore_cli.breadcrumb import render_banner
    ctx = _make_banner_context(tmp_path)
    banner = render_banner(ctx)
    assert banner.startswith("lore!:")
    assert "2 errors" in banner
    assert "errrun" in banner  # short ID in hint


def test_banner_hook_error_trailing_segment(tmp_path):
    events = tmp_path / ".lore" / "hook-events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    from datetime import UTC, datetime
    recent = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    events.write_text(
        json.dumps({"schema_version": 1, "ts": recent, "event": "session-end",
                    "outcome": "error"}) + "\n"
    )
    from lore_cli.breadcrumb import render_banner
    ctx = _make_banner_context(tmp_path)
    banner = render_banner(ctx)
    assert "hook error" in banner
```

Add `_make_banner_context` to the existing test helpers (or inline); it should build a `BannerContext` with a tmp_path-based `lore_root`, a minimal `scope`, default `WikiConfig`, and `now=datetime.now(UTC)`.

- [ ] **Step 2: Confirm fail**

Run: `pytest tests/test_breadcrumb.py -v -k "all_skips or last_run_error or hook_error"`
Expected: all FAIL.

- [ ] **Step 3: Modify `render_banner` to check the new conditions**

```python
# lib/lore_cli/breadcrumb.py — extend render_banner
import json
from datetime import timedelta


def _recent_hook_errors(lore_root, *, within: timedelta, now) -> int:
    path = lore_root / ".lore" / "hook-events.jsonl"
    if not path.exists():
        return 0
    threshold = now - within
    count = 0
    try:
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("outcome") != "error":
                continue
            try:
                ts = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if ts >= threshold:
                count += 1
    except OSError:
        return 0
    return count


def _most_recent_run_end(lore_root):
    runs_dir = lore_root / ".lore" / "runs"
    if not runs_dir.exists():
        return None, None
    files = sorted(
        (p for p in runs_dir.glob("*.jsonl") if not p.name.endswith(".trace.jsonl")),
        key=lambda p: p.name,
    )
    if not files:
        return None, None
    latest = files[-1]
    try:
        lines = latest.read_text().splitlines()
    except OSError:
        return None, None
    for line in reversed(lines):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("type") == "run-end":
            return latest, r
    return latest, None
```

Now update `render_banner` to consult these:

```python
def render_banner(ctx: BannerContext, *, errors: list[str] | None = None) -> str | None:
    mode = ctx.wiki_config.breadcrumb.mode
    errors = errors or []
    if errors:
        return "lore!: " + " · ".join(errors)

    # NEW: last-run error prefix always wins over normal banners.
    latest_path, run_end = _most_recent_run_end(ctx.lore_root)
    if run_end and run_end.get("errors", 0) > 0:
        short = latest_path.stem.split("-")[-1]
        return (
            f"lore!: last run had {run_end['errors']} errors "
            f"({_relative_time(_parse_ts(run_end['ts']), ctx.now)}) "
            f"· lore runs show {short}"
        )

    if mode == "quiet":
        return None

    # (existing lockfile + pending-ledger logic unchanged)
    tledger = TranscriptLedger(ctx.lore_root)
    pending = tledger.pending()
    wledger = WikiLedger(ctx.lore_root, ctx.scope.wiki)
    entry = wledger.read()
    lock_dir = ctx.lore_root / ".lore" / "curator.lock"
    if lock_dir.exists():
        return "lore: curator A running in background"

    parts = []
    if pending:
        parts.append(f"{len(pending)} pending")
        if entry.last_curator_a:
            parts.append(f"last curator {_relative_time(entry.last_curator_a, ctx.now)}")
        if entry.last_briefing:
            parts.append(f"briefing {_relative_time_short(entry.last_briefing, ctx.now)}")
        banner = "lore: " + " · ".join(parts)
    else:
        # NEW: all-skips hint (only when no pending, no errors).
        if run_end and run_end.get("errors", 0) == 0 \
           and run_end.get("notes_new", 0) == 0 \
           and run_end.get("notes_merged", 0) == 0 \
           and run_end.get("skipped", 0) > 0:
            return (
                f"lore: last run filed 0 notes "
                f"({run_end['skipped']} skipped) · lore runs show latest"
            )
        parts.append("up to date")
        parts.append(f"{ctx.note_count} notes in {ctx.scope.wiki}/{ctx.scope.scope}")
        banner = "lore: " + " · ".join(parts)

    # NEW: trailing hook-error segment.
    hook_errors_24h = _recent_hook_errors(
        ctx.lore_root, within=timedelta(hours=24), now=ctx.now
    )
    if hook_errors_24h > 0:
        banner += f" · {hook_errors_24h} hook error{'s' if hook_errors_24h > 1 else ''} today (lore doctor)"
    return banner


def _parse_ts(ts_iso: str):
    return datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_breadcrumb.py -v`
Expected: all PASS (old and new).

- [ ] **Step 5: Commit**

```bash
git add lib/lore_cli/breadcrumb.py tests/test_breadcrumb.py
git commit -m "feat(cli): banner surfaces run errors, all-skips, and hook errors"
```

---

### Task 14: `lore doctor` Capture pipeline panel

**Files:**
- Modify: `lib/lore_cli/doctor_cmd.py`
- Modify: `tests/test_doctor.py`

**Goal:** Add a single new panel to `lore doctor` that reads the two log streams and reports: last hook fired, last curator run, last note filed, stale lockfile, hook errors in last 24h, observability write failures (sentinel marker mtime).

- [ ] **Step 1: Write tests for each panel line**

```python
# tests/test_doctor.py — append
def test_doctor_capture_panel_empty(tmp_path, monkeypatch):
    from lore_cli.doctor_cmd import run_capture_panel
    lines = run_capture_panel(tmp_path)
    assert any("No capture activity" in l for l in lines)


def test_doctor_capture_panel_last_hook_and_run_and_note(tmp_path, monkeypatch):
    # Seed: one hook event, one run with a filed note.
    events = tmp_path / ".lore" / "hook-events.jsonl"
    runs = tmp_path / ".lore" / "runs"
    events.parent.mkdir(parents=True)
    runs.mkdir()
    from datetime import UTC, datetime
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    events.write_text(json.dumps({
        "schema_version": 1, "ts": now, "event": "session-end",
        "outcome": "spawned-curator", "duration_ms": 40,
    }) + "\n")
    (runs / "2026-04-20T14-32-05-xxxxxx.jsonl").write_text(
        json.dumps({"type": "run-start", "ts": now, "trigger": "hook"}) + "\n" +
        json.dumps({"type": "session-note", "ts": now, "action": "filed",
                    "wikilink": "[[some-note]]"}) + "\n" +
        json.dumps({"type": "run-end", "ts": now, "duration_ms": 3000,
                    "notes_new": 1, "notes_merged": 0, "skipped": 0, "errors": 0}) + "\n"
    )
    from lore_cli.doctor_cmd import run_capture_panel
    lines = run_capture_panel(tmp_path)
    flat = " ".join(lines)
    assert "Last hook fired" in flat
    assert "Last curator run" in flat
    assert "Last note filed" in flat
    assert "some-note" in flat


def test_doctor_capture_panel_hook_error_warning(tmp_path):
    events = tmp_path / ".lore" / "hook-events.jsonl"
    events.parent.mkdir(parents=True)
    from datetime import UTC, datetime
    ts = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    events.write_text(
        json.dumps({"schema_version": 1, "ts": ts, "event": "session-end",
                    "outcome": "error"}) + "\n"
    )
    from lore_cli.doctor_cmd import run_capture_panel
    lines = run_capture_panel(tmp_path)
    flat = " ".join(lines)
    assert "hook error" in flat
```

- [ ] **Step 2: Confirm fail**

Run: `pytest tests/test_doctor.py -v -k "capture_panel"`
Expected: FAIL — function not defined.

- [ ] **Step 3: Implement**

```python
# lib/lore_cli/doctor_cmd.py — append
import json
from datetime import UTC, datetime, timedelta


def run_capture_panel(lore_root: Path) -> list[str]:
    """Return lines for the Capture pipeline panel.

    Format: `  ✓ Label: value` per line. Empty state: one "No capture
    activity yet" line.
    """
    lines: list[str] = ["Capture pipeline"]
    any_data = False

    # Last hook
    events_path = lore_root / ".lore" / "hook-events.jsonl"
    if events_path.exists():
        any_data = True
        last = _last_json_line(events_path)
        if last:
            event = last.get("event", "?")
            outcome = last.get("outcome", "?")
            ago = _relative(last.get("ts", ""))
            lines.append(f"  ✓ Last hook fired {ago} ({event}, outcome: {outcome})")

    # Last curator run
    runs_dir = lore_root / ".lore" / "runs"
    if runs_dir.exists():
        files = sorted(
            (p for p in runs_dir.glob("*.jsonl") if not p.name.endswith(".trace.jsonl")),
            key=lambda p: p.name, reverse=True,
        )
        if files:
            any_data = True
            latest = files[0]
            records = [json.loads(l) for l in latest.read_text().splitlines() if l.strip()]
            end = next((r for r in reversed(records) if r.get("type") == "run-end"), None)
            if end:
                ago = _relative(end.get("ts", ""))
                dur = f"{end.get('duration_ms', 0) / 1000:.1f}s"
                t_count = sum(1 for r in records if r.get("type") == "transcript-start")
                errors = end.get("errors", 0)
                lines.append(
                    f"  ✓ Last curator run {ago} ({dur}, {t_count} transcripts, {errors} errors)"
                )
            # Last note filed across any run — walk all files newest→oldest
            last_note = None
            for p in files:
                for l in p.read_text().splitlines():
                    try:
                        r = json.loads(l)
                    except json.JSONDecodeError:
                        continue
                    if r.get("type") == "session-note" and r.get("action") == "filed":
                        last_note = r
                        break
                if last_note:
                    break
            if last_note:
                lines.append(
                    f"  ✓ Last note filed {_relative(last_note.get('ts', ''))} — "
                    f"{last_note.get('wikilink', '')}"
                )

    # Stale lockfile
    lock = lore_root / ".lore" / "curator.lock"
    if lock.exists():
        age_s = (datetime.now().timestamp() - lock.stat().st_mtime)
        if age_s > 3600:
            lines.append(f"  ✗ Stale lockfile ({int(age_s/60)}min old) — remove with `rm -r {lock}`")
        else:
            lines.append(f"  ✓ Lockfile present (curator running, {int(age_s)}s)")
    else:
        if any_data:
            lines.append("  ✓ No stale lockfile")

    # Hook errors in last 24h
    hook_err = 0
    if events_path.exists():
        threshold = datetime.now(UTC) - timedelta(hours=24)
        for l in events_path.read_text().splitlines():
            try:
                r = json.loads(l)
            except json.JSONDecodeError:
                continue
            if r.get("outcome") != "error":
                continue
            try:
                ts = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if ts >= threshold:
                hook_err += 1
    if hook_err > 0:
        lines.append(f"  ✗ {hook_err} hook error{'s' if hook_err > 1 else ''} in last 24h — lore runs list --hooks")

    # Observability write failures
    marker = lore_root / ".lore" / "hook-log-failed.marker"
    if marker.exists():
        mtime = datetime.fromtimestamp(marker.stat().st_mtime, tz=UTC)
        age = datetime.now(UTC) - mtime
        if age < timedelta(days=1):
            lines.append(
                f"  ✗ Hook log write failed {_relative(mtime.isoformat().replace('+00:00', 'Z'))} "
                f"— check disk space / permissions on {lore_root / '.lore'}"
            )

    if not any_data:
        lines.append("  No capture activity yet")
    return lines


def _last_json_line(path: Path) -> dict | None:
    for line in reversed(path.read_text().splitlines()):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def _relative(ts_iso: str) -> str:
    if not ts_iso:
        return "?"
    try:
        ts = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    except ValueError:
        return ts_iso
    delta = datetime.now(UTC) - ts
    s = delta.total_seconds()
    if s < 60: return "just now"
    if s < 3600: return f"{int(s//60)}m ago"
    if s < 86400: return f"{int(s//3600)}h ago"
    return f"{int(s//86400)}d ago"
```

Hook the panel into the existing `doctor()` command (find the place where checks print, and append after the last existing check):

```python
# in doctor_cmd.py's main doctor function, after existing checks
for line in run_capture_panel(lore_root):
    console.print(line)
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_doctor.py -v`
Expected: all PASS.

- [ ] **Step 5: Manual smoke test**

Run: `lore doctor`
Expected: Capture pipeline section appears at the bottom with the right signals.

- [ ] **Step 6: Commit**

```bash
git add lib/lore_cli/doctor_cmd.py tests/test_doctor.py
git commit -m "feat(doctor): add Capture pipeline panel"
```

---

### Task 15: Retention cleanup

**Files:**
- Modify: `lib/lore_core/run_log.py` (add cleanup call in `__exit__`)
- Create: `lib/lore_core/run_retention.py` (the cleanup logic)
- Test: `tests/test_run_retention.py`

**Goal:** At the end of each run, enforce:
- Keep ≤ 200 runs OR ≤ 100 MB total, whichever hits first (delete oldest FIFO)
- Keep ≤ 30 `.trace.jsonl` companions
- Delete orphan `.trace.jsonl` (parent `.jsonl` gone) in same pass
- Skip files with open handles (best-effort)

- [ ] **Step 1: Write retention tests**

```python
# tests/test_run_retention.py
import json
from pathlib import Path

from lore_core.run_retention import enforce_retention


def _mk_run(dir_: Path, name: str, size: int = 500) -> Path:
    p = dir_ / f"{name}.jsonl"
    p.write_text("x" * size + "\n")
    return p


def test_count_cap_deletes_oldest(tmp_path):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    for i in range(205):
        _mk_run(runs, f"2026-04-{i:02d}T10-00-00-xxxxxx")
    enforce_retention(tmp_path, keep=200, max_total_mb=9999, keep_trace=30)
    remaining = list(runs.glob("*.jsonl"))
    assert len(remaining) == 200


def test_mb_cap_deletes_until_under_cap(tmp_path):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    # 50 files at 2 MB each = 100 MB total, then add more to push over.
    for i in range(60):
        _mk_run(runs, f"2026-04-{i:02d}T10-00-00-xxxxxx", size=2_100_000)
    enforce_retention(tmp_path, keep=1000, max_total_mb=100, keep_trace=30)
    remaining = list(runs.glob("*.jsonl"))
    total = sum(p.stat().st_size for p in remaining)
    assert total <= 100 * 1024 * 1024


def test_orphan_trace_deleted(tmp_path):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    _mk_run(runs, "2026-04-20T10-00-00-keeper")
    (runs / "2026-04-20T10-00-00-orphan.trace.jsonl").write_text("x\n")
    # No 2026-04-20T10-00-00-orphan.jsonl
    enforce_retention(tmp_path, keep=200, max_total_mb=9999, keep_trace=30)
    assert not (runs / "2026-04-20T10-00-00-orphan.trace.jsonl").exists()
    assert (runs / "2026-04-20T10-00-00-keeper.jsonl").exists()


def test_trace_cap_deletes_oldest_trace(tmp_path):
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    for i in range(35):
        name = f"2026-04-{i:02d}T10-00-00-xxxxxx"
        _mk_run(runs, name)
        (runs / f"{name}.trace.jsonl").write_text("x\n")
    enforce_retention(tmp_path, keep=1000, max_total_mb=9999, keep_trace=30)
    trace_files = list(runs.glob("*.trace.jsonl"))
    assert len(trace_files) == 30


def test_retention_skips_open_files(tmp_path):
    import os
    runs = tmp_path / ".lore" / "runs"
    runs.mkdir(parents=True)
    for i in range(205):
        _mk_run(runs, f"2026-04-{i:02d}T10-00-00-xxxxxx")
    oldest = runs / "2026-04-00T10-00-00-xxxxxx.jsonl"
    fd = os.open(oldest, os.O_RDONLY)
    try:
        enforce_retention(tmp_path, keep=200, max_total_mb=9999, keep_trace=30)
        assert oldest.exists(), "open file should have been skipped"
    finally:
        os.close(fd)
```

- [ ] **Step 2: Confirm fail**

Run: `pytest tests/test_run_retention.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement retention**

```python
# lib/lore_core/run_retention.py
"""Lazy retention cleanup for run logs.

Invoked at the end of each Curator run (from RunLogger.__exit__).
Best-effort — never raises; skips files with open handles on
platforms that signal this.
"""

from __future__ import annotations

import os
from pathlib import Path


def _is_open_on_another_process(path: Path) -> bool:
    """Best-effort open-handle detection.

    On POSIX we can't generally tell — return False and rely on lsof
    at the doctor level. This hook is here so Windows can plug in
    specific behavior if needed.
    """
    return False


def _safe_unlink(path: Path) -> bool:
    """Return True iff path was deleted."""
    if _is_open_on_another_process(path):
        return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return True  # gone already; count as success
    except OSError:
        # Windows: unlink fails if file open. Mac/Linux: probably perms.
        return False


def enforce_retention(
    lore_root: Path,
    *,
    keep: int,
    max_total_mb: int,
    keep_trace: int,
) -> None:
    runs = lore_root / ".lore" / "runs"
    if not runs.exists():
        return

    archival = sorted(
        (p for p in runs.glob("*.jsonl") if not p.name.endswith(".trace.jsonl")),
        key=lambda p: p.name,
    )
    trace = sorted(runs.glob("*.trace.jsonl"), key=lambda p: p.name)

    # 1) Count cap on archival (delete oldest; delete matching .trace.jsonl too).
    while len(archival) > keep:
        victim = archival[0]
        if _safe_unlink(victim):
            archival.pop(0)
            t = victim.with_suffix(".trace.jsonl")  # wrong — need the stem + .trace.jsonl
            # with_suffix replaces only last extension — use explicit path.
            t2 = runs / (victim.stem + ".trace.jsonl")
            if t2.exists():
                _safe_unlink(t2)
                if t2 in trace:
                    trace.remove(t2)
        else:
            break

    # 2) MB cap on archival.
    max_bytes = max_total_mb * 1024 * 1024
    def _total():
        return sum(p.stat().st_size for p in archival if p.exists())
    while archival and _total() > max_bytes:
        victim = archival[0]
        if _safe_unlink(victim):
            archival.pop(0)
            t2 = runs / (victim.stem + ".trace.jsonl")
            if t2.exists():
                _safe_unlink(t2)
                if t2 in trace:
                    trace.remove(t2)
        else:
            break

    # 3) Orphan trace cleanup.
    archival_stems = {p.stem for p in archival}
    trace_live: list[Path] = []
    for t in trace:
        stem = t.name[:-len(".trace.jsonl")]
        if stem not in archival_stems:
            _safe_unlink(t)
        else:
            trace_live.append(t)

    # 4) Trace cap.
    while len(trace_live) > keep_trace:
        victim = trace_live[0]
        if _safe_unlink(victim):
            trace_live.pop(0)
        else:
            break
```

- [ ] **Step 4: Wire retention into `RunLogger.__exit__`**

In `lib/lore_core/run_log.py`, before the `__exit__` returns (after the `run-end` emit), call retention:

```python
def __exit__(self, exc_type, exc, tb):
    # ... existing emit of 'error' (if exc) and 'run-end'
    try:
        from lore_core.run_retention import enforce_retention
        from lore_core.root_config import load_root_config
        cfg = load_root_config(self._lore_root).observability.runs
        enforce_retention(
            self._lore_root,
            keep=cfg.keep,
            max_total_mb=cfg.max_total_mb,
            keep_trace=cfg.keep_trace,
        )
    except Exception:
        # Retention is best-effort.
        pass
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_run_retention.py -v`
Expected: 5 PASS.

- [ ] **Step 6: Run the full RunLogger suite to confirm no regression**

Run: `pytest tests/test_run_log.py -v`
Expected: all still PASS.

- [ ] **Step 7: Commit**

```bash
git add lib/lore_core/run_retention.py lib/lore_core/run_log.py tests/test_run_retention.py
git commit -m "feat(core): add run-log retention with count, MB, orphan, and open-file safety"
```

---

## Phase E — Integration & docs

### Task 16: End-to-end integration test

**Files:**
- Create: `tests/test_auto_diagnostics_e2e.py`

**Goal:** One happy-path test that exercises the full stack: hook → spawn-curator → run-log → reader → renderer. Uses the existing fixture infrastructure plus a fake anthropic client.

- [ ] **Step 1: Write the E2E test**

```python
# tests/test_auto_diagnostics_e2e.py
"""End-to-end: hook -> curator -> run-log -> lore runs show."""

import json
from pathlib import Path
from typer.testing import CliRunner


def test_e2e_capture_to_runs_show(tmp_path, attached_wiki, fake_anthropic_noteworthy_true):
    """Seed a pending transcript, fire the hook, wait for curator, inspect via lore runs."""
    from lore_cli import hooks, runs_cmd
    from lore_curator.curator_a import run_curator_a

    # Arrange: fixture already attached tmp_path as a wiki with one pending transcript.
    # Invoke capture (threshold=1 → spawns curator).
    hooks.capture(event="session-end", cwd_override=attached_wiki.cwd, host="fake")

    # Instead of waiting for the detached subprocess, run curator synchronously.
    run_curator_a(lore_root=attached_wiki.lore_root,
                  anthropic_client=fake_anthropic_noteworthy_true)

    # Assert a run file exists.
    runs = list((attached_wiki.lore_root / ".lore" / "runs").glob("*.jsonl"))
    archival = [p for p in runs if not p.name.endswith(".trace.jsonl")]
    assert len(archival) == 1

    # Assert hook-events.jsonl has the session-end entry.
    events = attached_wiki.lore_root / ".lore" / "hook-events.jsonl"
    assert events.exists()
    records = [json.loads(l) for l in events.read_text().splitlines()]
    assert any(r["event"] == "session-end" for r in records)

    # Assert `lore runs show latest` succeeds and mentions the filed note.
    runner = CliRunner()
    import lore_cli.runs_cmd as rc
    rc._get_lore_root = lambda: attached_wiki.lore_root
    result = runner.invoke(runs_cmd.app, ["show", "latest"])
    assert result.exit_code == 0
    assert "filed" in result.stdout.lower() or "new" in result.stdout.lower()
```

`attached_wiki` and `fake_anthropic_noteworthy_true` are fixtures — find the closest existing analog in `tests/test_hooks_capture.py` and `tests/test_curator_a.py` and either reuse via `conftest.py` or copy-adapt.

- [ ] **Step 2: Run**

Run: `pytest tests/test_auto_diagnostics_e2e.py -v`
Expected: PASS.

- [ ] **Step 3: Full test suite to confirm no regression**

Run: `pytest -q`
Expected: full suite PASSES.

- [ ] **Step 4: Commit**

```bash
git add tests/test_auto_diagnostics_e2e.py
git commit -m "test: add end-to-end hook→curator→runs-show integration test"
```

---

### Task 17: README Observability section

**Files:**
- Modify: `README.md`

**Goal:** Document the three new commands and the two scenarios most users will hit. No deep-dive — one section, the scenario table, link to spec.

- [ ] **Step 1: Append an Observability section to `README.md`**

```markdown
## Observability

The capture pipeline writes structured logs so you can inspect what it did — and
why. Three commands cover the common scenarios:

| Scenario | Command |
|---|---|
| "I had a session and no note appeared" | `lore runs show latest` |
| "Hook plumbing feels off" | `lore doctor` |
| "I'm tuning noteworthy/merge config" | `lore curator run --dry-run --trace-llm` |

`lore runs list` prints a table of recent curator runs. `lore runs show <id>`
accepts the alias `latest`, carets `^1`..`^N`, the 6-char random suffix
(e.g. `a1b2c3`), or any unique prefix of the full ID.

Logs live under `$LORE_ROOT/.lore/`:

- `hook-events.jsonl` — one line per hook invocation
- `runs/<id>.jsonl` — one file per curator run (decision trace)
- `runs/<id>.trace.jsonl` — optional LLM prompt/response trace
  (enabled by `LORE_TRACE_LLM=1` or `--trace-llm`)

Retention is count + MB capped; configure at `$LORE_ROOT/.lore/config.yml`:

```yaml
observability:
  hook_events:
    max_size_mb: 10
    keep_rotations: 1
  runs:
    keep: 200
    max_total_mb: 100
    keep_trace: 30
```

Full design: [`docs/superpowers/specs/2026-04-20-auto-session-diagnostics-design.md`](docs/superpowers/specs/2026-04-20-auto-session-diagnostics-design.md).
```

Place this section after the existing passive-capture sections so the reader sees it in the natural flow.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add Observability section to README"
```

---

## Self-review checklist (for the implementer)

After each commit, verify:

1. **All tests pass:** `pytest -q`
2. **Hot path still <100 ms:** `time lore hook capture --event session-end --cwd <attached>` — sanity check. If the hook suddenly takes 300 ms, the HookEventLogger is doing something wrong (e.g., fsync, blocking flock).
3. **No silent breakage in existing tests:** particularly `test_hooks_capture.py`, `test_curator_a.py`, `test_breadcrumb.py`, `test_doctor.py`, `test_cli_curator_run.py`.
4. **Scenarios from the spec work end-to-end:**
   - A: `lore runs show latest` after a session that filed a note
   - B: `lore doctor` with a recent hook-error seeded
   - C: `lore curator run --dry-run --trace-llm` followed by `lore runs show latest --verbose`

## Success criteria (from spec)

Phase 1 is done when:

1. ✅ Every silent-failure mode from the spec's audit table has a path to user visibility.
2. ✅ Scenario A answered by a single `lore runs show latest` invocation.
3. ✅ Scenario B answered by `lore doctor`.
4. ✅ Scenario C answered by `lore curator run --dry-run --trace-llm` + `lore runs show latest --verbose`.
5. ✅ Hook hot-path stays <100 ms.
6. ✅ All unit and integration tests pass.
7. ✅ Disk use bounded by retention config.
