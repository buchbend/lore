---
name: lore:curator
description: Mark notes stale by mtime, propagate `supersedes:` and
  `implements:` status flips, backfill missing dates. Judgment calls
  on metadata only — never edits note bodies. Use for routine vault
  hygiene; pair with `lore lint` when indexes are also stale. Run
  with "/lore:curator <wiki>".
user_invocable: true
---

# Curator

Keeps the vault's metadata trustworthy so SessionStart's auto-injection
surfaces only current, active knowledge.

Four passes per wiki, all frontmatter-only — bodies are never touched
without user approval:

1. **Staleness** — `status: active` + `last_reviewed > 90 days` ago →
   `status: stale`.
2. **Supersession** — note A says "supersedes [[B]]" → B becomes
   `status: superseded` with `superseded_by: [[A]]`.
3. **Git backfill** — missing `created` / `last_reviewed` filled from
   `git log --follow`.
4. **Review summary** — writes `wiki/<name>/_review.md` listing every
   action; SessionStart surfaces the count in its one-liner.

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
