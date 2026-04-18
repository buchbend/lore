---
name: attach
description: Attach the current repo or folder to a wiki scope by writing
  a managed `## Lore` section to CLAUDE.md. Interactive (three-question
  max), idempotent, non-destructive. Run with "/lore:attach".
user_invocable: true
---

# Attach-to-wiki

Writes (or upserts) the managed `## Lore` section in `CLAUDE.md` so this
directory becomes a scope anchor for the Lore knowledge vault. Content
outside the `## Lore` heading is never touched.

The mechanical work — parse, upsert, render — lives in `lore attach`.
This skill drives the interactive flow only.

## Workflow

### 1. Detect existing state

Run `lore attach read` (cwd is the default `--path`). Two cases:

- **Empty** (`{}`) — first-time attach. Continue to step 2.
- **Populated** — the directory is already attached. Show the current
  block to the user and ask: keep / re-scope / detach. If re-scope,
  continue to step 2 with the current values as suggestions.

### 2. Resolve wiki + scope (first question)

Auto-detection order:

1. **Git remote match.** Run `git remote get-url origin 2>/dev/null` to
   get the remote URL. For each wiki under `$LORE_ROOT/wiki/*/`, read
   `_scopes.yml` if it exists and scan leaves for `repo: <slug>`. If the
   remote matches exactly one leaf, the wiki and the scope are both
   known — ask the user to confirm.
2. **Multiple matches.** Ask the user which wiki (enumerated).
3. **No match.** Ask the user for wiki and scope.

Scope format: colon-separated, e.g. `ccat:data-center:data-transfer`.
When the wiki is known but the scope is not, suggest existing scopes
from that wiki's `_scopes.yml` (if any) and let the user pick or enter a
new one.

### 3. Backend

If the directory is inside a git repo with a `github.com` remote,
`backend: github` is the default. Otherwise `backend: none`. Do not
prompt — this is mechanical.

### 4. Issues + PRs filters (question two, optional)

Default values:

```
issues: --assignee @me --state open
prs:    --author @me
```

Show the defaults and ask once: "Accept defaults, or customize?" If the
user customizes, accept raw flag strings — they are forwarded verbatim
to `gh issue list` / `gh pr list` by the SessionStart hook.

When `backend: none`, skip this question entirely (the filters are
dormant).

### 5. Write

Call `lore attach write` with the resolved values:

```
lore attach write \
  --wiki <wiki> \
  --scope <scope> \
  --backend <backend> \
  --issues '<issues>' \
  --prs '<prs>'
```

The CLI is idempotent — re-running with the same spec is a no-op on disk.

### 6. Offer `_scopes.yml` append (question three, optional)

If the resolved scope is **not** already in `$LORE_ROOT/wiki/<wiki>/_scopes.yml`,
ask the user whether to add it. If yes:

- Locate or create `_scopes.yml` with a minimal skeleton.
- Append the scope path + the repo slug (from the git remote) as a leaf.
- **Stage** the edit in the wiki repo (`git add _scopes.yml`); do not
  commit. This matches the `/lore:session` convention — staging lets the
  user review before committing.

Skip this step silently if:

- The scope is already present in `_scopes.yml`.
- The folder is not inside a git repo (no slug to annotate with).

## Three-question ceiling

The skill asks at most:

1. Wiki + scope (skipped or reduced to confirm when auto-detected).
2. Accept default filters, or customize.
3. Append to `_scopes.yml`, yes/no.

If all three auto-resolve cleanly — matched remote, default filters,
scope already in `_scopes.yml` — the skill runs silently.

## Output

Print the resulting `## Lore` block back to the user, prefixed with
"Attached <path>:" — they should see exactly what was written.

## Important rules

- **Never touch content outside the `## Lore` section.** The CLI
  enforces this; don't try to edit `CLAUDE.md` directly.
- **Non-git folders are first-class.** `backend: none` is a valid,
  supported configuration (see [[git-aware-not-git-dependent]]).
- **Idempotent.** Re-running with the same inputs produces the same
  file contents. Safe to suggest on any directory.
- **Monorepo rule.** Nearest-ancestor `CLAUDE.md` with a `## Lore`
  section wins. If the user wants per-subdirectory scopes, they attach
  each one individually.
- **Don't commit the wiki-repo `_scopes.yml` edit.** Leave it staged.

## Related

- `/lore:detach` — companion: removes the `## Lore` section cleanly
- Concept: `claude-md-as-scope-anchor` (private wiki)
- Concept: `scopes-hierarchical` (private wiki)
- Decision: `git-aware-not-git-dependent` (private wiki)
- Tracked in [buchbend/lore#1](https://github.com/buchbend/lore/issues/1)
