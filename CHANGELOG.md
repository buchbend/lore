# Changelog

All notable changes are recorded here. The version in this file mirrors
`pyproject.toml` and `.claude-plugin/plugin.json` — bumping the package
version is what makes `claude plugin update lore@lore` re-fetch.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(0.x means anything can change between minor versions until 1.0).

## [Unreleased]

## [0.18.1] - 2026-04-28

### Fixed

- **`plan-capture` no longer silently drops every plan.** The
  `PostToolUse:ExitPlanMode` handler gated capture on
  `tool_response.approved`, but Claude Code's actual ExitPlanMode
  payload has no `approved` field — the field was a defensive guess
  during initial implementation. Every accepted plan was logged as
  `outcome: "rejected"` and dropped, contradicting the handler's
  "Never silent loss" guarantee. The handler now treats hook firing
  as the authoritative approval signal (Claude Code never fires
  `PostToolUse` on rejection — rejection bounces back into plan
  mode without producing a `tool_result`). Added a regression test
  using the real captured payload shape (`tool_response: { plan,
  isAgent, filePath, hasTaskTool }`).

## [0.18.0] - 2026-04-28

### Changed

- **Session-note revision Phase 6 — Curator B section-aware input
  + decision prompt strengthening.** Curator B's
  `_load_recent_session_notes` now feeds clustering and the abstract
  prompt a section-aware extract (`## Summary` paragraph + first 300
  chars of `## Decisions made`) instead of `body[:800]`. The
  rationale-rich layer lands in B's prefix window first, regardless
  of whether the Summary paragraph runs 4 sentences or 6. Legacy
  notes (pre-revision, no Summary/Decisions sections) fall back to
  the original `body[:800]` slice so existing cluster fixtures keep
  working until the vault rolls forward.

  `surface_templates/standard.md` `decision` `extract_prompt` now
  anchors on rationale-presence (six-month rule) rather than section
  presence. The prompt explicitly notes that a `## Decisions made`
  bullet is a *candidate* — apply the rejection rules — not license
  to extract. This complements the per-section authoring norms that
  Curator A's prompt now injects (Phase 5), so trivial bullets that
  slip through the writer get caught at the surface-extraction
  step.

### Notes

- Existing wikis with their own `<wiki>/SURFACES.md` won't pick up
  the strengthened `decision` prompt automatically — only new wikis
  initialised after this release get it. A `lore surface` resync
  command is a separate follow-up.

## [0.17.0] - 2026-04-28

### Added

- **Session-note revision Phase 5 — per-wiki session templates.** New
  `lib/lore_core/session_templates/standard.md` documents the locked
  body shape and per-section authoring norms (Decisions promote-vs-don't
  rule, Loose ends past-tense / stative grammar, bold-substance-phrase
  bullet style, etc.). New `lib/lore_core/session_template.py` loads
  it with a per-wiki override at `<wiki>/templates/session.md`, parallel
  to how `surface_templates/standard.md` works. `classify_slice` and
  `_build_prompt_text` accept a `wiki_dir` kwarg and inject the active
  template's `## Section-authoring norms` block into the noteworthy
  LLM prompt — so when a wiki tunes its norms the LLM picks them up
  on the next call without code changes. Drift test asserts every
  heading the renderer emits is mentioned in `standard.md` so the
  documentation can't silently diverge from the code.

## [0.16.4] - 2026-04-28

### Fixed

- **`_last_session_hint` walks the sharded layout.** The previous
  `sessions/*.md` flat glob only matched `_recent.md` (a stale cached
  pointer) — SessionStart's "last note: …" line silently went empty
  against any real vault. Now `rglob`s the full `sessions/<YYYY>/<MM>/`
  subtree, filters to date-prefixed filenames via the new
  `_session_note_date` helper, and skips non-session frontmatter.
- **No more 1024-byte head-read cap.** Real notes with SHA-256 hashes
  in `source_transcripts` plus a long paragraph routinely sat past the
  cap, dropping the field. Cap removed; `parse_frontmatter` stops at
  the closing `---` regardless.
- **`_recent_open_items` walks the sharded layout** with the same
  `rglob` fix and the new date helper.

### Changed

- **Status-line preference: `title` → `description` → `summary`.**
  Revised notes' `title` (explicit slug source) wins for the status
  line; legacy notes fall through to `description` (legacy short
  headline) and finally `summary` (legacy paragraph).
- **`_OPEN_ITEMS_RE` recognises both shapes.** `## Open items` (legacy
  v1) and `## Loose ends` (current) match. `## Issues touched` (v2 gh
  reference section) is intentionally excluded — it's resolved-work,
  not "things discussed but not pursued".
- **SessionStart copy softened.** "Open items" → "Loose ends from
  recent sessions". Loose ends are informational, not a TODO list;
  surviving work belongs in the configured PM backend.
- **`threads.py` reads `title`/`description`/`summary` with the new
  preference.** Title prefers `title`, falls back to `description`.
  Summary prefers `summary`, falls back to `description` only when
  `title` is also present (signals revision shape).

### Added

- **`docs/audit-description-readers.md`** — inventory of every
  session-note `description` / `summary` / `title` reader in
  `lore_core/` and `lore_cli/`, with the per-site Phase 4 decision.
- **6 new sharded-layout / cross-shape tests** in
  `test_hooks_last_session_hint.py` covering: title preference,
  description fallback, summary fallback, sharded walking, full
  frontmatter reads, non-session note skipping.

## [0.16.3] - 2026-04-28

### Added

- **Session-note revision Phase 3 — mechanical Activity section +
  cross-note frontmatter.** New module
  `lib/lore_curator/session_activity.py` collects:
  - **Commits** in the chunk's work window via `git log --since/--until`.
  - **Issues opened / closed** by extracting `<verb> #<N>` patterns
    from turn text + commit subjects, then intersecting with `gh issue
    list --state open/closed` so only issues actually mentioned this
    session land in the section.
  - **Plans advanced** from `Plan: <slug>#sN` commit trailers and
    `[[plan/<slug>(#sN)?]]` body wikilinks, validated against
    `wiki/<wiki>/plans/`. Hallucinated slugs drop silently.
  - **Projects** from cwd repo + repo prefixes in `files_touched`,
    validated against `wiki/<wiki>/projects/`.
  Results render under the body's `## Activity` parent (with
  `### Commits` / `### Issues opened` / `### Issues closed`
  subheadings, omit-when-empty) and into the frontmatter `plans:` /
  `projects:` lists. All collectors are best-effort: missing git, gh,
  network — return empty lists, never block. 26 new tests cover the
  collectors, renderers, and integration end-to-end.

### Changed

- **Append-mode Activity union.** `merge_body_sections` now unions
  Activity sub-sections (commits / issues) across appends with
  exact-line dedup, so re-running the collectors on a new chunk
  doesn't double-list a commit seen earlier.

## [0.16.2] - 2026-04-28

### Changed

