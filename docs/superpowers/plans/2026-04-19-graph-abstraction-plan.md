# Graph Abstraction — Implementation Plan (Plan 2 of 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Per-step TDD detail expanded by the executing subagent using current repo state.

**Goal:** After session notes accumulate, a daily background pass clusters them by topic and emits per-wiki surfaces (concept / decision / result / paper / …) as `draft: true` markdown notes. Briefings auto-publish after the pass when configured.

**Architecture:** SURFACES.md per wiki declares the surface vocabulary; Curator B (sonnet for cluster, opus-or-middle-fallback for abstract) reads recent session notes, clusters by scope+topic, emits surfaces. Triggered by SessionStart-sweep on calendar-day rollover. Briefing publishes immediately after.

**Tech Stack:** Python 3.11+, typer + rich + pyyaml + anthropic (existing), `claude-agent-sdk` (existing).

**Spec reference:** `docs/superpowers/specs/2026-04-19-passive-capture-v1-design.md` §5 (Curator B), §7 (SURFACES.md), §8 (config), §13 (model tier).

**Phases:**
- **A. SURFACES.md infra** (T1–T4): parser, schema integration, CLI surface tools, new-wiki extension.
- **B. Curator B engine** (T5–T9): surface writer, cluster, abstract, pipeline, CLI.
- **C. Auto-triggers + briefing** (T10–T11).
- **D. Integration** (T12).

Each task independently committable. Run existing suite after every commit: `pytest -q`.

**Carry-over from Plan 1 review:**
- Use `attached.wiki` directly when resolving wiki dirs — don't re-parse CLAUDE.md (carried into all new code).
- All advance/upsert API additions for ledger should accept `now` for timestamping.

---

## Phase A — SURFACES.md infrastructure

### Task 1: SURFACES.md parser

**Files:**
- Create: `lib/lore_core/surfaces.py`
- Test: `tests/test_surfaces.py`

**Goal:** parse a `SURFACES.md` file with embedded YAML blocks per `## section`. Return a structured representation. Forward-compat: unknown keys warn but don't crash.

**Key API:**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

@dataclass(frozen=True)
class SurfaceDef:
    name: str                          # "concept" | "decision" | "result" | "paper" | custom
    description: str                   # body prose between heading and YAML block
    required: list[str]                # required frontmatter fields
    optional: list[str]                # optional frontmatter fields
    extract_when: str = ""             # free-text rule for the LLM ("Extract when: …")

@dataclass(frozen=True)
class SurfacesDoc:
    schema_version: int                # top-level schema_version
    surfaces: list[SurfaceDef]         # ordered as written
    path: Path                         # the SURFACES.md file

def load_surfaces(wiki_dir: Path) -> SurfacesDoc | None:
    """Load and parse <wiki_dir>/SURFACES.md. Returns None if absent."""

def load_surfaces_or_default(wiki_dir: Path) -> SurfacesDoc:
    """Like load_surfaces, but returns a built-in default if absent.

    Default = standard template (concept + decision + session).
    """

class SurfacesError(ValueError):
    """Raised by lint paths; load_* functions never raise (fall back + warn)."""
```

**Format:**

````markdown
# Surfaces — <wiki>
schema_version: 2

## concept
Cross-cutting idea or pattern across sessions.

```yaml
required: [type, created, last_reviewed, description, tags]
optional: [aliases, superseded_by, draft]
```

Extract when: pattern appears across 3+ session notes.

## decision
A trade-off made — alternatives, path chosen.

```yaml
required: [type, created, last_reviewed, description, tags]
optional: [superseded_by, implements]
```
````

**Acceptance:**
- `test_load_returns_none_on_missing_file`
- `test_load_default_returns_standard_when_missing`
- `test_load_parses_top_level_schema_version`
- `test_load_parses_multiple_sections_in_order`
- `test_load_extracts_required_optional_lists_from_yaml_block`
- `test_load_extracts_description_prose_above_yaml`
- `test_load_extracts_extract_when_line_below_yaml`
- `test_load_unknown_yaml_key_warns_but_loads`
- `test_load_malformed_yaml_block_warns_and_skips_section`

**Commit:** `feat(core): add SURFACES.md parser with embedded-YAML sections`

---

### Task 2: Schema integration

**Files:**
- Modify: `lib/lore_core/schema.py` — extend `REQUIRED_FIELDS` to load from a per-wiki SURFACES.md when present; existing hardcoded types stay as fallback.
- Test: `tests/test_schema_surfaces_integration.py`

**Goal:** the existing `REQUIRED_FIELDS` dict becomes the *fallback* set. New `required_fields_for(type_name, wiki_dir=None)` resolves first from the wiki's SURFACES.md, then falls back to the hardcoded dict.

**Key API:**

```python
def required_fields_for(type_name: str, *, wiki_dir: Path | None = None) -> list[str]:
    """Return required frontmatter fields for `type_name`.

    If wiki_dir provided and SURFACES.md exists with this surface type,
    use its `required:` list. Else fall back to module-level REQUIRED_FIELDS.
    Raises KeyError if neither knows the type.
    """
