# Tier delegation

Every subagent spawn in this plugin names a **semantic tier**
(`frontier` / `strong` / `mid` / `cheap`), never a concrete model. Resolve
the tier to a concrete model at spawn time:

```
lore tier resolve <tier>
lore tier resolve <tier> --host cursor
```

and pass the *result* as the spawn's model parameter. See
`docs/model-tiers.md` in the `lore` package for the full table, the
ordinal/collapse rule, the fallback behavior, and the cheap-tier
reservation (bulk-mechanical only).

**No-implicit-inherit.** A spawn with no explicit model resolution
silently inherits the orchestrating session's model — this both violates
the tier contract and burns frontier-tier tokens on work a cheaper tier
would do. Every delegation point in this plugin names its tier and
resolves it explicitly; none relies on inheritance.

**Frontier main-session note.** Some steps (`grilling`, `seed-epic`) run
**in the main session**, at frontier-tier reasoning, and are never
delegated to a subagent at all — there is no spawn to resolve. That is a
different rule from the one above and the two are not interchangeable:
delegation-point skills resolve a tier for a spawn; main-session skills
simply state they don't spawn.

## Workflow-specific tier choices

**Implementation teammates** (`orchestrate-epic` Dispatch step): **advisory tier
selection**. Assess each feature for complexity and choose a tier for its
implementation teammate — `mid` for mechanical or well-scoped changes, `strong` for
architectural, ambiguous, or cross-cutting ones. Pass the tier to `lore tier
resolve` and set the spawn's model parameter to the resolved result (see "Resolution at spawn time"
above). Deviations are allowed but recorded in the supervision trail; adequate
output at mid-tier is preferred to stronger-tier cost.

**Review passes** (implementation-teammate review in `implement-issue`, per-feature
crosscheck in `orchestrate-epic`, whole-epic review in `orchestrate-epic`):
**required strong-tier**. Reviewers carry the strong-tier's resolved model in the
spawn call (no implicit inheritance). The implementation-teammate tier is advisory
(see above); the review tier is not — it is always strong.

**Exploration fan-out** (`orient` step 2): **required mid-tier**. Parallel
explorers (code map, docs, cross-repo scan) run at mid; cheaper tiers skip
the depth, frontier-tier would waste tokens on mechanical discovery.