- **Session-note revision Phase 2 — body shape.** The body now opens
  with `# <title>` (no `Session:` prefix), then a rationale-first
  layout: `## Summary` paragraph → `## Decisions made` → `## What we
  worked on` → `## Activity` (parent for Phase 3's `### Commits` /
  `### Issues opened` / `### Issues closed`) → `## Loose ends`.
  Curator B's prefix window now lands on the durable rationale layer
  first. The legacy `### Summary` bullets, `### Files touched` (which
  duplicated frontmatter), and freeform `Entities:` line are gone.
  Append-mode merges new chunks into the existing sections — no more
  per-chunk `## <chunk title>` H2 wrappers; first chunk's title and
  Summary win, subsequent chunks contribute bullets only. Activity
  sub-sections stay empty in this phase; Phase 3 populates them from
  git/gh. Section parsing is permissive — old-shape content (e.g. a
  v2-old `### Summary` block on a same-day open note) fades out the
  next time the note is appended to. Part of
  [plans/ok-file-issues-and-harmonic-lagoon.md].

## [0.16.1] - 2026-04-28

### Changed

- **Session-note revision Phase 1 — frontmatter shape + slug.** New
  `title` field carries the content-named headline (slug source);
  `description` keeps a 1-2-sentence status-line preview; the old
  paragraph `summary` field is gone (its content moved into
  `description`); `draft: true` is dropped — sessions are immutable
  historical records and the flag never flipped. The slug logic now
  truncates at the last hyphen boundary instead of the naive
  `[:60]` cut, so filenames stop ending mid-word
  (`...rebase-onto-pha`). Old notes (with `summary` + `draft`) keep
  validating via permissive `OPTIONAL_FIELDS`. Curator A's noteworthy
  prompt explicitly steers titles toward content names (no "Phase 12"
  / "v0.13.0" headlines) and asks for past-tense `loose_ends` bullets
  that read as state-of-the-world, not as TODOs. Body rendering is
  unchanged in this phase — Phase 2 picks that up. Part of
  [plans/ok-file-issues-and-harmonic-lagoon.md].

### Added

- **`install.sh` bootstrap installer.** Replaces the deprecation shim
  that lived at the repo root since the v0.10 migration. Picks
  `pipx` / `uv tool` / `pip --user` (in that preference order),
  installs or upgrades the `lore` binary, then chains into
  `lore install` to wire up integrations. Curl-pipeable from
  `https://raw.githubusercontent.com/buchbend/lore/main/install.sh`
  for first-time installs; re-run for upgrades. Detects Claude
  plugin presence to skip the full integration plan on upgrade
  reruns and just refresh the manifest cache instead.
- **`lore install --upgrade` / `-u`.** Delegates to `install.sh
  upgrade` via subprocess so the binary self-replacement happens
  outside the running Python process. Single command, full roundtrip
  (binary + integrations + plugin cache).
- **`lore install` auto-runs `claude plugin update lore@lore`** after
  a successful install when the Claude integration was part of the
  run, so the plugin manifest cache no longer drifts behind the
  binary. Falls back to a copy-pasteable hint when `claude` isn't on
  PATH or the plugin isn't yet marketplace-installed.
- **`lore doctor` plugin-cache drift check.** Reads
  `~/.claude/plugins/installed_plugins.json` and compares the cached
  `lore@lore` version to the installed pip version; flags the inverse
  footgun (cache updated, binary not) with the exact fix command.

### Fixed

- **Templates ship as package data.** `templates/` moved into
  `lib/lore_core/templates/` and declared in
  `[tool.setuptools.package-data]`. Previously `lore install` (cursor
  integration) and SessionStart's directive injection assumed a
  repo-root layout via `Path(__file__).parent.parent.parent.parent` —
  fine for editable installs, FileNotFoundError under pipx/uv wheel
  installs.

## [0.16.0] — 2026-04-28

P3.2 from the multi-agent synthesis review — composite multi-stage
retrieval. Settles the trace-vs-composite design question with one
round-trip + structured stage breadcrumbs in the response envelope.

### Added

- **`lore_drill` MCP tool** — composite chain `search → read → expand
  → read_expanded` in one envelope. Returns
  ``{trace: [...], result: {notes: [...]}}`` with `elapsed_ms` per
  stage and `skipped` reasons (`search_returned_zero`,
  `no_wikilinks`) for short-circuited intermediates. Cap on the
  expand stage via `expand_limit` (default 5) keeps hub-note
  blowups bounded; truncation is recorded in the trace as
  `{truncated: N, kept: M}` only when the cap actually fires (not
  when the candidate set was simply smaller after unresolvable
  slugs were skipped). Failed reads are surfaced as `read_failed`
  arrays so divergence between `paths` and `result.notes` stays
  debuggable.
- **`lore drill <query>` CLI verb** — calls the same handler;
  renders the trace as a rich Tree and the result as a paginated
  note list. `--json` returns the raw envelope. Supports
  `--wiki`, `--k`, `--expand-limit`. Mounted under the Knowledge
  panel in `lore --help`.
- **Trace contract documented** in
  ``docs/architecture/lore-drill.md`` — clients should always
  check ``"skipped" in step`` before reading data keys.

## [0.15.0] — 2026-04-28

P3.3 from the multi-agent synthesis review — settles slash-toggle
vocabulary AND closes the long-standing UX honesty bug where
`/lore:on`, `/lore:off`, `/lore:loud`, `/lore:quiet` described
sentinel-backed mute behaviour that was never wired in code.

### Added

- **`lore on [scope]` and `lore off [scope]` CLI verbs.** Scope
  defaults to `all`. `all` mutes hooks, the capture pipeline, MCP
  retrieval, and inline citation affordances for the session.
  `citations` mutes only the inline `› consulted [[X]]` affordance.
  Sentinel files live under `$TMPDIR/lore-off-{scope}-{sid}` so the
  OS reaps them at session boundary. See
  ``docs/architecture/slash-toggles.md``.
- **`/lore:off citations` takes effect mid-session.** The citation
  suppression directive is now also injected on every
  `UserPromptSubmit` while the sentinel is set, so the agent stops
  emitting `› consulted` lines on the very next turn rather than
  waiting for the next SessionStart.
- **`docs/architecture/slash-toggles.md`** — full vocabulary +
  semantics + check-site contract.
- **`docs/architecture/lore-drill.md`** — settles P3.2 design
  (composite MCP call with structured trace + result envelope, plus
  server-side short-circuit on empty intermediate results).
  Implementation pending.
- **`lib/lore_core/toggles.py`** — `is_off(scope, sid)` /
  `set_off(scope, sid)` / `clear_off(scope, sid)` — single source of
  truth for "is this session muted?" queries.

### Changed

- **`/lore:off` is now security-honest:** every Lore touchpoint is
  gated. Hook entries (`cmd_session_start`, `cmd_pre_compact`,
  `cmd_stop`, `cmd_user_prompt_submit`), the capture pipeline
  (SessionEnd / SessionStart-capture / PreCompact-capture), and the
  MCP `_dispatch` all check the per-session sentinel and short-circuit
  cleanly when set. Previously the four toggle skills described this
  behaviour but the SKILL.md prose had no implementation behind it.
- **MCP refusal envelope** — every tool returns
  `{"error": {"code": "session_off", "message": ..., "next": ...}}`
  while `off all` is active. Code string follows the existing
  snake_case taxonomy.
