# lore-workflow

Companion plugin to [`lore`](../README.md): deterministic epic/PRD/TDD
workflow skills, ported from `ccatobs/ccat-agent-workflow` (see PRD 0003).

**Dependency direction: `lore-workflow` depends on `lore`; `lore` never
depends on `lore-workflow`.** Workflow skills call `lore`'s CLI/MCP surface
(`lore codemap` / `lore_codemap`, `lore tier resolve`, `lore workflow
validate-roadmap` / `create-prd`, `lore attach --scaffold-workflow`); nothing
in `lore` core imports or requires this plugin. This keeps `lore` installable
standalone and lets `lore-workflow` stay opt-in.

Versioned independently from `lore` — see `.claude-plugin/plugin.json`. Tier
delegation conventions shared across skills live in
[TIER-DELEGATION.md](./TIER-DELEGATION.md).

## Bundled skills

| Skill | What it's for |
|-------|----------------|
| `ccat-workflow-init` | Onboard a repo — a thin pointer at `lore attach --scaffold-workflow`. |
| `orient` | First step of a task — homework, then reflect understanding back before planning. |
| `grilling` | Interview the user relentlessly to stress-test a plan or design — "grill with docs" mode also drives `domain-modeling`. |
| `domain-modeling` | Build and sharpen a project's domain model (CONTEXT.md, ADRs). |
| `to-epic` | Turn a plan/PRD into a PRD file plus an epic tracker issue with a roadmap DAG. |
| `orchestrate-epic` | Supervise parallel TDD implementation of an epic — plan, dispatch, crosscheck, land. |
| `implement-issue` | Fast path for one well-understood GitHub issue, outside the epic chain. |
| `tdd` | Test-driven red-green-refactor loop. |
| `debug` | Systematic root-cause debugging with a hard circuit breaker. |
| `document-epic` | After an epic merges, update Diátaxis docs to match the implemented state. |
| `seed-epic` | End a session by turning follow-up context into an epic-seed tracker issue. |