```

Existing `REQUIRED_FIELDS` stays exported for backward compat. Existing callers continue working without modification — they get the legacy behavior.

**Acceptance:**
- `test_required_fields_falls_back_when_no_wiki_dir`
- `test_required_fields_falls_back_when_surfaces_md_missing`
- `test_required_fields_uses_surfaces_md_when_present`
- `test_required_fields_raises_keyerror_for_unknown_type`
- `test_existing_required_fields_dict_unchanged_for_legacy_callers`

**Commit:** `feat(schema): resolve required fields from per-wiki SURFACES.md when available`

---

### Task 3: lore surface CLI + shipped templates

**Files:**
- Create: `lib/lore_cli/surface_cmd.py` — typer app with `add` and `lint` subcommands.
- Create: `lib/lore_core/surface_templates/` — package with `standard.md`, `science.md`, `design.md`, `custom.md`.
- Modify: `lib/lore_cli/__main__.py` — mount `surface_cmd.app`.
- Test: `tests/test_cli_surface.py`.

**Templates (shipped; user can edit freely afterward):**
- `standard.md`: schema_version: 2, surfaces: concept + decision + session.
- `science.md`: standard + paper + result.
- `design.md`: standard + artefact + critique.
- `custom.md`: schema_version + a single TODO-fill-me example surface.

**CLI:**

```
lore surface add <name> [--wiki <wiki>] [--template <std|science|design|custom>]
lore surface lint [--wiki <wiki>]
```

`add` appends a section to SURFACES.md (creating the file from `standard` if absent). `lint` validates parseability + no duplicate names + each surface has a YAML block. Curator B refuses to run on broken SURFACES.md (enforce in T8).

**Acceptance:**
- `test_surface_add_creates_surfaces_md_when_missing`
- `test_surface_add_appends_section_to_existing_file`
- `test_surface_add_rejects_duplicate_name`
- `test_surface_add_uses_template_initial_content`
- `test_surface_lint_accepts_well_formed_file`
- `test_surface_lint_rejects_duplicate_section_name`
- `test_surface_lint_rejects_unparseable_yaml_block`
- `test_surface_lint_exit_zero_on_clean_file`

**Commit:** `feat(cli): lore surface add/lint + shipped surface templates`

---

### Task 4: lore new-wiki --surfaces extension

**Files:**
- Modify: `lib/lore_cli/new_wiki_cmd.py` — accept `--surfaces <template>` flag; copy the template into the new wiki's `SURFACES.md`.
- Test: extend `tests/test_new_wiki.py` (or create `tests/test_new_wiki_surfaces.py`).

**Goal:** `lore new-wiki <name> --surfaces standard` (default `standard`) writes `<wiki>/SURFACES.md` from the template.

**Acceptance:**
- `test_new_wiki_writes_surfaces_md_with_default_template`
- `test_new_wiki_uses_specified_template`
- `test_new_wiki_rejects_unknown_template`
- `test_new_wiki_existing_wiki_does_not_clobber_surfaces_md`

**Commit:** `feat(new-wiki): scaffold SURFACES.md from --surfaces template`

---

## Phase B — Curator B engine

### Task 5: Surface writer

**Files:**
- Create: `lib/lore_curator/surface_filer.py`
- Test: `tests/test_surface_filer.py`

**Goal:** given a surface name, content, and source session notes → write a markdown file under `<wiki>/<surface-name>/<slug>.md` with frontmatter declared via SURFACES.md. `draft: true` always set on Curator B-authored surfaces.

**Key API:**

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass
class FiledSurface:
    path: Path
    wikilink: str           # "[[<stem>]]"

def file_surface(
    *,
    surface_name: str,                  # "concept" | "decision" | …
    title: str,
    body: str,                          # main body text (no frontmatter)
    sources: list[str],                 # wikilinks to source session notes
    wiki_root: Path,
    surfaces_doc,                       # SurfacesDoc from T1
    extra_frontmatter: dict | None = None,
    now: datetime | None = None,
) -> FiledSurface: ...
```