- **Skill bodies** for `/lore:off`, `/lore:on`, `/lore:loud`,
  `/lore:quiet` now invoke the new CLI verbs rather than describing
  imaginary behaviour.

### Deprecated

- **`/lore:loud` and `/lore:quiet`** are now thin aliases for
  `/lore:on citations` and `/lore:off citations`. Kept for one minor
  release with a deprecation note in the SKILL.md `description`; will
  be removed in `0.16.0`.

## [0.14.0] — 2026-04-28

Plans-as-Lore-core: capture, store, and surface multi-step plans
directly in the wiki. Headline outcome is the **zero-handover demo**
— form a plan in Claude Code, accept ExitPlanMode, ``/clear``, restart
in the same repo, and the SessionStart banner brings Claude back
oriented on the right step with no manual ritual.

### ⚠️ Required upgrade step

Run ``/plugin update lore`` (or ``claude plugin update lore@lore``)
after upgrading the pip package. The new ``PostToolUse:ExitPlanMode``
hook only fires once Claude Code re-fetches the plugin manifest;
without the update, plan capture silently doesn't happen. ``lore
doctor`` warns when the installed plugin version diverges from the
pip version.

### Added

- **``type: plan`` surface** at ``wiki/<wiki>/plans/<slug>.md`` with
  stable step anchors (``s1..sN``) and a ``step_status`` frontmatter
  dict that is the single authoritative signal for "where are we" in
  a plan. Set semantics support out-of-order completion (Claude
  reorders steps for efficiency) and parallel agents (multiple
  ``in_progress`` is normal). Three values: ``done | in_progress |
  blocked``; pending is implicit (absence).
- **``PostToolUse:ExitPlanMode`` hook** (``lore hook plan-capture``)
  captures accepted plans automatically. Top-level except writes the
  raw payload to ``~/.cache/lore/orphan-plans/<ts>.json`` and emits a
  recovery hint — never silent loss.
- **``lore plan`` CLI**: ``list``, ``delete [--force]``, ``import
  [--from-orphan|--from-markdown]``, ``step <slug> <step_id>
  --done|--in-progress|--blocked|--pending``, ``advance <slug>``.
  ``advance`` is sugar — marks the in-progress step done if any,
  else the next pending step.
- **Per-slug ``flock``** in plan writer + step_status mutator;
  concurrent same-slug writes serialize without data loss.
- **Project-note auto-stub on attach** (Phase 3, not yet wired):
  ``stub_project_note`` composes a project note from CLAUDE.md /
  AGENTS.md / .cursorrules / README / pyproject metadata. Canonical
  headings (``## Overview``, ``## Conventions``, ``## Architecture``,
  ``## Key decisions``) regenerate on re-stub; user content under
  any other heading is preserved.
- **Breadcrumb scan** (Phase 4 surface): trailers of the form
  ``Plan: <slug>#s<N>`` in recent commits + session note wikilinks
  surface in the SessionStart Resume block as informational nudges
  ("commit abc123 references s4 — ``/lore:plan-step s4 --done``?").
  Never authoritative; the ``step_status`` field is law.

## [0.13.1] — 2026-04-28

Fix #29 — mid-stream session notes now surface in the active session.

### Fixed

- **Hooks read the JSON payload Claude Code passes on stdin** and
  republish ``session_id`` as ``CLAUDE_SESSION_ID`` for the duration of
  the hook process. ``cmd_session_start``, ``cmd_pre_compact``,
  ``cmd_user_prompt_submit``, ``cmd_capture``, and ``cmd_stop`` all go
  through the new ``_read_hook_payload`` helper. Until now lore never
  read the payload, so :func:`lore_core.drain.resolve_session_id` had
  to guess — and with multiple concurrent Claude sessions the
  transcript-freshness heuristic could pick a *different* session at
  curator-write time vs. heartbeat-read time, leaving curator-filed
  ``note-filed`` events in the wrong drain file. The published
  ``CLAUDE_SESSION_ID`` is automatically inherited by detached curator
  subprocesses (``_spawn_detached`` already does
  ``env = os.environ.copy()``), so writer and reader now agree on the
  same drain file.

### Added

- **``Stop`` hook is now declared in ``.claude-plugin/plugin.json``.**
  Previously only ``examples/settings.json`` listed it, so users on
  the plugin path never got the post-turn capture hint.

## [0.13.0] — 2026-04-27

Phase 12 — P3.1 + P3.4 from the multi-agent synthesis review:
**typer-app lift + CLI contract layering test**. The `lore_runtime`
package — a 301-LOC workaround that existed solely so lower layers
could host typer apps without inverting the dependency graph — is gone.

### Changed

- **Typer apps lifted into `lore_cli/<verb>_cmd.py`.** Previously
  `lore_core.lint`, `lore_core.migrate`, `lore_curator.defrag_curator`,
  `lore_mcp.server`, and `lore_search.cli` each ended with a typer
  block that the dispatcher mounted via cross-package import
  (`from lore_core import lint as lint_cmd`). Now every verb lives in
  its own `lore_cli/<verb>_cmd.py` file and the lower layers contain
  only business logic. The dispatcher in `lore_cli/__main__.py`
  imports siblings via a single `from lore_cli import (...)` block.

- **`lore_runtime` package deleted.** Its two helpers moved to where
  they're used: `argv_main` → `lore_cli/_argv_compat.py` (internal
  compat shim wrapping typer apps in the legacy `main(argv) -> int`
  contract) and `run_render` (pure-Python run-log renderers, no I/O)
  → `lore_core/run_render.py`.

- **`lore_mcp.server._start_server` renamed to `start_server`.** The
  function now crosses a package boundary (it's imported by
  `lore_cli/mcp_cmd.py`), so the leading-underscore "private to the
  module" convention no longer applies.

- **`lore_curator.__init__` and `lore_mcp.__init__`** dropped their
  `main` re-exports — those re-exports surfaced typer entry points as
  package public API, which they aren't. (Pre-1.0 cut-hard policy.)

### Added

- **`tests/test_cli_contract.py`** — CLI contract layering test
  (P3.4): asserts every `lore_cli/<verb>_cmd.py` defines a module-level
  `app: typer.Typer` and that `lore_cli/__main__.py` only mounts
  subapps via `add_typer()` (the documented `cmd_uninstall_alias` is
  the single grandfathered exception). Catches accidentally-hidden
  verbs and inline command drift in the dispatcher.

- **`docs/architecture/cli-contract.md`** — the seam doc the
  synthesis flagged as P5.2: shape of a verb file, the four rules
  (enforced by the layering tests), where helpers live, and how to
  break the rules cleanly.

### Tests

1554 → **1585 pass** (+31 from the new contract file). All five
lifted verbs render `--help` correctly under `lore <verb> --help`.

## [0.12.0] — 2026-04-27

Phase 11 — eight P2 structural cleanups from the multi-agent review.
No new features; all internal. The codebase is materially smaller and
more uniform; no behaviour change.

### Changed

- **`lore_core.git.git_user_email`** is now the single source of truth
  for resolving git's configured author email. Three sites that each
  reimplemented the same `subprocess git config user.email` shape
  (`lore_core/session.py`, `lore_curator/session_filer.py`,
  `lore_cli/hooks.py`) collapse to one helper with a documented
  resolution order (env override → git config → optional hostname
  fallback → empty).

