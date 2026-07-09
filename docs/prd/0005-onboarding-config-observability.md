---
title: Onboarding, connection config & observability revamp
status: draft
epic: https://github.com/buchbend/lore/issues/183
repos:
  - buchbend/lore
---

# PRD 0005: Onboarding, connection config & observability revamp

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/183).
> The epic links here; this file is not embedded in the issue body.

## Problem

Three surfaces that every Lore user touches — first-run setup, keeping repo and
wiki connections healthy, and understanding what the background note-writer is
doing — are each fragmented, silent about failure, or both.

**Onboarding is a cliff.** Getting from "curl the installer" to "notes are
being written" is five loosely chained steps (installer → integration wiring →
vault init → wiki creation → repo attach). The vault wizard only runs in
interactive mode; in non-interactive installs the user gets a one-line hint and
is on their own. The installer can finish "successfully" while the binary is
not on PATH, while the Claude plugin cache silently stayed on an old version
(so hooks and skills quietly don't load), and without ever telling the user to
restart Claude Code. Nothing runs a verification pass at the end. The most
common first-run outcome of a partial install is *silence*: no error, and no
notes.

**Connection config is write-once, repair-never.** `lore config` can show
resolved configuration but cannot change it — users hand-edit YAML with no
schema validation, so typos are silently ignored. `.lore.yml` attachment
offers are parsed permissively with no validation and no dry-run preview.
Offer drift after acceptance is only advisory; declined offers are keyed on a
content fingerprint so any offer edit resurfaces the prompt. Renaming a scope
strands every checked-in offer that referenced the old name, with no report.
Moving a vault or a repo to a new path invalidates host-local attachment state
with no migration path. And when any of this state corrupts, there is no
repair command — only hand-editing JSON.

**Observability is seven uncorrelated surfaces.** Debugging one background
flush today means walking `lore log`, `lore proc`, `lore runs`, `lore status`,
`lore news`, `lore doctor`, and a crash-log directory — seven read surfaces
over seven unlinked file families with three different ad-hoc schemas. No
identifier follows a flush from hook fire through curator run to published
note, so concurrent sessions interleave into an untellable story. Failure
handling is "availability over debuggability" taken too far: drain writes
never raise, mid-session flush failures defer silently with no marker,
retention cleanup swallows its own errors, and a stuck flush is
indistinguishable from one that simply hasn't been triggered yet. Retention is
inconsistent per file family (10 MB rotation here, count caps there,
append-forever elsewhere) and invisible to the user.

## Solution

One epic, four pillars, one visual language.

**Pillar A — a single event spine.** All background-pipeline telemetry (hook
events, curator run events, drain/news events) is emitted through one writer
with one envelope schema onto one append-only JSONL family. Every flush gets a
`trace_id` at hook fire that travels through the detached curator into run
events, drain events, and the published note's linkage frontmatter — one ID
tells the whole story. Errors carry structured codes from a closed enum;
"silently swallowed" becomes a bug class the codebase no longer contains.
Every flush is tracked by an explicit persisted state machine
(`queued → running → published | withheld | dead-lettered`), with bounded
retries and backoff, so "stuck" is a queryable state rather than an absence of
evidence. One janitor enforces tiered, visible retention across the spine.

**Pillar B — three commands instead of seven.** `lore status` is the
glanceable dashboard (capture liveness, per-wiki connection health, flush
queue including dead letters, retention state, loud alerts that each name
their drill-down command). `lore trace` renders the correlated story of one
flush end-to-end from the spine. `lore doctor` keeps deep diagnostics and
gains `--fix` repair. `lore log`, `lore news`, `lore runs`, and `lore proc`
become deprecated aliases for one release, then die.

**Pillar C — one guided first-run path.** `lore init` becomes the single
wizard: vault → wiki (new / clone / link) → integration wiring → optional
first attach → an automatic `lore doctor` pass → explicit "restart Claude
Code" handoff. It is idempotent and resumable — re-running detects existing
state, skips completed steps, and thereby doubles as a repair path for partial
installs. Full non-interactive flag parity. The shell installer exits non-zero
when the result is not actually runnable.

**Pillar D — config that can be written.** `lore config get/set/unset/edit`
with schema validation on save, for both vault and per-wiki config. `.lore.yml`
gets a validated schema, a dry-run preview, and a write-offer path from
non-interactive attach; declines key on `(path, scope)` so offer edits don't
resurface them; scope renames report the checked-in offers they strand.

## Implementation decisions

### Event spine

- **Envelope schema**, mandatory on every event:

  ```
  { ts, v, source, event, level, trace_id, session_id, run_id,
    wiki, scope, error_code, data }
  ```

  `source` identifies the producer (hook, curator, drain, janitor, install);
  `level` is info/warn/error; `error_code` comes from a closed enum defined in
  one module — free-form strings live only inside `data`. Schema version `v`
  is bumped on any envelope change.
- **JSONL stays the write medium.** Hooks must never block or take a
  dependency on a daemon; O_APPEND writes ≤ PIPE_BUF are already the proven
  concurrency pattern in this codebase (hook-events log, sibling-flock
  rotation). The spine is a small family of JSONL files under one directory,
  all sharing the envelope; any query layer reads, never owns, the data.
- **trace_id propagation:** minted when a hook decides work exists, passed to
  the detached curator process via environment variable, stamped by the
  curator into every run event and drain event it emits, and written into the
  published chapter's linkage frontmatter. This is designed to compose with
  PRD 0004's deterministic linkage frontmatter (lore#175): trace_id is one
  more deterministic key in the same block.
- **Producers migrate fully, not opportunistically:** hook events move onto
  the spine in the foundation slice; drain emission moves when trace_id
  stamping rewrites it; curator run logging moves when the flush state machine
  rewrites the flush path. At the end of the epic the legacy hook-events /
  runs / drain writers are gone.

### Flush lifecycle state machine

- One persisted record per flush unit with states
  `queued → running → published | withheld | dead-lettered(reason)`, plus
  attempt count and next-eligible-retry time. Transitions are emitted to the
  spine; the record itself is the queryable source of truth for "what is
  in-flight right now".
- Bounded retries with backoff replace the current
  retry-forever-or-give-up-silently behavior. Exhaustion produces a
  dead-letter record with a structured reason — never silence. Every
  currently-silent failure path in the flush pipeline (buffer sidecar errors,
  spawn failures, chapter-append I/O errors, retention-cleanup errors) must
  either emit an error-level spine event or produce a dead letter.

### Command surface

- `lore status`: single Rich dashboard — capture liveness with last-flush
  outcome, flush queue counts (queued / running / dead-lettered), per-wiki
  connection health (remote reachable, ahead/behind, dirty), retention usage,
  and an alerts section where every warning names the exact drill-down
  command. Absorbs `lore news`.
- `lore trace <trace-id | session-id | note | last | dead>`: chronological
  Rich tree of one flush's spine events — hook fire, spawn, run decisions, LLM
  calls, gate outcomes, note append — with durations and error codes. Absorbs
  `lore log`, `lore runs`, `lore proc` as debugging surfaces.
- `lore doctor`: keeps deep install diagnostics; plugin-cache version drift
  becomes a *failing* check, not advisory. Gains `--fix`: rebuild the vault
  scope registry from accepted attachments, re-stamp offer fingerprints after
  a reviewed drift, and migrate attachment paths after a vault or repo move.
- Deprecated commands (`log`, `news`, `runs`, `proc`) print a pointer to their
  replacement and delegate, for one minor-version window.

### Onboarding wizard

- `lore init` steps: vault location (default honored from `$LORE_ROOT`) →
  wiki (new personal / clone team remote / link existing directory; scaffolds
  `_scopes.yml` with a commented example) → integration wiring (detect Claude
  Code / Cursor, reuse the existing install plumbing; plugin-cache refresh
  failure is loud) → optional first attach when cwd is a git repo (runs the
  attach wizard inline) → automatic `lore doctor` with non-zero exit on
  failure → printed handoff ("restart Claude Code", next steps).
- **Idempotent and resumable:** each step detects already-done state and
  collapses to a ✓ skip line, so re-running `lore init` completes or repairs a
  partial install.
- **Flag parity:** `--vault PATH`, `--wiki-new NAME | --wiki-clone URL |
  --wiki-link PATH`, `--attach`, `--yes` drive every step non-interactively.
- `install.sh` exits non-zero when the installed binary is not runnable on
  PATH, and chains into `lore init` for first installs.
- Coordinates with PRD 0003's onboarding merge (lore#171): workflow
  scaffolding remains a step inside this same wizard — one onboarding command
  per repo, ever.

### UX decisions (epic-wide)

- **Rich only, no new dependencies.** No full-screen TUI (Textual) and no
  prompt-toolkit-style dependency; the house style is Rich panels and ✓/✗
  lines, and every interactive prompt degrades to plain stdin under `--plain`
  or a non-TTY.
- **Wizard visual grammar:** step header (`Step k/6 · Wiki`), one explanation
  sentence, defaults in brackets with Enter-to-accept, and each completed step
  collapsing to a single ✓ receipt line so terminal history reads as an
  install record. Final screen: summary panel + doctor results + boxed next
  steps.
- **Dashboard grammar:** one line per concern, ⚠ alerts always paired with the
  exact command that drills down.

### Config

- `lore config get/set/unset/edit`, with `--wiki NAME` targeting per-wiki
  config. Writes are validated against a schema derived from the existing
  typed config model before saving; invalid keys or values are rejected with
  the valid alternatives named. `edit` opens `$EDITOR` and validates on close.
- `.lore.yml` offers get the same treatment: a schema, validation errors that
  point at the offending key, `lore attach offer --dry-run` printing the
  payload without writing, and a `--write-offer` option on non-interactive
  manual attach so the CLI path stops silently skipping the shared-config
  step. Declines are keyed `(path, scope)`. `lore scopes rename` prints a
  report of checked-in offers that still reference the old scope.

## Testing decisions

External behavior over implementation detail, in the repo's existing
fine-grained pytest style (the drift-test pattern and the hook/curator
behavior suites are prior art):

- **Spine:** concurrent-append safety (parallel writers, no interleaved
  records), envelope completeness (every producer's events validate against
  the schema), error-code enum closure (no free-form `error_code` anywhere),
  rotation under contention.
- **trace_id:** an end-to-end fixture drives hook fire → detached curator →
  note append and asserts one trace_id appears in every layer including the
  note frontmatter.
- **State machine:** transition-table tests (legal and illegal transitions),
  retry/backoff scheduling, dead-letter on exhaustion, and a regression test
  per formerly-silent failure path asserting it now produces an event or dead
  letter.
- **Commands:** golden-output tests for `status` and `trace` rendering under
  `--plain`; deprecated aliases assert delegation plus pointer text.
- **Wizard:** driven entirely through the non-interactive flags in tests plus
  prompt-response scripting for the interactive path; idempotency asserted by
  running init twice and diffing state; partial-install repair asserted by
  deleting one artifact and re-running.
- **Doctor/repair:** corrupt-then-`--fix`-then-revalidate round trips for each
  repairable state file; plugin-cache drift check asserted failing.
- **Config/offers:** schema acceptance/rejection tables, precedence unchanged
  (existing config-precedence tests keep passing), dry-run produces no writes.

## Out of scope

- Metrics, SLO dashboards, or alert *pushing* — the spine carries counters and
  the status command reads them, but no daemon, poller, or notification
  channel ships in this epic.
- A SQLite or otherwise indexed query layer over the spine — JSONL plus
  in-command aggregation is sufficient at current volumes; an index can be a
  later, purely additive layer.
- Curator quality, gating, or composition changes — this epic changes how the
  pipeline is *observed and driven*, not what it writes.
- Multi-author sync hardening and sharing consent (PRD 0004, lore#178/#179).
- Workflow-scaffolding onboarding content itself (PRD 0003, lore#171) — this
  epic provides the wizard frame it plugs into.
- Windows installer support.
- Per-host spawn-gate enforcement (epic seed lore#182 territory).
