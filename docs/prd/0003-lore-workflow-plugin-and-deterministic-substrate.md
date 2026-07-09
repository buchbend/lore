---
title: lore-workflow plugin + deterministic substrate
status: draft
epic: https://github.com/buchbend/lore/issues/161
repos:
  - buchbend/lore
  - ccatobs/ccat-agent-workflow
---

# PRD 0003: lore-workflow plugin + deterministic substrate

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/161).
> The epic links here; this file is not embedded in the issue body.

## Problem

The ccat-agent-workflow plugin (14 skills: orient → grill → to-epic →
orchestrate-epic, plus tdd, debug, document-epic, implement-issue, seed-epic,
domain-modeling and the grilling subsystem) reliably produces consistent
planning, vertical-slice epics, ADRs/PRDs in the right repo homes, and
enforced TDD — but it lives outside Lore, duplicating exactly the plumbing
Lore owns:

- Its deterministic code map (`CODEMAP.md`, committed per repo, Python-only,
  refreshed by a per-repo SessionStart hook) needs per-repo wiring and CI
  freshness gates, and covers only Python symbols.
- Its model-tier contract (`MODEL-TIERS.md`) resolves tiers to concrete models
  through a Markdown table plus prose repeated near-verbatim across five
  skills; it has a single Claude Code column, while Lore already knows which
  host it is installed into (host registry: Claude Code, Cursor, Copilot).
- Its Python underbelly (`roadmap_validator.py`, `prd_docs.py`, `diataxis.py`,
  `ccat_workflow_init.py`, `require_spawn_model.py`) is skill-local: outside
  any package, untyped, host-path-fragile, tested in a separate repo.
- Its repo onboarding (`ccat-workflow-init`) overlaps with Lore's repo
  attach/onboarding — two parallel init paths for one repo.

Meanwhile the lore repo itself has no CI, so nothing can be migrated into it
safely.

## Solution

Fold the workflow into the lore monorepo as a **second plugin** —
two marketplace entries, `lore` (notes/vault) and `lore-workflow` (planning
skills) — with independent versions and opt-in install, sharing the repo, the
Python package, the tests, and CI. The workflow hard-depends on the `lore`
CLI/MCP (dependency direction: workflow → lore, never reverse), enforced by a
runtime preflight rather than a formal plugin-dependency mechanism.

The deterministic substrate moves into the package:

- **Code map** becomes `lore codemap` (generator in `lore_core`): gitignore-
  aware single discovery pass, all-files repository inventory layer, ranked
  Python symbols via stdlib `ast`, multi-language symbols (JS, Vue, Rust,
  Julia, HTML, …) via an optional tree-sitter extra, fingerprint from git blob
  SHAs. Delivery is hybrid: a gitignored local `CODEMAP.md` written by Lore's
  own SessionStart hook (for subagents, grep, humans) plus a `lore_codemap`
  MCP tool serving queryable slices from a fingerprint-keyed cache. The
  committed-CODEMAP.md model is deprecated; ccatobs/ccat-agent-workflow#86 is
  superseded by this PRD.
- **Model tiers** become host-keyed data with a `lore tier resolve <tier>`
  resolver (CLI + MCP). Non-Claude host columns are seeded with provisional
  best-guess models; docs explain how users override the mapping. The
  spawn-model PreToolUse gate ships in the package and is wired by Lore's
  installer on the Claude Code host only. Per-host enforcement adapters are a
  later epic.
- **Workflow scripts** move to `lib/lore_workflow/` as `lore workflow`
  subcommands under lore's pytest/ruff/mypy regime. `ccat-workflow-init`
  merges into Lore's repo onboarding as a workflow-scaffolding step — one
  onboarding command per repo, ever.

The 14 skills port essentially verbatim, with only the rewires: tier prose →
resolver calls, CODEMAP references → the new generator/MCP tool, and the
five-fold tier boilerplate deduplicated into one shared reference. Deeper
lightening (orient consuming context packs, orchestrate-epic prose diet,
handovers on session notes) is deliberately a follow-up epic.

ccat-agent-workflow stays frozen during the migration and ends with a
deprecation pointer to the new marketplace entry.

## Implementation decisions

- **Monorepo, two plugins**: `marketplace.json` gains a `lore-workflow` entry
  with its own `plugin.json`; the release convention (version bump + changelog
  in one commit) extends to a second version axis, guarded by the existing
  version-sync drift tests.
- **CI first**: GitHub Actions running pytest and ruff (check + format) on
  push/PR is the prerequisite slice; everything else lands behind it.
- **Dependency direction**: `lore-workflow` skills may invoke `lore` CLI/MCP;
  nothing in `lore` references workflow skills. A preflight in the workflow
  entry skills verifies `lore` is installed and minimally recent.
- **Code map generator**: one gitignore-aware discovery pass (`git ls-files`,
  documented non-git fallback) feeds both the inventory layer (per-directory
  file counts/sizes/extensions — bounded for huge repos) and symbol
  extraction. Python via stdlib `ast` always; other languages behind a
  `lore[codemap]` extra using tree-sitter with per-language tag queries — no
  hand-rolled regex parsers. Fingerprint from `git ls-files -s` blob SHAs so
  any tracked-file change trips regeneration at near-zero cost.
- **`lore_codemap` MCP tool**: query interface (symbols matching a pattern,
  inventory of a directory, top-ranked N) so consumers pull 30 relevant rows
  instead of a 400-line file; cache keyed on the same fingerprint.
- **Tier data**: tiers remain semantic and ordinal (frontier > strong > mid >
  cheap, cheap reserved for bulk-mechanical); the resolution table is data
  keyed by the host registry, user-overridable via config; `lore tier resolve`
  is the single resolution point skills cite.
- **Skill port**: structural guarantees of the old `tests/validate.py`
  (no hardcoded model names in skills, tiers defined, README/skill-table
  sync, line budgets) are reimplemented as pytest tests in lore.

## Testing decisions

External behavior over implementation detail, following lore's existing
fine-grained pytest style (205 test files, drift tests like
`test_skill_cli_drift.py` as prior art):

- Code map: golden-file tests on fixture trees (inventory shape, symbol
  ranking, fingerprint no-op, non-git fallback, degradation without the
  tree-sitter extra).
- Tier resolver: table completeness per registered host, override precedence,
  unknown-tier/host failure modes.
- `lore workflow` subcommands: port the existing behavior tests from
  ccat-agent-workflow (roadmap validator DAG cases, prd_docs toctree
  idempotency, diataxis routing).
- Skill structure: pytest-based structural checks replacing `validate.py`.
- Plugin manifests: schema validity and version-sync for both entries.

## Out of scope

- Rewiring skill *behavior* beyond the named rewires (orient still fans out
  explorers; orchestrate-epic prose untouched) — follow-up lightening epic.
- Per-host spawn-gate enforcement adapters (Cursor, Copilot) — later epic,
  verified per host.
- Curator changes, linkage frontmatter, `lore_context_pack` — PRD 0004.
- Any functional change to ccat-agent-workflow (frozen; deprecation pointer
  only).