- **`lore_core.lockfile.flocked`** is now the single source of truth
  for `fcntl.flock` context managers. Three sites that each spelled
  the same `os.open` + `fcntl.flock(LOCK_EX[|LOCK_NB])` + cleanup
  pattern (`lockfile.try_acquire_spawn_lock`,
  `hook_log.MaintenancePolicy._maybe_rotate`,
  `install/_helpers._flocked`) now go through the helper. Yields a
  ``bool`` so callers branch on acquired-or-not for non-blocking
  cases.

- **`session_curator.run_curator_a`** dry-run vs locked branches
  deduplicated via a closure (`_iterate_pending`). Pre-Phase-11 this
  was ~22 lines of nearly-identical code that drifted the moment a
  fix touched only one branch — exactly the failure mode Phase 6's
  `run_curator_c` decomposition targeted, missed in this function.

- **Curator C defrag passes register at call time, not import time.**
  Replaced the `_DEFRAG_PASSES` global + import-side-effect
  ``_register()`` calls in `c_*.py` modules with a single
  `_all_defrag_passes()` function that lazy-imports the three pass
  callables. Order is now deterministic; `importlib.reload` doesn't
  lose passes; debugging "where did this pass come from?" is one
  grep instead of a registry walk.

- **`hooks._gh_*` wrappers inlined** where they were one-line
  passthroughs to `lore_core.gh.*`. `_run_gh` stays — it's
  monkeypatched by `tests/test_hooks_v2.py` to feed deterministic
  JSON. `_split_filter`, `_format_issue_line`, `_format_pr_line`
  are gone; tests now call `gh.*` directly.

### Removed

- **Curator role-name aliases** (`run_session_curator`,
  `run_daily_curator`, `run_defrag_curator`). The 0.10.x rename never
  stuck — usage skewed 195:11 toward A/B/C across `lib/` and
  `tests/`, and the aliases existed only to soften a transition that
  didn't happen. Per-wiki memory and `feedback_curator_naming` agree:
  user-facing copy says "Curator"; A/B/C live in code only. Public
  exports from `lore_curator/__init__.py` now name the canonical
  A/B/C entry points.

- **Legacy `pending-breadcrumb.txt` migration call** removed from the
  hot SessionStart path (`lore_cli/breadcrumb.consume_pending_breadcrumb`).
  The migration helper itself stays for hand-rolled use on long-dormant
  vaults; the unconditional call was 0.9.0-era, ran on every
  SessionStart, and was paying ~10μs of idempotent FS overhead on
  every Claude session start.

### Internal

- Pinned removal target on the two ``# legacy — retained during
  deprecation`` comments in `lore_core/lint.py` (NoteInfo.status,
  serialiser): scheduled for 1.0 removal once vaults converge to
  the `lifecycle` field.

### Tests

- Test count unchanged (1555/1555). All P2 work was structurally
  invariant — same behaviour, smaller surface.

## [0.11.1] — 2026-04-27

Phase 10 follow-ups: surface diverged wikis in `lore status`; clean up
the cosmetic "host" debt in test files that the Phase 9c rename pass
left behind.

### Added

- **`lore status` divergence indicator.** Walks attached wikis and
  reports any whose local branch has commits the remote doesn't AND
  vice versa. `auto_pull` (Phase 10) skips diverged trees silently —
  `lore status` is now the canonical "you have unfinished sync work"
  surface. New public helper `lore_core.git_sync.is_diverged(wiki_dir)`
  — cheap local check, never fetches.

### Internal

- Test-file cosmetic cleanup: `def lookup(host: str)` → `(integration: str)`
  in `test_curator_a.py`, `test_auto_diagnostics_e2e.py`,
  `test_adapter_protocol.py`. Stub class `_MissingHostAttr` →
  `_MissingIntegrationAttr`. Bodies were already correct from Phase 9c;
  these were the names/strings that Phase 9c's mechanical sweep
  intentionally left for a follow-up. Caught by the Phase 9c review.

- Reviewer-flagged Phase 10 corrections (already shipped in 0.11.0;
  noted here for completeness):
  - `git_sync.auto_push` step 3 now calls `git merge --abort` on
    commit-failure, mirroring step 4.
  - LLM-merge result validation requires `parse_frontmatter` to return
    a dict containing a `type` key — malformed responses can no longer
    clobber a real surface.
  - Push-after-merge failure messages now say "merge committed locally,
    push failed" so the user knows the next auto_push retries via the
    fast path.

### Tests

- 4 new `is_diverged` tests in `test_git_sync.py`. Total: 1555/1555.

## [0.11.0] — 2026-04-27

**Cross-host sync.** This is the release that turns Lore from "a
beautifully-architected single-host tool that *describes* a multi-host
product" (verbatim from the multi-agent review) into the cross-host
AI ↔ human knowledge vault its README pitches. Three orthogonal
mechanisms ship together:

### Added — auto-pull at SessionStart

`lore_core.git_sync.auto_pull(wiki_dir)` does a fetch + fast-forward on
the scope's wiki repo when SessionStart fires. Strictly read-only on
dirty or diverged trees; never disturbs in-flight user work. The
`lore_cli/hooks.py:cmd_session_start` handler invokes it once for the
attached scope; warnings (`· wiki [[name]] diverged from origin —
``git pull`` manually`) render into the banner footer. Per-wiki opt-in
via `.lore-wiki.yml`'s `git.auto_pull: true` (default true; was a dead
dataclass field through 0.10.x).

### Added — auto-push with LLM-merge surface conflict resolution

`lore_core.git_sync.auto_push(wiki_dir, *, llm_client=None)` pushes
local commits, classifies conflicts, and resolves surface conflicts
inline via an LLM call. **No temp `.host-A.md` artefacts. No wait for
Curator C to defrag.** When two hosts independently produce
`concepts/foo.md`, the second host's push triggers the merge, runs
the LLM with both versions + the merge-base, writes one canonical
file, commits "merge(auto-llm): N surface(s)", and pushes.

Conflict classification:
- **Surface** (`concepts/`, `decisions/`, `results/`, …): LLM-merge
- **Session note**: LLM-merge (rare in steady state — pre-pull eliminates)
- **Regenerable** (`_catalog.json`, `_index.md`, `llms.txt`,
  `threads.md`): take ours; lint reconciles
- **Unknown** (e.g. hand-edited `CLAUDE.md`): `git merge --abort`,
  surface to user

`lore_curator.session_curator._maybe_auto_commit` and
`lore_curator.daily_curator._maybe_auto_push` invoke `auto_push` when
the wiki opts in via `.lore-wiki.yml`'s `git.auto_push: true` (default
false — flip per-wiki when ready). Both curators already have an
`llm_client`; they pass it through so merges happen at curator cost,
not at user-prompt cost.

### Added — fs-watch reindex invalidation

`lore_mcp.reindex_watcher.start_watcher` boots a `watchdog`-backed
daemon thread inside the MCP server (one per Claude session). On
`*.md` create/modify/delete under `<lore_root>/wiki/`, it marks the
affected wiki dirty. The next `lore_search` reindexes that wiki
*regardless* of the 5-second throttle — so post-pull edits surface
immediately, not after the throttle's natural decay.

