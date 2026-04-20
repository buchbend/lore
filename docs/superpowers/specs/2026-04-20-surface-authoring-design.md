# Surface Authoring — Design Spec

**Date:** 2026-04-20
**Status:** Design — pending implementation plan
**Supersedes:** N/A (extends §7 of `2026-04-19-passive-capture-v1-design.md`)

## 1. Motivation

Surface authoring is a semantic task — deciding *what* a surface captures, *when* Curator should extract one, and *what* fields it requires. The current CLI (`lore surface init`, `lore surface add <name>`) is a typed-args primitive that can't help with any of those choices: every `add` leaves the user with a `TODO: describe this surface` stub to hand-edit, every `init` picks one of four shipped templates that almost never fit a specific wiki.

This spec adds LLM-assisted authoring as the default path — two skills (`/lore:surface-new` for single-surface addition, `/lore:surface-init` for whole-vocabulary design) — while preserving deterministic primitives for automation.

## 2. Goals

- Make surface authoring feel like a short, guided conversation with a Curator-aware Claude, not YAML hand-editing.
- Produce surfaces that fit the specific wiki (LLM reads actual notes + existing vocabulary, not just template names).
- Keep deterministic CLI/MCP primitives for scripts, CI, tests, and advanced users — progressive disclosure.
- Enrich SURFACES.md with `plural`, `slug_format`, `extract_prompt` — closes known correctness gaps in directory naming and slug generation, and gives Curator better extraction hints.
- Single source of truth for the `standard` template (no more `_DEFAULT_STANDARD` duplicate in code).

## 3. Non-goals

