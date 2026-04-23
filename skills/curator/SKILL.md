---
name: lore:curator
description: Per-wiki maintenance — flag stale notes, detect superseded
  decisions, backfill created / last_reviewed from git log, write a
  review summary the next SessionStart can surface. Frontmatter-only
  edits. Run with "/lore:curator <wiki>".
user_invocable: true
---

# Curator

Keeps the vault's metadata trustworthy so SessionStart's auto-injection
surfaces only current, active knowledge.

## What it does

Four passes per wiki (all frontmatter-only — bodies are never touched
without user approval):

1. **Staleness** — notes with `status: active` + `last_reviewed > 90
   days` ago become `status: stale`.
2. **Supersession** — when note A says "supersedes [[B]]", B becomes
   `status: superseded` with `superseded_by: [[A]]`.
3. **Git backfill** — notes missing `created` / `last_reviewed` get
   them from `git log --follow`.
4. **Review summary** — writes `wiki/<name>/_review.md` listing every
   action taken; SessionStart surfaces the count as part of its
   one-liner.

## Safety

- **Mtime guard**: read mtime before patch, re-read and abort if it
  changed mid-patch (Obsidian edit race).
- **Obsidian-open warning**: detects `.obsidian/` in the vault tree;
  proceeds but warns the user.
- **Dry-run by default**: `--apply` required to write. `/lore:curator
  <wiki>` without `--apply` shows the diff.
- **No body edits**: staleness, supersession, and backfill are all
  frontmatter-only.

## Workflow

```bash
# 0. Git pull
git -C $LORE_ROOT/wiki/<wiki> pull --ff-only

# 1. Dry run — review what would change
python -m lore_cli curator --wiki <wiki>

# 2. Apply
python -m lore_cli curator --wiki <wiki> --apply

# 3. Commit
git -C $LORE_ROOT/wiki/<wiki> add -A
git -C $LORE_ROOT/wiki/<wiki> commit -m "lore: curator pass YYYY-MM-DD"
```

## Scheduling

No default cadence — pick your trade-off (see README):

- `/schedule /lore:curator <wiki>` — laptop-local, free
- `cron` + `claude -p "/lore:curator <wiki> --apply"` — headless, free
- GitHub Actions on push to the wiki repo — always-on, API cost
- Home server + cron — always-on, free

Examples in the plugin's `examples/` directory.

## Output surfaced to future sessions

Next SessionStart reads `_review.md` (or its absence means "all clear")
and folds the count into the one-liner status:

```
lore: loaded <wiki> (N notes, M open items, 3 stale flagged) · /lore:context
```