`watchdog>=4` is in the `[search]` extras (was already declared, just
unused). When the dep isn't installed, behaviour falls back to
throttle-only invalidation — same as 0.10.x. Kernel-level monitoring
(inotify on Linux, FSEvents on macOS) makes this near-zero-overhead.

### Added — sink registry

`lore_core.briefing.sinks` is now a real registry: schemes register
themselves at import (`markdown`, `matrix`), and
`dispatch(uri, text)` looks up the scheme prefix and calls the
registered sender. Adding Slack / Discord / GitHub Discussion is one
new module and one `register("slack", _send)` call — no edits to
`daily_curator.py` or `briefing_cmd.py`.

`lib/lore_curator/daily_curator.py:435`'s hardcoded
`if sink.startswith("markdown:")` chain is gone; `briefing_cmd.py`'s
`_KNOWN_SINKS` static set is gone. Both go through `dispatch`.

### Changed (breaking — pre-1.0)

- **`lib/lore_sinks/` deleted.** Contents moved to
  `lib/lore_core/briefing/sinks/` per the simplifier review's CC1
  finding. Anyone running `python -m lore_sinks.markdown` directly
  needs to switch to `lore briefing publish --sink markdown:<path>`.
- **`lib/lore_core/briefing.py` is now a package** (`lib/lore_core/briefing/`).
  `gather()` and `mark_incorporated()` re-exported from
  `lore_core.briefing` — public import path unchanged.
- `_strip_frontmatter` / `_split_frontmatter` test no longer required:
  the existing helpers are now `from lore_core.schema import …`
  re-exports; they continue to work but the canonical path is direct
  import of `split_frontmatter` / `strip_frontmatter` from `schema`.

### Architecture

`docs/architecture/sync.md` (new) documents the conflict policy, the
fs-watch lifecycle, and the **MCP-daemon-as-fast-path principle**:

> MCP-daemon work is the fast-path; hooks are the correctness fallback.
> If the MCP server crashes, hook-driven work still fires on Claude
> lifecycle events and produces the same end state.

The fs-watcher is the first daemon thread inside the MCP server; the
ADR documents the principle so future work (e.g. detaching Curator A
trigger from hooks) inherits the same shape.

### Tests

- 21 new tests for `git_sync` against bare-repo+two-clone fixtures
  (auto_pull, auto_push happy path, LLM-merge stub, regenerable
  ours-wins, unknown bail, classifier parametrisation)
- 9 new tests for `reindex_watcher` (state primitives, watchdog
  integration, optional-dep fallback)
- 2 new tests for the dirty-flag bypass of the throttle
- 1 reworked test in `test_curator_b_briefing_integration` (renamed
  from "unsupported sink" to "unknown sink" — matrix is now
  registered, so the test now uses an unregistered scheme to exercise
  the same skip-and-log path)
- Total: 1551/1551 passing (was 1520 in 0.10.6)

## [0.10.6] — 2026-04-26

Phase 9d — frontmatter splitter consolidation. Six sites collapsed to
one canonical implementation in `lore_core/schema.py`.

### Changed

- **`lore_core.schema` adds `split_frontmatter()` and `strip_frontmatter()`**
  as the canonical YAML-frontmatter splitter. `parse_frontmatter()` now
  delegates to `split_frontmatter()` rather than repeating the
  start-fence/closing-fence logic inline.

- **Six call sites consolidated** — `lore_core/migrate.py`,
  `lore_curator/defrag_curator.py`, `lore_curator/daily_curator.py`,
  `lore_core/session_writer.py`, `lore_search/fts.py`, and
  `lore_mcp/server.py` (which had its own inline `txt.startswith("---\\n")`
  parser) now all share the canonical implementation. The local helpers
  (`_split_frontmatter`, `_strip_frontmatter`) remain as one-line
  re-export aliases so call sites don't need rewriting.