Writes frontmatter using `required_fields_for(surface_name, wiki_dir=wiki_root)` — fields the surface declares are filled from `extra_frontmatter` or sane defaults; missing required fields raise `ValueError` with a clear message.

**Acceptance:**
- `test_file_surface_creates_file_with_frontmatter`
- `test_file_surface_sets_draft_true`
- `test_file_surface_writes_to_correct_subdir` (concept → wiki/concepts/, decision → wiki/decisions/)
- `test_file_surface_includes_synthesis_sources_in_frontmatter`
- `test_file_surface_collision_appends_counter`
- `test_file_surface_raises_on_missing_required_field`

**Commit:** `feat(curator): add surface writer for graph-abstraction outputs`

---

### Task 6: Cluster step

**Files:**
- Create: `lib/lore_curator/cluster.py`
- Test: `tests/test_cluster.py`

**Goal:** given a list of recent session notes (paths + frontmatter + summaries) and a wiki's surface vocabulary → group by scope+topic via middle-tier LLM call.

**Key API:**

```python
@dataclass(frozen=True)
class Cluster:
    topic: str                          # short label
    scope: str
    session_notes: list[str]            # wikilinks/paths
    suggested_surface: str | None       # one of the wiki's surface names, if obvious

def cluster_session_notes(
    *,
    notes: list[dict],                  # {path, frontmatter, summary}
    surfaces: list[str],                # surface names from SURFACES.md
    anthropic_client,
    model_resolver,
) -> list[Cluster]: ...
```

Empty `notes` short-circuits to `[]` (no LLM call). Middle-tier prompt asks for clustering as JSON via tool-use; one cluster per coherent topic; suggests a surface name when one fits.

**Acceptance:**
- `test_cluster_empty_notes_short_circuits_no_llm_call`
- `test_cluster_returns_clusters_from_llm_response`
- `test_cluster_each_cluster_has_topic_scope_notes`
- `test_cluster_suggested_surface_matches_wiki_vocabulary_or_none`
- `test_cluster_uses_middle_tier_model`
- `test_cluster_handles_malformed_llm_response_gracefully`

**Commit:** `feat(curator): add session-note clustering step (middle tier)`

---

### Task 7: Abstract step

**Files:**
- Create: `lib/lore_curator/abstract.py`
- Test: `tests/test_abstract.py`

**Goal:** given a `Cluster`, the wiki's `SurfacesDoc`, and the cluster's source session notes → emit zero or more new surfaces (concept/decision/result/…) via opus-tier (or middle-tier fallback if `models.high == "off"`). LLM judgment decides whether the cluster meets the surface's `extract_when` rule.

**Key API:**

```python
@dataclass(frozen=True)
class AbstractedSurface:
    surface_name: str               # one of surfaces_doc.surfaces' names
    title: str
    body: str
    extra_frontmatter: dict         # e.g., {"tags": [...]}

def abstract_cluster(
    *,
    cluster,
    surfaces_doc,
    source_notes_by_wikilink: dict[str, str],  # wikilink → note body for context
    anthropic_client,
    model_resolver,
    high_tier_off: bool = False,
) -> list[AbstractedSurface]: ...
```

If `high_tier_off`: prompt is coarser; warning emitted once per session via the existing simple-tier-warning pattern from `noteworthy.py`. Empty cluster → empty list (no LLM call).

