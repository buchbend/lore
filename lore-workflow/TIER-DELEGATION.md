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
