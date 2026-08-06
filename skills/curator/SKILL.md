---
name: lore:curator
description: Propagate `supersedes:` and `implements:` relations, backfill
  missing dates from git, and hint when a wiki has outgrown solo mode.
  Frontmatter-only judgement calls — never edits note bodies. Use for
  routine vault hygiene; pair with `lore lint` when indexes are also
  stale. Run with "/lore:curator <wiki>".
user_invocable: true
---

# Curator

Keeps the vault's frontmatter trustworthy so SessionStart's context
surfaces only current, active knowledge.

Passes per wiki, all frontmatter-only — note bodies are never touched:

1. **Supersession** — note A says "supersedes [[B]]" → B gets
   `superseded_by: [[A]]`. Only the relation is written; `status:` is
   never set (lifecycle is derived at read time).
2. **Implements-propagation** — reads `implements: <slug>` frontmatter
   from notes under `sessions/` and drops `draft:` on the target,
   stamping `implemented_by` / `implemented_at` back-links;
   `implements: <slug>:superseded-by:<other>` writes `superseded_by:
   [[other]]`. Nothing writes a new session note since the compose
   pipeline retired, so this pass only ever finds back-links on notes
   that predate the retirement.
3. **Git backfill** — missing `created` / `last_reviewed` filled from
   `git log --follow`.
4. **Team-mode hint** — advises creating `_users.yml` once a solo wiki
   shows multiple distinct git authors.
5. **Review summary** — writes `wiki/<name>/_review.md` listing every
   action; SessionStart surfaces the count in its one-liner.

Staleness is **not** flagged by age: it is positive-evidence-only at
read time (a broken wikilink or an authored marker), so the curator's
staleness pass is a deliberate no-op.

## Workflow

```bash
git -C $LORE_ROOT/wiki/<wiki> pull --ff-only
lore curator --wiki <wiki>            # dry run; review the diff
lore curator --wiki <wiki> --apply    # write
git -C $LORE_ROOT/wiki/<wiki> commit -am "lore: curator pass YYYY-MM-DD"
```

Dry-run is the default; `--apply` is required to write. Mtime guards
detect concurrent Obsidian edits and abort the patch.

## Scheduling

No default cadence. See README for trade-offs across `/schedule`,
`cron + claude -p`, GitHub Actions, and home-server cron. Examples in
`examples/`.
