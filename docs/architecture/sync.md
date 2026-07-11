# Cross-host sync

**Status:** active (introduced in 0.11.0)
**Replaces:** the dataclass-shaped placeholder `WikiConfig.git.{auto_pull, auto_push}` that shipped without callers in 0.3.0–0.10.6.

This ADR captures the conflict policy and runtime model behind Lore's
cross-host sync layer. **"Host" here means *machine*** — a user's
laptop and workstation are two hosts of the same wiki.

---

## Vision pillar this addresses

> Cross-host: a single user works from multiple machines; their vault
> syncs. Cross-team: team members write sessions; cross-team synergies
> surface automatically.

Both pillars rest on a single mechanism: **the wiki is a git repo, and
git is the sync layer.** The system needs to keep the local copy
current with the remote *without manual `git pull`/`git push` rituals*,
and to resolve the conflicts that multi-host editing inevitably
produces — without producing temp artifacts or losing content.

---

## Sync surfaces

### When auto_pull fires

| Trigger | Where | What |
|---|---|---|
| **SessionStart** | `lore_cli/hooks.py` (existing hook) | Fetch + fast-forward each attached wiki on a clean tree. ~200ms latency for a clean tree. Skipped silently when no remote, dirty tree, or non-FF history. |
| Manual | `lore status` shows divergence; user runs `git pull` themselves | When auto-pull bailed (dirty/diverged), the user is the next line of defence. |

### When auto_push fires

| Trigger | Where | What |
|---|---|---|
| **Curator A post-commit** | `lib/lore_curator/session_curator.py:_maybe_auto_commit` (existing) | After auto-commit of a freshly filed session note or appended chapter. |
| LLM-merge follow-up | `lib/lore_core/git_sync.py:auto_push` | When push fails non-FF, run LLM merge, re-push. |

`auto_push` is `auto_pull` + `git push`, with conflict-resolution baked in.

### What never gets pushed

- `.lore/` directory (host-local state — `attachments.json`, `transcript-ledger.json`, lockfiles, hook events)
- `.transcripts/` directory (raw conversation logs; gitignored on purpose, see `transcript_sync.py:42-50`)

---

## Conflict policy

Three classes of conflict, each with a deterministic resolver.

### 1. Session notes — pre-pull eliminates conflicts in steady state

Session notes live at `<wiki>/sessions[/<handle>]/<YYYY>/<MM>/<DD>-<slug>.md`.
Two hosts can in principle produce the same path on the same day with
the same slug. **The pre-pull at SessionStart eliminates this in steady
state**: when Host B starts a session, it pulls Host A's commits first,
sees the same-day note, and the existing append-to-today logic (in
`session_writer.py:_find_todays_open_note`) merges the new chunk into
that one note — same code path that handles concurrent same-host
sessions today.

**Edge case fallback:** if the pre-pull is skipped (offline, dirty
tree, network failure), and Host B writes its own day-note that later
collides with Host A's on push, the LLM-merge resolver kicks in. The
two notes get merged into one canonical body. No `.host-A.md` siblings.

### 2. Manually-authored notes — LLM-merge on push conflict

Notes in a wiki's typed subdirectories (`concepts/`, `decisions/`,
`projects/`, …) are written directly — by hand or via `/lore:inbox` —
there is no automatic abstraction pass that populates them, so this
conflict class is rarer than session-note conflicts in practice. The
classifier and merge path still apply to whatever two hosts
independently write there:

1. `git fetch origin`
2. `git merge origin/<branch> --no-commit --no-ff`
3. Enumerate conflicted paths: `git diff --name-only --diff-filter=U`
4. Classify each path. For a typed-subdirectory note:
   - Read OURS (HEAD), THEIRS (origin), BASE (merge-base)
   - LLM call (middle tier, ~$0.001) with structured prompt: "merge
     these two versions of `<note>`. Preserve all distinct facts,
     deduplicate restated points, keep wikilinks from both sides."
   - Write merged result, `git add <path>`
5. If all conflicts resolved: `git commit -m "merge(auto-llm): N
   note(s)"`, `git push`
6. If any unresolved: `git merge --abort`, log via `lore status`,
   surface a `lore curator merge --resolve` action to the user.

LLM-merge at push time is synchronous: by the time Host B's push
completes, the wiki has one canonical note. No temp artifacts, no
drift window where MCP search would see two notes about the same
thing.

### 3. Regenerable artifacts — pick either side, lint to truth

`_catalog.json`, `_index.txt`, `threads.md` are all
regenerated from the corpus. On conflict, accept either side
(`git checkout --ours <path>`), then run `lore lint` after the merge
to truth them up. They'll converge to the same byte-for-byte output
on either host's next regen.