- **`lore_curator/abstract._strip_leading_frontmatter`** — kept as a
  specialised variant (it `lstrip("\\n")`s first and requires the
  candidate to yaml-parse-to-dict, both LLM-output safety nets the
  canonical doesn't need) but rebuilt on top of the shared splitter
  rather than reimplementing the fence search.

### Why

Five different bespoke implementations of "find `---` … `\\n---`" was
a low-stakes invitation for divergence. The simplifier review flagged
this; consolidation costs nothing and makes the next bug-fix obvious.

## [0.10.5] — 2026-04-26

Phase 9b — P1 quick wins from the multi-agent review synthesis. No new
features; cleanup the Phases 0–8 sweep missed plus README polish.

### Fixed

- **Telemetry leaked vendor naming** — `skip_reason="no_anthropic_client"`
  (and the matching log event `reason="no-anthropic-client"`) survived
  Phase 0's `anthropic_client → llm_client` rename in two places
  (`lib/lore_curator/session_curator.py:385`, `lib/lore_curator/daily_curator.py:83`).
  Renamed to `no_llm_client` / `no-llm-client`. The whole point of
  Phase 0 was to stop spelling "anthropic" in user-facing telemetry.

- **Legacy SessionStart cache writer was perpetually populated.** The
  pre-PID-keying cache file `last-session-start.md` was documented as
  "deprecated, read-only fallback" since 0.9.0, but `hooks.py:996-999`
  kept overwriting it on every SessionStart. Its mtime never aged out,
  so the deprecation could never fire. Removed the writer; the read-
  fallback in `_context_log` stays for one release and drops in 0.11.0.

### Removed

- **`lore_search/backend.py`** (-57 LOC). The `LoreBackend` Protocol had
  one implementer (`FtsBackend`) and zero typed consumers — every
  caller imported `FtsBackend` directly. Moved `SearchHit` next to
  `FtsBackend` in `fts.py`. When a second backend lands (Qdrant, Chroma,
  …), reintroduce the Protocol *with* the second implementer in the same
  patch, not before.

### Internal

- **`ruff --select F401` sweep** removed 35 unused imports across `lib/`
  (`lore_adapters/cursor_agent.py`, `lore_curator/session_curator.py`,
  `lore_curator/daily_curator.py`, etc.). No behaviour change.

### Documentation

- **README install path unified.** The canonical install used to live in
  `## Install`, but `## Bootstrap > Fresh install` re-instructed with a
  different ordering and `[capture]` extras, and `## As a Claude Code
  plugin (via marketplace)` added a third path. New: `## Install` is
  the single canonical block (with `[capture]` extras as default);
  `Fresh install` was removed in favour of a back-link; `Update from
  an older install` now mirrors the canonical command.

- **README Observability now leads with `lore status`.** The previous
  three-row table mentioned `lore runs show latest` but not the
  exemplary 7-line activity-first dashboard `lore status` — promoted
  to the first row with the framing "Is Lore doing anything for me
  right now?" plus a one-paragraph explainer.

- **CONTRIBUTING broken link removed.** Internal artifact (a
  `~/.claude/plans/give-these-considerations-to-melodic-castle.md`
  reference) leaked into the public file in a prior commit. Replaced
  with a generic "PR description / docs/REVIEW-*.md" pointer.

## [0.10.4] — 2026-04-26

Phase 9c — second half of the host → integration rename. 0.10.3 covered
the install/CLI surface; this release renames the **runtime data model**
(adapters, ledger, frontmatter) so the codebase is fully consistent.

### Changed (breaking — pre-1.0; one-release back-compat for ledger reads)

- **Adapter Protocol** (`lore_adapters.Adapter`): `host: str` →
  `integration: str`. All four built-in adapters (claude-code, cursor,
  manual-send, vscode-copilot) renamed accordingly.
- **Registry**: `UnknownHostError` → `UnknownIntegrationError`,
  `registered_hosts()` → `registered_integrations()`. `get_adapter()`
  parameter `host` → `integration`.
- **Turn dataclass** (`lore_core.types.Turn`): `host_extras` →
  `integration_extras`. In-memory only; no persistence impact.
  Adapter-specific keys inside (`cursor.raw_content`, `claude_code.unknown_block`,
  etc.) keep their integration-name namespacing.
- **TranscriptHandle**: `host: str` → `integration: str`.
- **TranscriptLedgerEntry**: `host: str` → `integration: str`. JSON
  field key in `transcript-ledger.json` renamed `"host"` → `"integration"`.
  **Reads accept either key for one release** (drop in 0.11.0); writes
  always emit `"integration"`. Existing ledgers continue to work.
- **session-note frontmatter**: `source_transcripts[].host` →
  `source_transcripts[].integration`. Existing notes continue to read
  via parse, but new notes are written with the new key. No reader inside
  Lore programmatically depends on the field; it's display + drill-down.
- **`classify_tool_name`** (`lore_core.tool_categories`): first parameter
  renamed `host` → `integration`.
- **Capture hook**: `lore hook capture --host <name>` →
  `--integration <name>`. Hook event JSON envelope renames the same key.
- **`lore ingest`**: `--host <name>` → `--integration <name>`. Internal
  `manual_send.declared_host` extras key → `manual_send.declared_integration`.
- **Adapter call signature**: `read_from(declared_host=...)` →
  `read_from(declared_integration=...)`.

### Why a hard cut

Pre-1.0 cut-hard policy. The single carve-out is the ledger JSON read
fallback — keeps existing user vaults' transcript-watermark history
intact across the upgrade so the curator doesn't re-derive everything
from scratch.

## [0.10.3] — 2026-04-26

Vocabulary cleanup: **"host" now means *machine*, "integration" means
*AI tool*** (Claude Code, Cursor, OpenCode). Two reviewers (architect +
UX) flagged the overload — `attachments.json` calls itself "host-local"
to mean "this machine," while `lore_cli/hosts.d/*.toml` used "host" for
"AI integration target." The same word for two unrelated concepts was
making cross-host sync discussions ambiguous.

### Changed (breaking — pre-1.0; no compat alias)

- **Directory rename**: `lib/lore_cli/hosts.d/` → `lib/lore_cli/integrations.d/`
- **Directory rename**: `templates/host-rules/` → `templates/integration-rules/`
- **Env var**: `LORE_HOSTS_DIR` → `LORE_INTEGRATIONS_DIR`
- **CLI flag**: `lore install --host <name>` → `lore install --integration <name>`
  (also affects `check`, `upgrade`, `uninstall`, `reinstall` subcommands and
  `lore uninstall`).
- **JSON envelope**: `lore install --json` now emits `"integrations": [{"integration": ...}]`
  in place of `"hosts": [{"host": ...}]`.
- **Public API** (`lore_core.install`): `known_hosts()` → `known_integrations()`,
  `get_host()` → `get_integration()`. `Action.on_failure` constant
  renamed `ON_FAILURE_ABORT_HOST` → `ON_FAILURE_ABORT_INTEGRATION`
  (string value also: `"abort_host"` → `"abort_integration"`).
- **Launcher API** (`lore_cli.launcher`): `HostConfig` → `IntegrationConfig`,
  `list_hosts` → `list_integrations`, `load_host` → `load_integration`.
  `lore resume --launch HOST` → `--launch INTEGRATION` (metavar only;
  flag name unchanged).
- **TOML schema**: `hosts.d/*.toml`'s `lore.host/1` schema label →
  `lore.integration/1`. (Comment-only — nothing parses the label.)
- Internal data-model field renames (`Adapter.host`, `Turn.host_extras`,
  `LedgerEntry.host`, etc.) ship in **0.10.4** — the second half of the
  rename.

### Why a hard cut

Pre-1.0 policy is "nothing is sacred yet." The compat-alias path for
`LORE_HOSTS_DIR` and `--host` would carry deprecation comments without
removal targets — the simplifier review specifically flagged that
pattern as anti-discipline.

## [0.10.2] — 2026-04-26

### Fixed

- **`threads.md` headline counts full paths, not basenames.** Real-world
  bug: a thread of 6 notes about curator development was labelled
  `SKILL.md` because one note touched 4 different `SKILL.md` paths
  (`skills/quiet/SKILL.md`, `skills/off/SKILL.md`, …) and the
  label-counter aggregated by basename — so one note's 4 different
  paths beat `curator_b.py`'s 3 cross-note votes. Union-find was
  already correct (it used full paths to *group* notes); only the
  display-label aggregation was wrong. Now each `(note, path)` pair
  contributes one vote, and the chosen full path's basename is used
  purely for display. Two new tests lock the behaviour in.

## [0.10.1] — 2026-04-26

### Fixed

- **`lore install` and `lore doctor` now detect Python-package version
  drift** (issue #28). The Claude Code plugin and the Python CLI binary
  update via separate channels (`claude plugin update` vs `pipx
  install`); when they drift, SessionStart's status line silently
  shows the binary's old version even after a successful plugin
  update. Both commands now compare `importlib.metadata.version("lore")`
  against the on-disk `pyproject.toml` and surface a copy-pasteable
  fix command (`pipx install --force --editable <repo>`) when they
  disagree. Advisory check (does not block installs) — the CLI still
  functions at the older version, just visibly.

## [0.10.0] — 2026-04-26

Cleanup-roadmap closeout (Phases 0-8). User-visible CLI/slash changes
warrant a minor bump; no breaking changes — every legacy form keeps
working via aliases.

### Added

- **`lore wiki new <name>`** — canonical home for wiki-lifecycle
  verbs going forward; matches `lore surface init/add/commit`. The
  legacy `lore new-wiki <name>` keeps working as an alias and prints
  a one-line stderr hint pointing at the new form.
- **Role-named curator modules**:
  - `lore_curator/session_curator.py` (was `curator_a.py`) — files
    session notes from completed transcripts.
  - `lore_curator/daily_curator.py` (was `curator_b.py`) — extracts
    surfaces and regenerates `threads.md`.
  - `lore_curator/defrag_curator.py` (was `curator_c.py`) — weekly
    defrag/stale-flag/supersession.
  Function aliases `run_session_curator` / `run_daily_curator` /
  `run_defrag_curator` are added alongside the legacy `run_curator_a/b/c`
  names; all old import sites continue to work.

### Changed

- **Slash command renamed**: `/lore:surface-new` → `/lore:surface-add`
  for symmetry with `lore surface add`. The skill directory was
  renamed via `git mv` (history preserved); autocomplete now shows
  the new name.
- **Skill cleanup**: `skills/lint/SKILL.md` and `skills/curator/SKILL.md`
  now call the `lore` CLI directly (`lore lint`, `lore curator`,
  `lore migrate`) instead of leaking internal package paths
  (`python -m lore_core.lint` etc.).
- **SKILL.md description sharpening**: `/lore:lint` and `/lore:curator`
  descriptions revised to make their distinct roles obvious to Claude
  (mechanical-vs-judgment) so picker reliability improves.

### Notes

- `tests/test_skill_cli_drift.py` is the static guard against future
  `python -m lore_*` regression in skills.
- `tests/test_cli_wiki.py` pins both the canonical `lore wiki new`
  path and the legacy `lore new-wiki` alias.
- Curator module renames are mechanical: ~25 import sites migrated;
  function aliases mean ~188 callers of `run_curator_a/b/c` keep
  working unchanged.

## [0.9.0] — 2026-04-25

Surface-extraction quality push (full notes in
`docs/superpowers/HANDOVER-2026-04-19.md` and the v0.9.0 commit). This
entry also closes a long version-sync drift: `.claude-plugin/plugin.json`
was stuck at 0.5.0 while `pyproject.toml` advanced to 0.9.0, meaning
`claude plugin update lore@lore` silently reused cached code. A pytest
guard (`tests/test_version_sync.py`) now fails CI if the two sources
disagree.

> **Note on the gap.** Versions 0.4.0 through 0.8.2 shipped without
> changelog entries; their notes live in commit messages
> (`git log --grep="Lore v0\."`). They will be backfilled in a future
> docs pass.

## [0.3.0] — 2026-04-22

Local-Lore-state release. Replaces the distributed `## Lore` CLAUDE.md
routing model with a host-local registry + optional `.lore.yml` offer.
CLAUDE.md is no longer a routing artifact. See issue #22 and
`docs/superpowers/plans/2026-04-22-local-lore-state-plan.md`.

### Added

- **Host-local state** — `$LORE_ROOT/.lore/attachments.json` (which
  paths route where) and `$LORE_ROOT/.lore/scopes.json` (the scope
  tree, flat ID-as-path with wiki-inheritance).
- **`.lore.yml` offer format** — optional checked-in repo file
  declaring `wiki`, `scope`, `backend`, `issues`, `prs`, and
  `wiki_source`. Fingerprinted over routing fields only so non-routing
  tweaks don't re-prompt users who accepted.
- **Consent state machine** — `UNTRACKED | OFFERED | ATTACHED |
  DORMANT | MANUAL | DRIFT` surfaced by a non-blocking notice in
  SessionStart when `.lore.yml` is pending acceptance.
- **Registry CLI** — `lore attach {accept, decline, manual, offer}`,
  `lore attachments {ls, show, rm, purge-unattached}`,
  `lore scopes {ls, show, rename, reparent, rm}`. Scope rename/reparent
  propagates across both state files atomically.
- **Doctor extensions** — `lore doctor` validates attachments (path
  exists, wiki dir exists, scope in tree, fingerprint matches) and
  scope-tree integrity; surfaces `__orphan__` / `__unattached__` ledger
  buckets with actionable suggestions.
- **Migration tool** — `lore migrate attachments` (one-shot,
  idempotent, dry-run) converts legacy `## Lore` CLAUDE.md blocks into
  `.lore.yml` + registry rows and strips the section from CLAUDE.md
  (surrounding content preserved).
- **Reinstall shortcut** — `lore install reinstall` composes
  `uninstall` + `install` in one step.

### Changed

- `resolve_scope(cwd)` is registry-only — longest-prefix match on
  `attachments.json`. No filesystem walk-up. O(log n) lookup.
- Ledger's `pending()` / `pending_by_wiki()` default resolver is
  bound to the ledger's own `lore_root`, not `$LORE_ROOT` env —
  simplifies test fixtures.
- `_walk_up_lore_config` is now a registry-backed shim that returns
  a synthetic `claude_md_path` sentinel plus a block dict derived from
  the resolved scope (merged with any `.lore.yml` at the attachment
  path for non-routing fields).

### Removed

- Legacy `## Lore` CLAUDE.md walk-up resolver (`_legacy_walk_up_resolve`).
- Lazy-migration hook in the legacy resolver (transition-only,
  superseded by explicit `lore migrate attachments`).
- `TranscriptLedger._resolve_wiki_cached` + cache dict (redundant now
  that longest-prefix match is O(log n)).
- Legacy `lore attach read` / `lore attach write` commands (replaced
  by `lore attach accept|manual|offer` + `lore attachments show`).

### Migration

On machines with existing `## Lore` blocks in CLAUDE.md:

```
lore migrate attachments --dry-run   # preview
lore migrate attachments --yes       # apply
```

Idempotent. Re-runs are no-op. Preserves surrounding CLAUDE.md content.

## [0.2.4] — 2026-04-21

### Fixed

- **Capture hooks were never registered with Claude Code after v0.2.3**.
  `.claude-plugin/plugin.json` gained `SessionEnd` + `lore hook capture`
  wiring for `SessionStart`/`PreCompact` in commit `004d033`, but the
  package version wasn't bumped. `claude plugin update lore@lore` had
  nothing to re-fetch, so installed plugin caches stayed on the
  pre-capture manifest — the banner hook fired but the capture hook
  never did, and no transcripts were ever ledgered unless curator was
  run by hand. Bump forces a re-fetch.
- `lore_search` FTS5 index auto-migrates from the legacy contentless
  schema (which couldn't DELETE). `/lore:resume <keyword>` and
  `lore_search` MCP calls no longer raise
  `cannot DELETE from contentless fts5 table: notes_fts`.

### Added

- `lore hook capture` now emits a `hook-events.jsonl` record with
  `outcome="no-scope"` when the cwd isn't inside a configured wiki
  instead of silently returning. Makes "hook never fired" vs "hook
  fired but declined" distinguishable in `lore status` and
  `lore runs list --hooks`.
- `lore status` gains a `Hook` line between `Last run` and `Pending`
  (`· Hook  12m ago · session-start · spawned-curator`) plus a
  loud-on-earning alert when pending > 0 AND no hook events in 24h.
- `lore runs list --hooks` prints a diagnostic banner when runs
  exist but `hook-events.jsonl` is empty/missing.

## [0.2.3] — 2026-04-18

### Fixed

- **Slash commands lost their `lore:` namespace prefix in v0.2.0**.
  When skill directories were renamed from `skills/lore:<name>/`
  to `skills/<name>/` and the SKILL.md frontmatter `name:` was
  set to the bare value, Claude Code's picker started showing
  bare slash commands (`/init`, `/resume`, `/inbox`, …) — colliding
  with built-ins like Claude Code's own `/init`.
- **Restored explicit scoping in SKILL.md frontmatter**:
  `name: lore:<bare>` (literal colon). Other plugins like
  `frontend-design:frontend-design` use the same pattern; Claude
  Code uses the frontmatter `name` field verbatim as the slash
  command name. Directory names stay bare; only the in-frontmatter
  name carries the prefix.

  After `claude plugin update lore@lore`, slash commands appear in
  the picker as `/lore:resume`, `/lore:loaded`, `/lore:init`, etc.
  No collision with built-ins; explicit namespace always visible.

## [0.2.2] — 2026-04-18

### Fixed

- **`claude plugin update lore@lore` failed** with "destination is
  empty after copy" because the v0.2.0 marketplace.json used a
  `github` source object pointing back at `buchbend/lore` — the
  same repo as the marketplace itself. Claude Code's update path
  for github-source plugins (clone source repo → copy into
  versioned cache) appears to mishandle this self-reference and
  produces an empty cache.
- **Switched to `source: "./"`** (validated cleanly with
  `claude plugin validate`). The marketplace root IS the plugin
  root in our setup; Claude Code uses the marketplace clone
  directly, no separate github source clone, no copy-step bug.

## [0.2.1] — 2026-04-18

### Fixed

- **Top-level `lore --help` was still argparse-style** (bare list of
  subcommand names). The 0.2.0 typer migration covered the leaves
  but `__main__.py` still used the legacy SUBCOMMANDS lookup. Now a
  proper `typer.Typer()` root mounts every subcommand via
  `add_typer`, so `lore --help` shows the Rich-boxed command tree
  with descriptions.
- **`lore mcp --help` hung waiting for stdin** because the MCP
  server's `main()` was bare argparse-free and ignored `--help`.
  Wrapped in a typer app with a no-arg callback so help works
  without starting the STDIO loop.
- **`lore migrate`** was still on argparse; migrated to typer.
- **`lore uninstall`** alias preserved as a top-level typer command
  forwarding to `install_cmd._cmd_install` with `mode="uninstall"`
  (same flags as `lore install uninstall`).

## [0.2.0] — 2026-04-18

### Added

- **`lore install` / `lore uninstall`** — multi-host installer
  dispatcher (Claude Code + Cursor in v1) with print-and-confirm UX,
  per-host plans, schema versioning per host module, semantic-undo
  contract, `--force --yes` refusal, legacy-artifact detection.
  Replaces the 340-line `install.sh`.
- **`lore doctor`** — smoke-test subcommand: LORE_ROOT, wikis, cache,
  MCP server, FTS backend, SessionStart hook, attach block.
- **`lore_core.resume.gather()`** — unified resume entry point covering
  no-arg / wiki / keyword / scope modes. `/lore:resume` skill now does
  one MCP call instead of ~6 iterative Glob/Read/Grep.
- **`lore_core.session.scaffold()`** + `lore session new` /
  `lore session commit` — split `/lore:session` into MCP scaffold-read
  + visible CLI write/commit. Subagent goes from 6–8 tool calls to ~3.
- **`lore_core.briefing.gather()`** + `lore briefing
  {gather,publish,mark}` — split `/lore:briefing` into deterministic
  MCP gather + LLM prose composition + visible CLI publish.
- **`lore_core.inbox.classify()`** + `lore inbox
  {classify,archive}` — same shape for `/lore:inbox`.
- **`lore resume <topic> --launch <host>`** — standalone launcher
  pre-warms a fresh agent session with a gathered context block.
  TOML host registry at `lib/lore_cli/hosts.d/*.toml` for cross-host
  dispatch (Claude + Cursor in v1).
- **3 new MCP tools**: `lore_session_scaffold`, `lore_briefing_gather`,
  `lore_inbox_classify` (now 9 total).
- **`/lore:loaded`** — renamed from `/lore:why`; matches the
  SessionStart status line text. Cache stores full text (truncation
  only on inject).
- **Vault-first directive** in SessionStart `additionalContext` and
  re-asserted in PreCompact (`templates/host-rules/default.md` is
  the single source of truth, used by hooks + Cursor rules file).
- **`tools/undo_install_sh.py`** — stdlib-only Python helper to
  cleanly reverse the legacy `install.sh` mutations.
- **CONTRIBUTING.md** — dev-mode install recipe, "filing a host
  module" guide, version-bump convention.

### Changed

- **CLI migrated from argparse to typer + Rich** across all 12
  subcommands. Pretty `--help` boxes, type-coerced options, future
  shell-completion. `lib/lore_cli/_compat.py:argv_main()` keeps the
  legacy `main(argv) -> int` contract for tests + the SUBCOMMANDS
  dispatcher.
- **Skill directory names dropped the `lore:` prefix** — Claude Code's
  plugin namespace supplies the prefix. `skills/lore:loaded/` →
  `skills/loaded/`. Slash commands stay `/lore:loaded` etc.
- **`--json` envelopes** standardised across `lore.<verb>/N` schemas
  for attach, detach, search, lint, curator, resume, session,
  briefing, inbox, doctor, install.
- **`install.sh`** shrunk from 340 lines to a ~50-line deprecation
  shim pointing users at `lore install`.
- **`lore-thesis.md`** reframed from "plugin with CLI underneath" to
  "CLI-first that ships plugins per host." Token-economy principle is
  now the architectural backbone (gather → CLI/MCP, synthesis at
  write/maintenance time, no synthesis at retrieval).
- **`.claude-plugin/marketplace.json`** schema fixed
  (`source: {"source": "github", "repo": "buchbend/lore"}`,
  `metadata.description` instead of root-level `description`).
- **`.claude-plugin/plugin.json`** declares `hooks` + `mcpServers`
  inline (Claude Code's plugin system wires them; install no longer
  mutates `~/.claude/settings.json`).

### Fixed

- **PyPI name `lore` is squatted** by an unrelated package
  (`lore 0.8.6`, broken on Python 3.13). Install path uses
  `pipx install git+https://github.com/buchbend/lore.git` until a
  clean PyPI name is picked (issue #9).
- **Marketplace registration step missing** in `lore install --host
  claude` — added `claude plugin marketplace add buchbend/lore`
  with `on_failure=continue` so re-runs don't wedge.
- **Install error messages printed literal `[red]...[/red]`** markup
  tags — `markup=False` (added for ANSI safety) suppressed the
  wrapper colour. Now uses `rich.markup.escape()` on user-derived
  content with `markup=True` on the wrapper.
- **`lore install --json` mixed Rich legacy warning with JSON
  envelope** — warning now suppressed when `--json` is set;
  artifacts ride in `legacy_artifacts` field.

### Filed as known gaps

- **#6** — `LORE_ROOT` portable resolver (`~/.config/lore/config.toml`
  fallback for host-agnostic resolution).
- **#7** — Verify `lore_mcp.server` protocol compatibility with
  Cursor's MCP client.
- **#8** — Windows support for `lore install` and the Cursor host
  adapter.
- **#9** — Pick a clean PyPI name (`lore` is squatted).

## [0.1.0] — 2026-04-17 (initial alpha)

Initial public release. Linter, schema v2 with `## Issues touched` /
`## Loose ends` sections, MCP server, FTS5 search, curator (stale
detection + supersession + git-date backfill), session-writer
subagent, briefing sinks (Matrix, markdown), `lore attach` /
`lore detach`, scope-prefix `lore resume --scope`, identity +
team-mode machinery.