**Acceptance:**
- `test_abstract_empty_cluster_short_circuits`
- `test_abstract_emits_surface_for_clear_pattern`
- `test_abstract_emits_zero_surfaces_when_pattern_unclear`
- `test_abstract_uses_high_tier_by_default`
- `test_abstract_falls_back_to_middle_when_high_off`
- `test_abstract_warning_logged_once_when_high_off`
- `test_abstract_surface_name_must_be_in_wiki_vocabulary`

**Commit:** `feat(curator): add cluster abstraction (high tier, fallback to middle on high:off)`

---

### Task 8: Curator B pipeline

**Files:**
- Create: `lib/lore_curator/curator_b.py`
- Test: `tests/test_curator_b.py`

**Goal:** wire the pipeline. Acquire lock → enumerate session notes touched since `last_curator_b` (or default 3 days) → cluster → for each cluster, abstract → file surfaces (`draft: true`) via T5 → update WikiLedger `last_curator_b`.

**Key API:**

```python
@dataclass
class CuratorBResult:
    notes_considered: int = 0
    clusters_formed: int = 0
    surfaces_emitted: list[Path] = field(default_factory=list)
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0.0

def run_curator_b(
    *,
    lore_root: Path,
    wiki: str,                              # specific wiki — Curator B is per-wiki
    anthropic_client=None,
    dry_run: bool = False,
    now: datetime | None = None,
    since: datetime | None = None,          # defaults to wiki_ledger.last_curator_b or now-3d
) -> CuratorBResult: ...
```

**Refuses to run** on broken SURFACES.md (use `lint`-style check; record in `skipped_reasons["surfaces_md_invalid"]`).

**Acceptance (LLM mocked):**
- `test_curator_b_no_recent_notes_short_circuits`
- `test_curator_b_clusters_then_abstracts_then_files`
- `test_curator_b_files_surfaces_with_draft_true`
- `test_curator_b_advances_last_curator_b_on_wiki_ledger`
- `test_curator_b_dry_run_writes_nothing`
- `test_curator_b_lock_contention_records_skip`
- `test_curator_b_no_anthropic_client_records_skip`
- `test_curator_b_broken_surfaces_md_refuses_to_run`
- `test_curator_b_high_tier_off_still_runs_with_warning`

**Commit:** `feat(curator): add Curator B pipeline (cluster + abstract + file)`

---

### Task 9: CLI `lore curator run --abstract`

**Files:**
- Modify: `lib/lore_curator/core.py` — extend the existing `run` subcommand with `--abstract` flag; when set, call `run_curator_b` after `run_curator_a` (or alone if `--abstract --skip-files`).
- Test: extend `tests/test_cli_curator_run.py`.

**Behavior:**
- `lore curator run` (default) → A only (existing behavior, unchanged).
- `lore curator run --abstract` → A then B for every wiki found under `$LORE_ROOT/wiki/`.
- `lore curator run --abstract --wiki <name>` → A then B scoped to one wiki.

**Acceptance:**
- `test_curator_run_abstract_invokes_curator_b`
- `test_curator_run_default_does_not_invoke_curator_b`
- `test_curator_run_abstract_with_wiki_flag_filters_to_one_wiki`
- `test_curator_run_abstract_dry_run_propagates`

**Commit:** `feat(cli): lore curator run --abstract triggers Curator B`

---

## Phase C — Auto-triggers + briefing

### Task 10: SessionStart auto-trigger for Curator B

**Files:**
- Modify: `lib/lore_cli/hooks.py` — extend `cmd_session_start` (already enriched with banner in P1-T16) to detect calendar-day rollover and spawn Curator B detached.
- Test: extend `tests/test_hooks_capture.py` or create `tests/test_hooks_curator_b_trigger.py`.

**Logic** (from spec §3, §5):

```python
# Inside session-start hook handler:
wledger = WikiLedger(lore_root, scope.wiki)
ledger_entry = wledger.read()
today = (now or datetime.now(UTC)).date()
last_b_date = ledger_entry.last_curator_b.date() if ledger_entry.last_curator_b else None

if last_b_date is None or today > last_b_date:
    _spawn_detached_curator_b(lore_root, scope.wiki)
```

