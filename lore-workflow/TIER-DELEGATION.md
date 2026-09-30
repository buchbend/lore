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

**Frontier main-session note.** Some steps (`grilling`, `handover`) run
**in the main session**, at frontier-tier reasoning, and are never
delegated to a subagent at all — there is no spawn to resolve. That is a
different rule from the one above and the two are not interchangeable:
delegation-point skills resolve a tier for a spawn; main-session skills
simply state they don't spawn.

## Workflow-specific tier choices

**Implementation teammates** (`build`, `epic` mode, Dispatch): **advisory**. Assess each
feature: `mid` for mechanical or well-scoped changes, `strong` for architectural, ambiguous or
cross-cutting ones. Pass the tier to `lore tier resolve` and set the spawn's model parameter to
the result. A deviation is allowed; adequate output at `mid` beats the cost of `strong`.

**Review passes** (`build`: the per-PR review in `issue` and `epic` mode, the whole-epic
review): **set by the risk level**. `lore workflow risk <pr> --json` prints `low` or `high`.

| Risk level | Reviewer tier | Correctness pass |
|---|---|---|
| `low` | `mid` | `code-review low` |
| `high` | `strong` | `code-review medium` |

The agent may raise the level and never lowers it. The reviewer does not judge CI or ruff: the
`build` run reads both from `gh pr checks`.

**Exploration fan-out** (`orient` full mode, step 2): **required `mid`**. Parallel explorers
(code map, docs, cross-repo scan) run at `mid`. A cheaper tier skips the depth; `frontier`
wastes tokens on mechanical discovery.

**Triage lenses** (`triage`, step 3): **`strong`** for the security and architect lenses,
**`mid`** for the ops lens. The security lens must not miss an exposure; ops checks are
mostly mechanical.

**Background suite** (`build`, `loop` mode): **`cheap`**. The full suite and ruff after each
round are bulk-mechanical: run, read the exit code, report the failing test ids.

**Advisory pass** (`build`, loop wrap-up): **`strong`**. The architect and web-design reviewers
read a cumulative diff and rank refactor suggestions.

A smaller plan remaps the tiers instead of skipping steps: override `frontier` and `strong` in
the vault config (see `docs/model-tiers.md`, "User overrides").
