# Passive Capture MVP — Implementation Plan (Plan 1 of 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Per-step TDD detail is expected to be expanded by the executing subagent using current repo state.

**Goal:** After a Claude Code session ends, a session note appears automatically in the attached wiki — without the user typing `/lore:session`. End-to-end working capture path.

**Architecture:** Hook-driven capture (SessionEnd / PreCompact / SessionStart-sweep) → sidecar ledger with content-hash watermarks → detached Curator A (noteworthy filter + session-note writer) → session note in vault with `draft: true`. All via `Turn` normalisation from a `claude-code` adapter (Claude Agent SDK) plus a `manual-send` CLI fallback. Attached-folders-only; everything else is silent.

**Tech Stack:** Python 3.11+, typer + rich (existing), pyyaml (existing), `claude-agent-sdk` (new), `anthropic` SDK (new, for Curator A's middle-tier LLM call), pytest (existing).

**Spec reference:** `docs/superpowers/specs/2026-04-19-passive-capture-v1-design.md`

**Non-blocker items to surface during execution** (carried from senior-architect review):
1. Name `Surface`, `Scope`, and a `BlastRadius` enum as explicit code types when they first appear (Task 1 / 4).
2. `host_extras` coupling — add a registry-of-recognised-keys before opening to third-party adapters (post-MVP, flag in Task 2).
3. Drain compaction atomic-rewrite under curator lockfile — Plan 3 concern (drain doesn't land in this plan).
4. Verify mtime-guard covers git-merge-induced mtime bumps when `auto_pull: true` — Task 9 (lockfile / mtime-guard).

**Phases:**
- **A. Foundations** (Tasks 1–5): types, protocol, ledger, scope resolver, redaction.
- **B. Adapters** (Tasks 6–8): claude-code, manual-send, registry.
- **C. Curator A** (Tasks 9–13): lockfile, config loader, noteworthy filter, session-note writer, pipeline.
- **D. CLI + wiring + integration** (Tasks 14–18): hooks, CLI commands, banner, plugin wiring, E2E test.

Each task is independently committable. Run existing tests after every commit: `pytest -q`.

---

## Phase A — Foundations

### Task 1: Core types

**Files:**
- Create: `lib/lore_core/types.py`
- Test: `tests/test_types.py`

**Goal:** Define `Turn`, `TranscriptHandle`, `ToolCall`, `ToolResult`, `BlastRadius`, `Scope` as frozen dataclasses / enums. These are the common vocabulary every downstream module speaks.

**Key signatures:**

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

Role = Literal["user", "assistant", "system", "tool_result"]

@dataclass(frozen=True)
class ToolCall:
    name: str
    input: dict[str, Any]
    id: str | None = None

@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str | None
    output: str
    is_error: bool = False

@dataclass(frozen=True)
class Turn:
    index: int
    timestamp: datetime | None
    role: Role
    text: str | None = None
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    reasoning: str | None = None
    host_extras: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        """sha256 of role + text + tool_call.input (deterministic)."""
        ...

@dataclass(frozen=True)
class TranscriptHandle:
    host: str
    id: str
    path: Path
    cwd: Path
    mtime: datetime

@dataclass(frozen=True)
class Scope:
    wiki: str
    scope: str                  # colon-separated
    backend: str                # "github" | "none"
    claude_md_path: Path

class BlastRadius(Enum):
    CREATE = "create"           # draft: true new note — safe
    EDIT_FRONTMATTER = "edit-fm"  # frontmatter-only — safe
    EDIT_BODY = "edit-body"     # body changes — medium
    SUPERSEDE = "supersede"     # Curator C — highest
```

**Acceptance:**
- `test_turn_content_hash_is_deterministic` — same input → same hash.
- `test_turn_content_hash_differs_on_text_change`.
- `test_types_are_frozen` — `pytest.raises(FrozenInstanceError)`.
- `test_blast_radius_enum_values`.

**Commit:** `feat(core): add Turn/TranscriptHandle/Scope/BlastRadius types`

---

### Task 2: Adapter protocol

**Files:**
- Create: `lib/lore_adapters/__init__.py`
- Create: `lib/lore_adapters/protocol.py`
- Test: `tests/test_adapter_protocol.py`

**Goal:** Define the `Adapter` Protocol that every host adapter implements. `host_extras` gets a docstring noting it's currently debug-only — registry of recognised keys deferred until third-party adapters land.

**Key signatures:**

```python
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, runtime_checkable

from lore_core.types import Turn, TranscriptHandle

@runtime_checkable
class Adapter(Protocol):
    host: str                   # class-level attribute: "claude-code", "cursor", ...

    def list_transcripts(self, directory: Path) -> list[TranscriptHandle]:
        """Return transcripts whose session cwd equals `directory`."""

    def read_slice(
        self,
        handle: TranscriptHandle,
        from_index: int = 0,
    ) -> Iterator[Turn]:
        """Stream turns from `from_index`. Indices are monotonic."""

    def read_slice_after_hash(
        self,
        handle: TranscriptHandle,
        after_hash: str | None,
        index_hint: int | None = None,
    ) -> Iterator[Turn]:
        """Stream turns after the turn with `content_hash == after_hash`.

        Starts at `index_hint` if provided; verifies the hash at that
        position; falls back to content scan on mismatch. If `after_hash`
        is None, streams from start.
        """

    def is_complete(self, handle: TranscriptHandle) -> bool:
        """True if the transcript's session has ended."""
```

**Acceptance:**
- `test_protocol_has_runtime_check` — `isinstance(stub, Adapter)` works.
- `test_protocol_required_methods` — a class missing `read_slice_after_hash` fails `isinstance`.

**Commit:** `feat(adapters): define Adapter protocol with hash-watermark read`

---

### Task 3: Sidecar ledger

**Files:**
- Create: `lib/lore_core/ledger.py`
- Test: `tests/test_ledger.py`

**Goal:** Transcript-level + wiki-level sidecar JSON at `$LORE_ROOT/.lore/transcript-ledger.json` and `$LORE_ROOT/.lore/wiki-<name>-ledger.json`. Content-hash watermarks. Atomic writes via existing `lore_core.io.atomic_write_text`. Pending-transcript enumeration. First-come-wins update pattern (lock required — see Task 9).

**Key API:**

```python
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

@dataclass
class TranscriptLedgerEntry:
    host: str
    transcript_id: str
    path: Path
    directory: Path
    digested_hash: str | None
    digested_index_hint: int | None
    synthesised_hash: str | None
    last_mtime: datetime
    curator_a_run: datetime | None
    noteworthy: bool | None
    session_note: str | None    # wikilink, e.g. "[[2026-04-19-slug]]"

class TranscriptLedger:
    def __init__(self, lore_root: Path): ...
    def get(self, host: str, transcript_id: str) -> TranscriptLedgerEntry | None: ...
    def upsert(self, entry: TranscriptLedgerEntry) -> None: ...
    def pending(self) -> list[TranscriptLedgerEntry]:
        """Entries where last_mtime > any digested state, or digested_hash is None."""
    def advance(self, host: str, transcript_id: str, *,
                digested_hash: str, digested_index_hint: int,
                noteworthy: bool, session_note: str | None) -> None: ...

@dataclass
class WikiLedgerEntry:
    wiki: str
    last_curator_a: datetime | None
    last_curator_b: datetime | None
    last_briefing: datetime | None
    pending_transcripts: int
    pending_tokens_est: int
```

**Acceptance:**
- `test_ledger_empty_pending_on_fresh_lore_root`.
- `test_ledger_upsert_then_get`.
- `test_ledger_pending_returns_mtime_gt_digested`.
- `test_ledger_advance_updates_hash_and_hint`.
- `test_ledger_atomic_write_survives_concurrent_read` (spawn thread reading while writing).

**Commit:** `feat(core): add sidecar transcript + wiki ledger with hash watermarks`

---

### Task 4: Scope resolver

**Files:**
- Create: `lib/lore_core/scope_resolver.py`
- Test: `tests/test_scope_resolver.py`

**Goal:** Given a cwd, walk up the filesystem to find `CLAUDE.md` containing a `## Lore` block; return a `Scope` (from Task 1) or `None`. Reuses the existing `lore_cli.attach_cmd.read_attach` reader.

**Key API:**

```python
from pathlib import Path
from lore_core.types import Scope

def resolve_scope(cwd: Path) -> Scope | None:
    """Walk up from cwd until a CLAUDE.md with `## Lore` is found.

    Returns None if no attach block is reachable.
    """
```

**Acceptance:**
- `test_resolve_finds_direct_parent_claude_md` (tmp_path fixture with `## Lore` block).
- `test_resolve_walks_multiple_levels`.
- `test_resolve_returns_none_when_no_attach`.
- `test_resolve_respects_nearest_attach` (nested attach wins over ancestor).

**Commit:** `feat(core): add scope resolver walking up for CLAUDE.md ## Lore`

---

### Task 5: Secret redaction

**Files:**
- Create: `lib/lore_core/redaction.py`
- Test: `tests/test_redaction.py`

**Goal:** Deterministic best-effort scrub of common secret patterns before transcript content leaves lore (LLM calls, note writes). Pattern list is explicit and versioned. Redaction log appended to `vault/.lore/redaction.log`.

**Patterns:**
- API keys: `sk-[A-Za-z0-9_-]{20,}`, `ghp_[A-Za-z0-9]{36}`, `AIza[0-9A-Za-z_-]{35}`, `xoxb-…`, `sk-ant-[…]`
- AWS: `AKIA[0-9A-Z]{16}`
- JWT: three `.`-separated base64url segments, total length > 60
- PEM: `-----BEGIN [A-Z ]+PRIVATE KEY-----` (block capture through end-marker)
- High-entropy after key-like identifier: `(?i)\b(password|secret|token|api[_-]?key)\s*[:=]\s*["']?([A-Za-z0-9/+_-]{24,})` — redact group 2 only if Shannon entropy > 4.0.

**Key API:**

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass
class RedactionHit:
    kind: str                   # "sk-api-key" | "jwt" | "pem-private-key" | ...
    start: int
    end: int
    preview: str                # first 6 chars + "…" for the log

def redact(text: str, *, log_path: Path | None = None) -> tuple[str, list[RedactionHit]]:
    """Replace detections with [REDACTED:<kind>] markers.

    If `log_path` provided, append one JSONL entry per hit.
    """
```

**Acceptance:**
- `test_redaction_catches_sk_key`, `test_redaction_catches_ghp_key`, etc. — one per pattern.
- `test_redaction_preserves_non_secret_text`.
- `test_redaction_entropy_gate_on_password_context` — low-entropy `password=hunter2` NOT redacted, high-entropy `password=<random40>` redacted.
- `test_redaction_log_appends_jsonl`.

**Commit:** `feat(core): add best-effort secret redaction pre-pass`

---

## Phase B — Adapters

### Task 6: Claude Code adapter

**Files:**
- Create: `lib/lore_adapters/claude_code.py`
- Modify: `pyproject.toml` (add `claude-agent-sdk` to a new optional-deps group `capture`)
- Test: `tests/test_adapter_claude_code.py` (with SDK mocked)
- Test: `tests/test_adapter_claude_code_live.py` (integration, skipped unless SDK installed)

**Goal:** Adapter implementation using Claude Agent SDK's `list_sessions(directory=...)` and `get_session_messages(...)`. Raw-JSONL parsing is a fallback mode; for v1 ship SDK-only and raise a clear error if SDK is absent.

**Key outline:**

```python
from pathlib import Path
from collections.abc import Iterator

from lore_core.types import Turn, TranscriptHandle, ToolCall, ToolResult

class ClaudeCodeAdapter:
    host = "claude-code"

    def list_transcripts(self, directory: Path) -> list[TranscriptHandle]:
        from claude_agent_sdk import list_sessions
        out = []
        for session in list_sessions(directory=directory):
            out.append(TranscriptHandle(
                host=self.host,
                id=session.id,
                path=Path(session.path),
                cwd=Path(directory),
                mtime=session.mtime,
            ))
        return out

    def read_slice_after_hash(self, handle, after_hash, index_hint=None):
        all_turns = list(self._iter_turns(handle))
        if after_hash is None:
            yield from all_turns
            return
        # Try index_hint first
        if index_hint is not None and index_hint < len(all_turns):
            if all_turns[index_hint].content_hash() == after_hash:
                yield from all_turns[index_hint + 1:]
                return
        # Fallback: content scan
        for i, t in enumerate(all_turns):
            if t.content_hash() == after_hash:
                yield from all_turns[i + 1:]
                return
        # Hash not found — host mutated; yield everything with a warning
        yield from all_turns

    def read_slice(self, handle, from_index=0):
        for t in self._iter_turns(handle):
            if t.index >= from_index:
                yield t

    def is_complete(self, handle):
        # SDK's ResultMessage marks session end
        ...

    def _iter_turns(self, handle: TranscriptHandle) -> Iterator[Turn]:
        """Normalise SDK messages to Turn. Thinking → reasoning; tool_use → tool_call; tool_result → tool_result role."""
        ...
```

**Acceptance:**
- `test_list_transcripts_returns_handles` (SDK mocked).
- `test_read_slice_after_hash_uses_hint_when_valid`.
- `test_read_slice_after_hash_falls_back_on_mismatch`.
- `test_iter_turns_normalises_thinking_to_reasoning`.
- `test_iter_turns_normalises_tool_use_tool_result`.
- **Live integration test** (skipped unless `CLAUDE_AGENT_SDK_INTEGRATION=1`): read a real transcript from `~/.claude/projects/`, assert non-empty turn stream.

**Commit:** `feat(adapters): add claude-code adapter via claude-agent-sdk`

---

### Task 7: Manual-send adapter

**Files:**
- Create: `lib/lore_adapters/manual_send.py`
- Test: `tests/test_adapter_manual_send.py`

**Goal:** Adapter that reads a transcript file (or stdin) dumped by a user from any host without a native adapter. Accepts a minimal JSONL shape; emits `Turn`s with `host = manual-send` and `host_extras = {"original_host": <declared>}`.

**Input shape (JSONL, one turn per line):**

```json
{"index": 0, "role": "user", "text": "hi"}
{"index": 1, "role": "assistant", "text": "hello", "reasoning": "..."}
{"index": 2, "role": "assistant", "tool_call": {"name": "Read", "input": {...}}}
```

**Key outline:**

```python
class ManualSendAdapter:
    host = "manual-send"

    def read_from(self, source: Path | "TextIO", cwd: Path, *,
                  declared_host: str = "unknown") -> Iterator[Turn]:
        """Parse JSONL into Turn stream. Validates required fields."""

    # Protocol methods:
    def list_transcripts(self, directory: Path) -> list[TranscriptHandle]:
        return []               # manual-send never auto-discovers

    def read_slice_after_hash(self, handle, after_hash, index_hint=None):
        ...
    def read_slice(self, handle, from_index=0): ...
    def is_complete(self, handle) -> bool: return True
```

**Acceptance:**
- `test_manual_send_parses_jsonl_stream`.
- `test_manual_send_rejects_missing_required_fields`.
- `test_manual_send_preserves_declared_host_in_extras`.

**Commit:** `feat(adapters): add manual-send adapter for user-dumped transcripts`

---

### Task 8: Adapter registry

**Files:**
- Create: `lib/lore_adapters/registry.py`
- Modify: `lib/lore_adapters/__init__.py` (re-export `get_adapter`)
- Test: `tests/test_adapter_registry.py`

**Goal:** Map `host` string → `Adapter` instance. Ship with `claude-code` and `manual-send` registered; leave an entry-points hook commented-in-code for future third-party adapters.

**Key API:**

```python
from lore_adapters.protocol import Adapter

def get_adapter(host: str) -> Adapter:
    """Return a registered adapter or raise UnknownHostError."""

def registered_hosts() -> list[str]: ...

class UnknownHostError(KeyError): ...
```

**Acceptance:**
- `test_registry_returns_claude_code`.
- `test_registry_returns_manual_send`.
- `test_registry_unknown_host_raises`.
- `test_registered_hosts_lists_v1_set`.

**Commit:** `feat(adapters): add registry + ship claude-code and manual-send`

---

## Phase C — Curator A

### Task 9: Lockfile utility

**Files:**
- Create: `lib/lore_core/lockfile.py`
- Test: `tests/test_lockfile.py`

**Goal:** Atomic `mkdir`-based lockfile at `$LORE_ROOT/.lore/curator.lock`. Context manager with timeout and stale-detection (removes locks older than N minutes). Same mtime-guard pattern used by existing `lore_curator.core` — verify it handles git-merge-induced mtime bumps when `auto_pull: true` (add a test that simulates `git pull` touching the lockfile's parent).

**Key API:**

```python
from contextlib import contextmanager
from pathlib import Path

class LockContendedError(Exception): ...

@contextmanager
def curator_lock(lore_root: Path, *, timeout: float = 0, stale_after: float = 3600):
    """Atomic mkdir lock. Raises LockContendedError on contention (timeout=0)
    or waits up to `timeout` seconds. Removes locks older than `stale_after`.
    """
```

**Acceptance:**
- `test_lock_acquires_and_releases`.
- `test_lock_contended_raises_when_timeout_zero`.
- `test_lock_waits_and_acquires_when_timeout_positive`.
- `test_stale_lock_reclaimed_after_timeout`.
- `test_lock_unaffected_by_git_pull_mtime_bump` (touch parent dir, assert lock still valid).

**Commit:** `feat(core): add curator lockfile with stale detection`

---

### Task 10: Per-wiki config loader

**Files:**
- Create: `lib/lore_core/wiki_config.py`
- Test: `tests/test_wiki_config.py`

**Goal:** Load `$LORE_ROOT/wiki/<name>/.lore-wiki.yml` with sane defaults matching the spec §8 block. Structured dataclass output; invalid keys warn but don't crash (forward-compat).

**Key API:**

```python
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class GitConfig:
    auto_commit: bool = True
    auto_push: bool = False
    auto_pull: bool = True

@dataclass
class CuratorCConfig:
    enabled: bool = False
    mode: str = "local"         # local | central

@dataclass
class CuratorConfig:
    threshold_pending: int = 3
    threshold_tokens: int = 50_000
    a_noteworthy_tier: str = "middle"   # middle | simple
    curator_c: CuratorCConfig = field(default_factory=CuratorCConfig)

@dataclass
class ModelsConfig:
    simple: str = "claude-haiku-4-5"
    middle: str = "claude-sonnet-4-6"
    high: str = "claude-opus-4-7"   # or "off"

@dataclass
class BreadcrumbConfig:
    mode: str = "normal"        # quiet | normal | verbose
    scope_filter: bool = True

@dataclass
class WikiConfig:
    git: GitConfig = field(default_factory=GitConfig)
    curator: CuratorConfig = field(default_factory=CuratorConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    breadcrumb: BreadcrumbConfig = field(default_factory=BreadcrumbConfig)

def load_wiki_config(wiki_dir: Path) -> WikiConfig: ...
```

**Acceptance:**
- `test_load_defaults_on_missing_file`.
- `test_load_partial_yaml_merges_with_defaults`.
- `test_load_curator_c_enabled_parsed`.
- `test_load_unknown_key_warns_does_not_crash`.

**Commit:** `feat(core): add per-wiki config loader (.lore-wiki.yml)`

---

### Task 11: Noteworthy filter

**Files:**
- Create: `lib/lore_curator/noteworthy.py`
- Test: `tests/test_noteworthy.py`

**Goal:** Given a slice of `Turn`s (post-redaction), call the middle-tier LLM with a structured-output prompt returning `noteworthy: bool` + summary. On simple-tier opt-in, emit a first-run warning banner via `sys.stderr`.

**Prompt contract (structured output via Anthropic SDK's tool-use or JSON mode):**

```python
@dataclass
class NoteworthyResult:
    noteworthy: bool
    reason: str                 # short: "single-shot bash question" | "substantive refactor"
    title: str                  # 5-10 words
    bullets: list[str]          # 3-5 items, each a short phrase
    files_touched: list[str]
    entities: list[str]         # wikilink candidates
    decisions: list[str]        # one-liners

def classify_slice(
    turns: list[Turn],
    *,
    tier: str = "middle",       # "middle" | "simple"
    model_resolver: "Callable[[str], str]",
    anthropic_client: "anthropic.Anthropic",
) -> NoteworthyResult: ...
```

**Implementation notes:**
- Anthropic SDK call uses tool-use for structured output (schema above).
- On `tier="simple"`, log a one-time warning per session to `$LORE_ROOT/.lore/warnings.log`: *"Using simple tier for noteworthy filter — some substantive session slices may be silently dropped."*
- Tool results in turns are truncated to `<tool {name} returned {n} lines>` before feeding the LLM.
- Thinking blocks dropped entirely in the prompt (stay in Turn objects).

**Acceptance (with LLM mocked via a fake `anthropic_client`):**
- `test_classify_returns_noteworthy_true_for_substantive_slice`.
- `test_classify_returns_noteworthy_false_for_trivial_slice`.
- `test_classify_truncates_long_tool_results_in_prompt`.
- `test_classify_drops_thinking_blocks_from_prompt`.
- `test_simple_tier_writes_warning_once_per_session`.

**Commit:** `feat(curator): add noteworthy filter with middle-tier default`

---

### Task 12: Session-note writer / merger

**Files:**
- Create: `lib/lore_curator/session_filer.py`
- Test: `tests/test_session_filer.py`

**Goal:** Given a `NoteworthyResult` + full `Turn` slice + existing recent session notes in scope → either create a new session note or merge into an existing one. Uses `session-note-schema-v2` frontmatter, writes `draft: true`, records `source_transcripts` with hash watermarks.

**Merge judgment:** a second middle-tier call with (new slice's summary) + (recent notes' frontmatter + first 40 lines of body, last 7 days in scope). Returns `{"merge": "<wikilink>"}` or `{"new": true}`.

**Key API:**

```python
from pathlib import Path
from lore_core.types import Turn, Scope
from lore_curator.noteworthy import NoteworthyResult

@dataclass
class FiledNote:
    path: Path
    wikilink: str
    was_merge: bool

def file_session_note(
    *,
    scope: Scope,
    handle: TranscriptHandle,
    noteworthy: NoteworthyResult,
    turns: list[Turn],
    recent_notes_dir: Path,
    anthropic_client,
    model_resolver,
) -> FiledNote: ...
```

**Frontmatter written:**

```yaml
---
schema_version: 2
type: session
created: 2026-04-19
last_reviewed: 2026-04-19
description: "{noteworthy.title}"
scope: "{scope.scope}"
draft: true
curator_a_run: 2026-04-19T12:00:00Z
source_transcripts:
  - host: claude-code
    id: "{handle.id}"
    from_hash: "{first_turn.content_hash()}"
    to_hash: "{last_turn.content_hash()}"
tags: []
---
```

**Acceptance:**
- `test_file_new_session_note_creates_file_with_frontmatter`.
- `test_file_draft_true_on_new_note`.
- `test_merge_judgment_returns_new_when_no_recent_notes`.
- `test_merge_judgment_merges_into_recent_continuation` (mocked LLM).
- `test_merge_appends_section_and_bumps_mtime`.
- `test_source_transcripts_hashes_recorded`.

**Commit:** `feat(curator): add session-note writer with merge-or-create judgment`

---

### Task 13: Curator A pipeline

**Files:**
- Create: `lib/lore_curator/curator_a.py`
- Test: `tests/test_curator_a.py`

**Goal:** Top-level function wiring all Phase A/B/C modules: lock → load ledger → enumerate pending → for each pending in scope, read slice after `digested_hash` → redact → noteworthy filter → if noteworthy, file session note → advance ledger. On simple-tier config, plumb the warning through.

**Key API:**

```python
def run_curator_a(
    *,
    lore_root: Path,
    scope: Scope | None = None,    # None = all scopes
    anthropic_client=None,
    adapters=None,                  # None = use global registry
    dry_run: bool = False,
) -> CuratorAResult: ...

@dataclass
class CuratorAResult:
    transcripts_considered: int
    noteworthy_count: int
    new_notes: list[Path]
    merged_notes: list[Path]
    skipped_reasons: dict[str, int]   # reason → count
    duration_seconds: float
```

**Acceptance (with mocked adapter + mocked LLM):**
- `test_curator_a_end_to_end_noteworthy_slice_produces_note`.
- `test_curator_a_non_noteworthy_slice_advances_ledger_no_file`.
- `test_curator_a_dry_run_writes_nothing`.
- `test_curator_a_respects_lock_contention` (second invocation raises).
- `test_curator_a_reuses_hash_watermark_across_runs` (second run reads after `digested_hash`).
- `test_curator_a_skips_unattached_directories`.

**Commit:** `feat(curator): add Curator A pipeline with ledger advancement`

---

## Phase D — CLI + wiring + integration

### Task 14: Hot-path hook entrypoints

**Files:**
- Modify: `lib/lore_cli/hooks.py` (extend existing `hook_app` typer)
- Test: `tests/test_hooks_capture.py`

**Goal:** Add `lore hook capture` subcommand that handles SessionEnd / PreCompact / SessionStart-sweep. Resolves scope, updates ledger, spawns detached Curator A if threshold exceeded. Returns in <100 ms (no LLM, no network, bounded FS walk N=8 levels).

**Key API:**

```python
# in lore_cli/hooks.py
@hook_app.command("capture")
def capture(
    event: str = typer.Option(..., help="session-end | pre-compact | session-start"),
    transcript: Path = typer.Option(None, help="explicit transcript path; else autodetect"),
    cwd: Path = typer.Option(None, help="explicit cwd; else CLAUDE_PROJECT_DIR or getcwd"),
) -> None: ...

def _spawn_detached_curator_a(lore_root: Path, scope: Scope) -> None:
    """subprocess.Popen with start_new_session=True (Unix) or DETACHED_PROCESS (Windows)."""
```

**Acceptance:**
- `test_capture_session_end_updates_ledger`.
- `test_capture_unattached_cwd_returns_fast_no_ledger_touch`.
- `test_capture_under_100ms` (deterministic path, mock the spawn).
- `test_capture_spawns_when_threshold_exceeded`.
- `test_capture_session_start_sweep_reports_pending_count_to_stdout` (for banner).

**Commit:** `feat(hooks): add lore hook capture for SessionEnd/PreCompact/SessionStart`

---

### Task 15: CLI commands — ingest, curator run, registry ls

**Files:**
- Create: `lib/lore_cli/ingest_cmd.py`
- Create: `lib/lore_cli/registry_cmd.py`
- Modify: `lib/lore_curator/core.py` (extend `lore curator` typer with `run` subcommand)
- Modify: `lib/lore_cli/__main__.py` (mount `ingest_cmd`, `registry_cmd`)
- Test: `tests/test_cli_ingest.py`, `tests/test_cli_registry.py`, `tests/test_cli_curator_run.py`

**Goal:** Three CLI surfaces — `lore ingest`, `lore curator run`, `lore registry {ls,show,doctor}`. All typer apps following the existing project pattern.

**Command signatures:**

```
lore ingest --from <path|-> --host <name> --directory <path> [--declared-host <name>]
lore curator run [--scope <scope>] [--dry-run]
lore registry ls [--format json|table]
lore registry show <path>
lore registry doctor
```

**Acceptance:**
- `test_ingest_reads_from_file_and_advances_ledger`.
- `test_ingest_reads_from_stdin`.
- `test_curator_run_invokes_pipeline`.
- `test_curator_run_dry_run_reports_no_writes`.
- `test_registry_ls_lists_all_attach_blocks`.
- `test_registry_show_prints_full_config_for_attach`.
- `test_registry_doctor_detects_missing_wiki`.

**Commit:** `feat(cli): add ingest, curator run, registry subcommands`

---

### Task 16: SessionStart banner

**Files:**
- Modify: `lib/lore_cli/hooks.py` (extend the existing `session-start` handler)
- Test: extend `tests/test_hooks_v2.py` with banner cases.

**Goal:** Extend the existing SessionStart hook's banner with pending-count and last-curator-run info. `lore:` prefix for events, `lore!:` prefix for actionable errors. Respects `breadcrumb.mode` from wiki config.

**Banner strings (from spec §12):**

- Pending: `lore: 3 pending · last curator 2h ago · briefing yesterday`
- Up-to-date: `lore: up to date · 47 notes in private/lore`
- Running: `lore: curator A running in background`
- Schema drift: `lore!: Cursor schema unrecognised — run lore doctor`
- Broken SURFACES.md (even if not blocking v1 Curator B, surface it): `lore!: SURFACES.md invalid — run lore surface lint`

**Rendering logic:**

```python
def render_banner(ledger: WikiLedgerEntry, cfg: WikiConfig,
                  now: datetime, attach: Scope | None) -> str | None:
    if cfg.breadcrumb.mode == "quiet":
        # errors only
        ...
    # normal / verbose …
```

**Acceptance:**
- `test_banner_pending_format`.
- `test_banner_up_to_date_when_no_pending`.
- `test_banner_quiet_mode_suppresses_non_errors`.
- `test_banner_relative_time_within_24h_uses_hours`.
- `test_banner_renders_lore_bang_prefix_on_error`.

**Commit:** `feat(hooks): extend SessionStart banner with capture state`

---

### Task 17: Claude Code plugin hook wiring

**Files:**
- Modify: the Claude Code plugin manifest / hooks config (in `skills/` or plugin metadata — locate during execution).
- Test: manual smoke test (document procedure in task comment); optional `tests/test_plugin_hooks_wiring.py` that asserts YAML structure is parseable.

**Goal:** Wire the plugin so that Claude Code's `SessionEnd`, `PreCompact`, and `SessionStart` events call `lore hook capture --event <name>` with no arguments (cwd comes from `CLAUDE_PROJECT_DIR` env; transcript autodetected).

**Per the vault's `claude-code-hook-schema.md`:** avoid `$CLAUDE_PROJECT_DIR` expansion in the hook command itself (triggers permission gate). Invoke arg-less; resolve cwd inside the hook via `os.getcwd()`.

**Acceptance:**
- Manual: start a fresh Claude Code session in a lore-attached folder, type a few messages, end the session, verify `vault/.lore/transcript-ledger.json` has a pending entry.
- Manual: wait 30 s (or reopen session), verify session note appears in the attached wiki with `draft: true`.
- Optional automated: lint the plugin hooks YAML for well-formedness.

**Commit:** `feat(plugin): wire SessionEnd/PreCompact/SessionStart to lore hook capture`

---

### Task 18: End-to-end integration test

**Files:**
- Create: `tests/test_mvp_capture_e2e.py`

**Goal:** Simulated end-to-end — no real Claude Code, but real adapters, real ledger, real redaction, real Curator A pipeline, mocked Anthropic client. Exercises the full capture path.

**Test shape:**

```python
def test_mvp_e2e_session_end_produces_note(tmp_path, fake_anthropic, monkeypatch):
    # 1. Set up tmp_path as $LORE_ROOT with private/ wiki + CLAUDE.md ## Lore attach.
    # 2. Drop a fixture JSONL into a fake "~/.claude/projects/<encoded>/<uuid>.jsonl".
    # 3. Monkeypatch the claude-code adapter's SDK calls to read from the fixture.
    # 4. Invoke `lore hook capture --event session-end --cwd <attached-cwd>`.
    # 5. Assert ledger now has a pending entry.
    # 6. Invoke `lore curator run` (mocked LLM returns noteworthy=True + title/bullets).
    # 7. Assert a session note file exists under <wiki>/sessions/YYYY-MM-DD-<slug>.md.
    # 8. Assert its frontmatter carries draft: true + source_transcripts + the hash range.
    # 9. Invoke `lore curator run` again — assert idempotent (no duplicate note).
```

**Acceptance:**
- `test_mvp_e2e_session_end_produces_note` passes.
- `test_mvp_e2e_non_noteworthy_slice_produces_no_note`.
- `test_mvp_e2e_idempotent_on_rerun`.
- `test_mvp_e2e_unattached_cwd_produces_nothing`.
- `test_mvp_e2e_manual_send_via_cli` (parallel path using `lore ingest`).

**Commit:** `test: add MVP capture end-to-end integration tests`

---

## Self-Review

**1. Spec coverage.** Walking the spec §1–§13 against tasks:

| Spec section | Task(s) |
|---|---|
| §1 Host adapters + handles | 2, 6, 7, 8 |
| §2 Sidecar ledger + hash watermarks | 3 |
| §3 Hot path triggers | 14 |
| §4 Curator A | 11, 12, 13 |
| §5 Curator B | Plan 2 |
| §6 Curator C | Plan 5 |
| §7 SURFACES.md | Plan 2 |
| §8 Per-wiki config | 10 |
| §9 Registry tooling | 15 |
| §10 Backfill | Plan 4 |
| §11 Onboarding | Plan 4 |
| §12 Breadcrumb UX | 16 (SessionStart only); drain + mid-stream = Plan 3 |
| §13 Model tier abstraction | 10 (config), 11 (tier call site) |
| Privacy / secret redaction | 5 |
| Attached-only capture | 4, 14 |
| Scope resolver (non-blocker #2) | 4 |
| BlastRadius type (non-blocker #2) | 1 |

Gap checks: no task for "Curator C on/off behaviour" (belongs to Plan 5). No drain file in Plan 1 (mid-stream deferred to Plan 3). `host_extras` registry stays deferred per spec and senior-arch feedback.

**2. Placeholder scan.** Every task has: files, a goal, key code (signatures or implementation outlines), acceptance criteria with test names, a commit message template. Per-step TDD expansion is explicitly delegated to the subagent. No "TBD" / "TODO" / "similar to task N" strings.

**3. Type consistency.** `Turn`, `TranscriptHandle`, `Scope`, `BlastRadius` defined once in Task 1; every downstream task imports from `lore_core.types`. Config types defined in Task 10, referenced in Task 16. `NoteworthyResult` defined in Task 11, consumed in Task 12. `CuratorAResult` defined in Task 13, consumed in Task 15.

**4. Non-blocker items visibility.**
- (1) `Surface`/`Scope`/`BlastRadius` as code types → Task 1 + 4. ✓
- (2) `host_extras` registry → flagged in Task 2 goal. ✓ (deferred — correct for v1).
- (3) drain compaction atomic-rewrite → noted as Plan 3 scope. ✓
- (4) mtime-guard under git pull → Task 9 acceptance criterion. ✓

---

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-04-19-passive-capture-mvp-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — A fresh subagent per task expands the TDD detail from the signatures + acceptance criteria here, writes tests first, implements, runs tests, commits. The main session reviews between tasks. Fast iteration, keeps the main context uncluttered. Uses `superpowers:subagent-driven-development`.

**2. Inline Execution** — Execute tasks in the current session using `superpowers:executing-plans`, batched with checkpoints for review.

Recommended: **1**. The 18 tasks are independent enough that fresh-subagent-per-task is ideal; the main session stays in review mode; per-task TDD fleshing happens with current repo state.
