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

The chain: `orient → grilling → to-epic → build → document`, with `handover` at any stop.

| Skill | What it's for |
|-------|----------------|
| `orient` | First step of a task: homework, then reflect understanding back. A light mode ("brief me") pulls only the context pack and hands one change to `build`. |
| `grilling` | Interview the user to stress-test a plan, and sharpen the domain model: `CONTEXT.md` terms and ADRs with `Holds` and `Revisit if`. |
| `to-epic` | Turn a plan into a PRD file plus an epic tracker issue with a roadmap table. |
| `build` | Build code in three modes: `loop` (fast rounds, local merges, one wrap-up PR), `issue` (one issue, one PR), `epic` (a roadmap, teammates, review by risk level). Keeps a ledger and writes a handover section. |
| `tdd` | Test-driven red-green-refactor loop. |
| `debug` | Systematic root-cause debugging with a hard circuit breaker. |
| `document` | Bring the Diátaxis docs in line with the code: pre-merge, catch-up, or a plan-approved consolidation of a docs tree. Marks a shipped PRD at the epic tail. |
| `handover` | Write the handover section for work that stops early, and turn follow-up work into a seed issue. |
| `file-issue` | Writes issue text and files it: resolve the writing rules, draft, Vale-lint, then post the issue or PR body. |
| `triage` | Reviews every open issue and PR of a repo against the code, gives each a verdict, and presents one filterable page for discussion. Review only until the user decides. |

Onboard a repo with `lore attach --scaffold-workflow`.