Reuse the same `subprocess.Popen(start_new_session=True)` pattern from P1-T14's `_spawn_detached_curator_a`.

**Acceptance:**
- `test_session_start_spawns_curator_b_on_new_day`
- `test_session_start_does_not_spawn_curator_b_same_day`
- `test_session_start_does_not_spawn_when_unattached`
- `test_session_start_curator_b_spawn_is_async_does_not_block_hook`

**Commit:** `feat(hooks): SessionStart spawns detached Curator B on calendar rollover`

---

### Task 11: Briefing auto-trigger after Curator B

**Files:**
- Modify: `lib/lore_curator/curator_b.py` (or wrapper) — after a successful run with surfaces emitted (or even with zero, if config says so), if `wiki_config.briefing.auto`, invoke briefing publish.
- Modify (optional): `lib/lore_core/briefing.py` if its API needs adjustments for the post-curator-B trigger path.
- Test: `tests/test_curator_b_briefing_integration.py`.

**Logic:**
- After `run_curator_b` completes (non-dry), read `WikiConfig.briefing`. If `auto=True`, call `briefing.gather()` then `briefing.publish_to_sinks(...)`. Update `WikiLedger.last_briefing`.
- On briefing failure: log to `vault/.lore/curator.log`; don't fail Curator B.

**Acceptance:**
- `test_curator_b_publishes_briefing_when_config_auto_true`
- `test_curator_b_skips_briefing_when_config_auto_false`
- `test_curator_b_briefing_failure_does_not_break_curator`
- `test_curator_b_advances_last_briefing_on_success`

**Commit:** `feat(curator): briefing publishes after Curator B when config.briefing.auto`

---

## Phase D — Integration

### Task 12: E2E integration test

**File:**
- Create: `tests/test_graph_abstraction_e2e.py`

**Test plan (similar shape to P1's `test_mvp_capture_e2e.py`):**

1. `test_graph_e2e_session_notes_become_surfaces` — set up tmp `LORE_ROOT` with `wiki/private/` containing `SURFACES.md` (standard template) and 4 fake session notes (all about the same topic). Mock Anthropic to return one cluster + one abstracted concept. Run `run_curator_b`. Assert a `concept-*.md` file lands in `wiki/private/concepts/` with `draft: true` + `synthesis_sources` listing the 4 session notes.

2. `test_graph_e2e_briefing_auto_publishes_after_curator_b` — same setup but with `briefing.auto: true` and a markdown sink → assert briefing file written.

3. `test_graph_e2e_no_recent_notes_no_writes` — empty `sessions/` dir → curator B is a no-op, no surface files.

4. `test_graph_e2e_high_tier_off_still_emits_surfaces` — `models.high: off` → still produces surfaces (middle-tier fallback) with the warning logged.

5. `test_graph_e2e_broken_surfaces_md_refuses` — corrupt SURFACES.md → curator B records `surfaces_md_invalid`, no writes.

**Commit:** `test: add Curator B end-to-end integration tests`

---

## Self-review

**1. Spec coverage** (passive-capture-v1 §5, §7, §8, §13):

| Spec | Task |
|---|---|
| §5 Curator B pipeline | T6, T7, T8 |
| §7 SURFACES.md + schema integration | T1, T2 |
| §7 templates + lint + add | T3, T4 |
| §8 config (briefing.auto, models.high) | T8, T11 |
| §13 model tiers (middle for cluster, high+fallback for abstract) | T6, T7 |
| Briefing auto-publish | T11 |
| SessionStart-sweep daily trigger | T10 |
| Draft-true on new surfaces | T5, T8 |

**2. Placeholder scan.** Each task has files, signatures, acceptance criteria, commit message. No "TBD".

**3. Type consistency.** `SurfaceDef` / `SurfacesDoc` / `Cluster` / `AbstractedSurface` / `FiledSurface` / `CuratorBResult` defined once; downstream tasks import.

**4. Carry-over.** `attached.wiki` direct usage; `now` parameter on advance/upsert calls — both already-honored from P1.

---

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-04-19-graph-abstraction-plan.md`. Use `superpowers:subagent-driven-development` — fresh subagent per task expands TDD detail.