- Rename / remove of existing surfaces (including migration of existing note `type:` frontmatter). Follow-up spec.
- Deep lint cross-checks (do declared surfaces have matching directories? do existing notes' `type:` values all map?). Follow-up spec.
- Unified `--json` flag across all surface subcommands. Follow-up spec.
- Surface-level `schema_version` (per-surface versioning). Spec-version 2 stays single top-level integer.
- `aliases:` field. Dropped; LLM does overlap detection without it for now.
- Automated quality assertions on LLM synthesis output. Skill conversation correctness is manual-test only.

## 4. Architecture

Three layers, clean boundaries:

**Skill layer** (`.claude/skills/lore/surface-new.md`, `surface-init.md`, plugin-namespaced as `lore:surface-new` / `lore:surface-init`). Drives conversation. Invokes LLM for synthesis. Owns all user-facing copy. User-facing term is "Curator" (not "Curator B") — the A/B/C split is internal.

**MCP layer** — two new tools, both deterministic, never write files:
- `lore_surface_context(wiki)` — returns the context pack the skill feeds to the LLM.
- `lore_surface_validate(wiki, draft)` — takes a draft-spec (shape per §5), returns validation issues + rendered markdown + diff preview.

**CLI layer:**
- `lore surface add [--wiki <name>]` — thin launcher; resolves wiki (flag or cwd) and exec's `claude "/lore:surface-new <wiki>"`.
- `lore surface init [--wiki <name>]` — same shape, exec's `claude "/lore:surface-init <wiki>"`.
- `lore surface commit <draft.json>` — deterministic write primitive. Validates via shared library, appends or writes SURFACES.md. Only write path.
- `lore surface lint` — unchanged today; gains the new-key validations described in §5.
- `lore new-wiki --surfaces <template>` — unchanged, keeps shipped templates as the automation / no-LLM path.

**Shared core** (`lib/lore_core/surfaces.py`) — one parser, one renderer (draft-spec → markdown section / full file), one validator. Used by MCP validate, CLI commit, CLI lint.

**Write boundary:** only `lore surface commit` touches the filesystem. MCP never writes. This mirrors the existing `lore_session_scaffold` / `lore session commit` pattern.

## 5. Schema changes to SURFACES.md

Three new YAML keys per surface section, all optional, backwards-compatible with existing files:

```yaml
required: [type, created, description, tags]
optional: [draft]
plural: papers
slug_format: "{citekey}"
extract_prompt: |
  A publication note. Extract when a paper is discussed with concrete findings.
  Prefer citekey over title for slug.
```

`SurfaceDef` (in `lib/lore_core/surfaces.py`) gains three fields:

```python
@dataclass(frozen=True)
class SurfaceDef:
    name: str
    description: str
    required: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    extract_when: str = ""
    plural: str | None = None          # NEW
    slug_format: str | None = None     # NEW
    extract_prompt: str | None = None  # NEW
```

Unknown YAML keys keep the existing warn-and-skip behavior (forward-compat preserved).

Top-level `schema_version` stays at 2. The new keys are additive, not a breaking change.

### 5.1 Lint coverage for new keys

Added in the scope of this spec (other lint improvements are follow-up):

- `plural`: must match `^[a-z][a-z0-9_]*$` when present (same rule as surface name).
- `slug_format`: must be a valid Python `str.format` template. Legal placeholders: `{date}` (ISO-8601 date of the note), `{title}` (raw title string), `{slug}` (slugified title, fallback), and any key named in the surface's `required` or `optional` field lists (e.g., `{citekey}` for a paper). Unknown placeholders fail lint.
- `extract_prompt`: must be a non-empty string when present.
- No two surfaces may resolve to the same directory after applying `plural` / `_pluralise(name)` fallback.

## 6. Draft-spec contract (`draft.json`)

The skill produces this; the MCP validates it; `lore surface commit` consumes it. One format, two shapes selected by `operation`.

Single-surface append (flow A):

```json
{
  "schema": "lore.surface.draft/1",
  "wiki": "science",
  "operation": "append",
  "surface": {
    "name": "paper",
    "description": "Citekey-named publication note.",
    "required": ["type", "citekey", "title", "authors", "year", "description", "tags"],
    "optional": ["draft", "status"],
    "extract_when": "a paper is discussed with concrete findings",
    "plural": "papers",
    "slug_format": "{citekey}",
    "extract_prompt": "A publication note. Prefer citekey over title for slug."
  }
}
```

Full-set init (flow B):

```json
{
  "schema": "lore.surface.draft/1",
  "wiki": "science",
  "operation": "init",
  "schema_version": 2,
  "surfaces": [ {...}, {...} ]
}
```

### 6.1 `lore surface commit` semantics

- `operation: append` — refuses if `surface.name` already exists in SURFACES.md (exit 1) unless `--force`. Creates minimal header (`# Surfaces\nschema_version: 2\n`) if SURFACES.md is missing, then appends the rendered section.
- `operation: init` — refuses if SURFACES.md already exists unless `--force`. Writes the full file from scratch.
- Validates via the shared validator before writing. Exit 0 on success with JSON receipt (`{"schema": "lore.surface.commit/1", "data": {...}}`) on stdout. Exit 1 with rendered issues on stderr on failure.

### 6.2 Draft escape hatch

When the skill user chooses "save as draft" instead of commit:

- Skill writes `$LORE_ROOT/drafts/surfaces/<wiki>-<name>.json` (append) or `$LORE_ROOT/drafts/surfaces/<wiki>-init.json` (init) via a simple file write from the skill itself. `$LORE_ROOT/drafts/` already exists as part of the vault shape scaffolded by `lore init`.
- Skill prints the `lore surface commit <path>` command for the user to run later.
- Existing `$LORE_ROOT` `.gitignore` entries govern whether the draft lands in git. The skill does not modify `.gitignore`; that's the user's call.

### 6.3 Why JSON (not YAML)

Machine-to-machine contract between skill, MCP, CLI. SURFACES.md itself stays markdown-with-YAML for human authors. JSON drafts are ephemeral.

## 7. Skill conversation flows

Both skills use synthesis-first with optional deepen (Option 3 from brainstorming). User-facing copy says "Curator" everywhere.

### 7.1 `/lore:surface-new <wiki>` — flow A

1. **Context turn** (silent, calls `lore_surface_context`): reads current SURFACES.md, wiki CLAUDE.md attach block, samples ~3 recent notes per existing surface type, includes shipped templates as inspiration material.
2. **Open question**: *"Describe this surface in your own words — what does it capture, and when should Curator extract one?"*
3. **Synthesis**: LLM proposes a full draft-spec (per §6). Overlap check is LLM-driven against existing SURFACES.md names + descriptions ("this sounds a lot like your existing `decision` — extend that instead?"). Skill invokes `lore_surface_validate` on the draft; validator issues feed the next turn.
4. **Present**: rendered `## <name>` section + diff preview + any validator warnings in plain prose. Offer: *"Commit, deepen a field, or save as draft?"*
5. **Commit path**: skill writes draft to a temp JSON file, invokes `lore surface commit <path>`, reports receipt (SURFACES.md path + the new surface's wikilink).
6. **Deepen path**: user names the field; skill asks field-specific question, updates draft, re-validates, re-presents.
7. **Draft path**: skill writes `drafts/surfaces/<wiki>-<name>.json` and prints the `commit` command.
8. **Deep scan** (opt-in): at step 2 user can say "scan first"; skill runs `lore_search` against the wiki, presents clusters of uncategorized notes that might fit the surface concept, then continues from step 3.

### 7.2 `/lore:surface-init <wiki>` — flow B

Same pattern, one open question instead:

- **Open question**: *"What's this wiki for, and what kinds of things do you want to capture? Rough list or free-text description both work."*
- **Synthesis**: LLM produces a whole SURFACES.md (header + 3-6 internally-consistent surfaces, with shared naming conventions, no overlap between surfaces, consistent field schemas). Shipped templates (`standard`, `science`, `design`) appear in the synthesis prompt as *inspiration* but are not picked wholesale.
- **Present**: full rendered file + diff against current (usually "new file"). Offer the same three options (commit, deepen one surface, save as draft).
- **Deepen-one-surface** drops into a mini flow-A loop for a single surface; other surfaces in the draft are preserved unchanged.

### 7.3 Error handling

- `claude` not on PATH (CLI launchers) → helpful error with install pointer.
- MCP server unavailable → skill refuses to start with actionable error; CLI `commit` still works for hand-written drafts.
- Validator fails → skill shows issues as rendered prose and asks user to refine; skill does not silently patch.
- User aborts mid-conversation → draft is lost unless they chose "save as draft" first.
- Commit-time duplicate / existing-file clashes → error messages point to `--force` and to `lint`.

## 8. MCP tool contracts

### 8.1 `lore_surface_context(wiki: str) -> ContextPack`

```json
{
  "schema": "lore.surface.context/1",
  "wiki": "science",
  "wiki_dir": "/path/to/wiki/science",
  "surfaces_md_exists": true,
  "current_surfaces": [ /* parsed SurfaceDef dicts */ ],
  "claude_md_attach": "text of the lore attach block",
  "note_samples": {
    "concept": ["[[2026-04-02-foo]]", "[[2026-03-15-bar]]", "[[2026-03-01-baz]]"],
    "decision": ["[[...]]"]
  },
  "shipped_templates": {
    "standard": "contents of standard.md",
    "science": "contents of science.md",
    "design": "contents of design.md"
  }
}
```

Sampling: up to 3 most recent notes per existing surface type by `created` frontmatter date. Skip types with no notes. `custom.md` is not included (it's a placeholder, not inspiration).

### 8.2 `lore_surface_validate(wiki: str, draft: DraftSpec) -> ValidationResult`

```json
{
  "schema": "lore.surface.validate/1",
  "wiki": "science",
  "ok": false,
  "issues": [
    {"level": "error", "code": "plural_collision", "message": "plural 'papers' collides with existing surface 'paper'"}
  ],
  "rendered_markdown": "## paper\n...",
  "diff_preview": "--- a/SURFACES.md\n+++ b/SURFACES.md\n@@ ..."
}
```

- Validates shape per §5.1 plus general surface-def rules (required ∩ optional = ∅, name shape, YAML parseability).
- `rendered_markdown` is the section (append) or full file (init).
- `diff_preview` is unified-diff against the current SURFACES.md (or `/dev/null` for init on a fresh wiki).
- Pure function: reads files, returns result, never writes.

## 9. Consumer changes

### 9.1 `lib/lore_curator/surface_filer.py`

- `_pluralise(name: str)` → `_directory_for(surface_def: SurfaceDef)`. Returns `surface_def.plural or _pluralise(surface_def.name)`.
- `_slug(title: str)` → `_slug(title: str, surface_def: SurfaceDef, ctx: dict[str, Any])`. If `surface_def.slug_format` is set, interpolates via `str.format(**ctx)`; else falls back to current behavior. `ctx` includes `date`, `title`, plus any frontmatter field the caller supplies (e.g., `citekey`).
- Callers updated to pass `surface_def` and a minimal ctx dict.

### 9.2 `lib/lore_curator/curator_b.py` (abstract step)

- When building the abstraction prompt for a given surface, include `surface_def.extract_prompt` if set, under a clearly-fenced section the model can anchor on.
- No behavior change when `extract_prompt` is absent — existing prose description + `Extract when:` still drive extraction.

### 9.3 `lib/lore_core/surfaces.py`

- `_DEFAULT_STANDARD` removed.
- `load_surfaces_or_default(wiki_dir)` re-implemented to read `standard.md` from `lore_core.surface_templates` package resources and parse it on-demand (cached). Same external contract.
- New `render_section(surface_def) -> str` — renders a single `## <name>` section. Used by CLI `commit` (append) and MCP `validate` (preview).
- New `render_document(schema_version, surfaces, wiki) -> str` — renders a full SURFACES.md with preamble + sections. Used by CLI `commit` (init) and MCP `validate` (preview for init).
- New `validate_draft(draft: dict, wiki_dir: Path) -> list[Issue]` — shared validator shared between MCP `lore_surface_validate` and CLI `commit`. Returns structured issues per §5.1.

### 9.4 `lib/lore_cli/surface_cmd.py`

- `init` / `add` CLI commands rewritten as thin exec launchers (see §4).
- `lint` retained; gains the new-key validations from §5.1.
- `commit <path>` added (new subcommand).
- `_BARE_HEADER` stays — used by `commit` append when SURFACES.md is missing.
- `_load_template` / `TEMPLATE_NAMES` stay — used by `new_wiki_cmd` (`--surfaces` flag, automation path) and read by MCP `lore_surface_context` as skill inspiration material. Not used by `commit` itself (init writes the user-authored full file from the draft, not from a shipped template).

## 10. Testing

### 10.1 Unit

- Parser/renderer round-trip on all three new keys (`plural`, `slug_format`, `extract_prompt`).
- Validator cases per §5.1: plural collision, invalid slug_format placeholder, malformed `plural` identifier, empty `extract_prompt`, required ∩ optional disjoint, schema-version check.
- `surface_filer._directory_for` override + fallback.
- `surface_filer._slug` interpolation with full ctx, missing ctx key, no `slug_format` fallback.
- `load_surfaces_or_default` reads packaged `standard.md` (no more module-level duplicate).

### 10.2 CLI integration

- `lore surface commit <draft.json>` — append happy path, append-dup rejection, `append --force`, init happy path, init-existing rejection, `init --force`, validator-fail rejection.
- `lore surface lint` — existing cases retained; new cases for plural collision, invalid slug_format, forward-compat unknown keys still warn + skip.
- `lore surface add` / `lore surface init` launchers — resolve wiki correctly; shell out to `claude` with the right args. Tests use a stub `claude` on PATH to assert invocation shape without running a real session.
- `lore new-wiki --surfaces <template>` — end-to-end still produces a valid SURFACES.md.

### 10.3 MCP integration

- `lore_surface_context(wiki)` — golden-file test against a fixture wiki with two existing surfaces and three sampled notes per type.
- `lore_surface_validate(wiki, draft)` — happy path + each §5.1 failure mode.

### 10.4 Skill-level

- Build realistic `draft.json` fixtures (from manual skill runs) for both flows. Feed through MCP validate + CLI commit, assert final SURFACES.md matches expected.
- Skill synthesis quality (conversation correctness) is explicitly out of scope for automated testing — manual verification only.

### 10.5 Curator integration

- Extend existing `test_curator_b_*` fixtures to include `extract_prompt` on at least one surface.
- Assert the prompt fragment appears in the abstraction prompt sent to the model.

## 11. Migration / rollout

- Existing SURFACES.md files without the new keys continue to work unchanged — the three new fields are optional and default to `None`.
- Today's `lore surface add <name>` syntax becomes an error (the launcher doesn't accept a positional name; that's now the skill's job). Messaging points users at `lore surface add` (no args) which opens the skill. This is a breaking change for anyone who wrote scripts calling `lore surface add <name>`; the replacement is a two-line draft JSON + `lore surface commit`. Documented in the release note.
- `_DEFAULT_STANDARD` removal is internal; no external API changes.
- Curator reads the new `extract_prompt` only when set — zero impact on wikis that don't migrate.

## 12. Open questions (resolve during plan-writing)

- Plugin-namespace verification: the skill files will ship as part of the `lore` plugin; `name:` in frontmatter must be `lore:surface-new` / `lore:surface-init` (per prior `feedback_claude_code_plugin_namespace` memory).
- Stub `claude` shim for launcher tests — decide shape (file on `$PATH` via fixture vs. env-var override that `surface_cmd` checks for `CLAUDE_BIN`).
- `drafts/surfaces/` gitignore integration — skill writes to it; should it also ensure the path is ignored? (Lean: yes, one-time prompt on first draft save.)

## 13. References

- `docs/superpowers/specs/2026-04-19-passive-capture-v1-design.md` §7 — original SURFACES.md spec this extends.
- `docs/superpowers/plans/2026-04-19-graph-abstraction-plan.md` — Phase A tasks that shipped the current CLI being rewritten.
- `feedback_curator_naming` memory — user-facing copy uses "Curator" only.
- `feedback_progressive_disclosure` memory — design principle driving the CLI/MCP/skill split.