### 4. Unknown — bail to user

Anything else (CLAUDE.md root files, hand-edited markdown outside
sessions/typed-notes) → `git merge --abort`, log via `lore status` with
the path list and a `lore curator merge --resolve <path>` hint. Never
silently overwrite hand-edited content.

---

## Reindex invalidation — the MCP daemon

The MCP server (one process per Claude session, lifetime = session)
hosts a `watchdog.observers.Observer` daemon thread watching
`<lore_root>/wiki/` recursively. On `*.md` create/modify/delete, the
watcher sets `dirty[<wiki>] = True` in a process-local dict. The next
`lore_search(wiki=<x>)` call sees the flag, force-reindexes that wiki,
clears the flag.

**Without `watchdog`** (optional dep, in `[search]` extras): the
existing 5-second throttle is the only invalidation path. Behaviour
regresses to "post-pull, MCP search may be stale for up to 5s." This
is what 0.10.x ships today, so the optional-dep fallback is not a
regression.

**Self-edit handling:** the curator's own writes (session notes,
chapters, hygiene-pass frontmatter edits) trip the watcher too. That's
fine: the dirty flag just says "next query may need fresh data." The
throttle prevents a reindex storm.

### Future direction (not for 0.11.0)

The fs-watch is the first daemon thread inside the MCP server. The
pattern it establishes:

```
MCP server (lifetime = Claude session)
├── stdio JSON-RPC handler (existing tools)
├── fs-watch daemon thread → invalidates reindex cache
└── [future]                → e.g. detach Curator A trigger from hooks?
```

**Principle: MCP-daemon work is the fast-path; hooks are the
correctness fallback.** If the MCP server crashes, hook-driven work
still fires on Claude lifecycle events and produces the same end
state. Don't migrate things to the MCP daemon that hooks can't replay.
Don't add cron-style timers there — only activity-triggered work
(file events, MCP queries). This keeps Lore consistent with its
heartbeat-not-cron design.

After 0.11.0 ships, candidates for migration are: Curator A trigger,
`auto_pull` cadence (today only on SessionStart). Decide post-shipping
based on observed behaviour, not now.

---

## Configuration

Per-wiki, in `<wiki>/.lore-wiki.yml`:

```yaml
git:
  auto_commit: true     # the curator commits its own writes
  auto_push: true       # push after each commit
  auto_pull: true       # fetch + ff at SessionStart
```

Defaults: `auto_commit=false`, `auto_push=false`, `auto_pull=true`.
The push/commit defaults stay false-by-default in 0.11.0 to avoid
surprising users whose wiki repos are in odd states; flipping them
to true is a one-line per-wiki opt-in. `auto_pull` defaults to true
because it's read-only on a clean tree.

---

## Failure modes catalog

| Mode | Behaviour | Surfaced via |
|---|---|---|
| No remote configured | Skip silently | (none) |
| Remote unreachable | Skip silently, retry on next trigger | `spine.jsonl` |
| Dirty working tree (uncommitted user edits) | Skip pull, log it | `lore status` warning |
| Clean tree, fast-forwardable | Fetch + ff | `lore status` (silent unless new commits pulled) |
| Clean tree, diverged (we have local commits, remote has different commits) | Skip pull, surface to user | `lore status` `· wiki diverged — git pull manually` |
| Push: remote ahead, FF possible | Fetch + ff + push | (silent) |
| Push: typed-note conflict | LLM-merge → push | `lore status` shows merge count |
| Push: unresolvable conflict | abort merge, surface to user | `lore status` `· merge needed: <paths>` |
| `watchdog` not installed | Reindex throttle = 5s natural decay | (silent — same as 0.10.x) |

---

## Test surface

- Bare-repo fixtures for two hosts (`tmp_path/host_a`, `tmp_path/host_b`,
  shared `tmp_path/origin`)
- `auto_pull`: clean tree → ff; dirty tree → skip + log; diverged → skip + status flag
- `auto_push`: typed-note conflict → LLM-stub merge → assert merged file present, no
  `.host-*` siblings, exit code 0
- `auto_push`: session-conflict → LLM-stub merge → assert one canonical file
- `auto_push`: regenerable conflict (`_catalog.json`) → ours wins → lint reconciles
- `auto_push`: unknown-path conflict → `git merge --abort` → status flag
- `reindex_watcher`: write a `.md`, assert dirty flag within 50ms, assert next
  `lore_search` reindexes
- `reindex_watcher`: import-error fallback when `watchdog` not installed
