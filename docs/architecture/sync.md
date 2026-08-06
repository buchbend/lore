# Cross-host sync

**Status:** active (introduced in 0.11.0; push transport restored by
epic #375 after issue #361 left it without a caller)
**Replaces:** the dataclass-shaped placeholder `WikiConfig.git.{auto_pull, auto_push}` that shipped without callers in 0.3.0–0.10.6.

This page captures the conflict policy and runtime model behind Lore's
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
| **Session boundary (SessionEnd hook)** | `lib/lore_cli/hooks.py` → `lib/lore_core/session_start.py:maybe_auto_push_for_scope` | Pushes the attached wiki once, after every flag filed that session is already committed (`lore_core/flag.py` commits each flag through `lore_core/session.py:commit_note` at write time). |
| Manual | `lore briefing --wiki <name>` (unless `--no-git`) | The briefing one-shot pulls before gather and pushes after mark — parked with briefings (PRD 0011), not part of the session-boundary path. |

`auto_push` is `auto_pull` + `git push`, with conflict-resolution baked
in — see "Conflict policy" below for what "resolution" means today.

### What never gets pushed

- `.lore/` directory (host-local state — `attachments.json`, `transcript-ledger.json`, lockfiles, hook events)
- `.transcripts/` directory (raw conversation logs; gitignored on purpose, see `transcript_sync.py:42-50`)

---

## Conflict policy

> **Status: parked.** `auto_push`'s `llm_client` parameter accepts a
> client, but no caller passes one — the session-boundary push (above)
> always calls it with none. A session-note or typed-note conflict
> therefore always ends in `MERGE_BLOCKED`, `git merge --abort` runs,
> and the working tree comes back clean; nothing below the "LLM call"
> step in class 2 currently executes. The classifier, the merge path
> and the prompt stay in the tree — the path resolves conflicts for the
> push this epic restored — what's on hold is the decision to run a
> model at the session boundary.

Four classes of conflict, each with a deterministic resolver.

### 1. Session notes — no longer written

Session notes lived at `<wiki>/sessions[/<handle>]/<YYYY>/<MM>/<DD>-<slug>.md`.
Nothing writes a new one since the compose pipeline retired (`#361`),
so two hosts can no longer produce a same-day collision going forward.
The classifier still recognizes a `sessions/` path as this conflict
class — for a wiki that still carries pre-retirement session notes —
and routes it through the same LLM-merge path as class 2, parked the
same way.

### 2. Typed-subdirectory notes — LLM-merge on push conflict

Notes in a wiki's typed subdirectories (`concepts/`, `decisions/`,
`projects/`, …) are written directly — by hand, via `/lore:inbox`, or
appended to by a flag (`lore_core/flag.py`) — there is no automatic
abstraction pass that populates them on its own. This is the conflict
class two hosts filing flags into the same topic note actually hit.
The classifier and merge path apply to whatever two hosts independently
write there:

1. `git fetch origin`
2. `git merge origin/<branch> --no-commit --no-ff`
3. Enumerate conflicted paths: `git diff --name-only --diff-filter=U`
4. Classify each path. For a typed-subdirectory note:
   - Read OURS (HEAD), THEIRS (origin), BASE (merge-base)
   - LLM call (middle tier, ~$0.001) with structured prompt: "merge
     these two versions of `<note>`. Preserve all distinct facts,
     deduplicate restated points, keep wikilinks from both sides."
     **Parked** — reached only when a caller passes an `llm_client`.
   - Write merged result, `git add <path>`
5. If all conflicts resolved: `git commit -m "merge(auto-llm): N
   note(s)"`, `git push`
6. If any unresolved (the current outcome for every typed-note
   conflict): `git merge --abort`, working tree returns clean. The
   user resolves it with git.

LLM-merge at push time is designed to be synchronous: by the time Host
B's push completes, the wiki would have one canonical note, no temp
artifacts, no drift window where MCP search would see two notes about
the same thing — once a caller opts a model into the session boundary.

### 3. Regenerable artifacts — pick either side, lint to truth

`_catalog.json`, `_index.txt`, `threads.md` are all
regenerated from the corpus. On conflict, accept either side
(`git checkout --ours <path>`), then run `lore lint` after the merge
to truth them up. They'll converge to the same byte-for-byte output
on either host's next regen.

### 4. Unknown — bail to user

Anything else (CLAUDE.md root files, hand-edited markdown outside
sessions/typed-notes) → `git merge --abort`. `lore status` computes no
alert for a blocked push; the SessionEnd hook's spine event carries
`push=merge-blocked`, the only trace of it today. `lore` mounts no
merge-resolution command for this — resolve the conflict with git
directly in the wiki repo, commit, and push. Never silently overwrite
hand-edited content.

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

**Self-edit handling:** a flag append and the hygiene curator's own
frontmatter edits trip the watcher too. That's fine: the dirty flag
just says "next query may need fresh data." The throttle prevents a
reindex storm.

### Future direction (not for 0.11.0)

The fs-watch is the first daemon thread inside the MCP server. The
pattern it establishes:

```
MCP server (lifetime = Claude session)
├── stdio JSON-RPC handler (existing tools)
├── fs-watch daemon thread → invalidates reindex cache
└── [future]                → e.g. detach auto_pull cadence from hooks?
```

**Principle: MCP-daemon work is the fast-path; hooks are the
correctness fallback.** If the MCP server crashes, hook-driven work
still fires on Claude lifecycle events and produces the same end
state. Don't migrate things to the MCP daemon that hooks can't replay.
Don't add cron-style timers there — only activity-triggered work
(file events, MCP queries). This keeps Lore consistent with its
heartbeat-not-cron design.

After 0.11.0 ships, `auto_pull` cadence (today only on SessionStart) is
a migration candidate. The compose pipeline's own mid-session spawn
trigger, once a second candidate, retired with the pipeline (`#361`).
Decide based on observed behaviour, not now.

---

## Configuration

Per-wiki, in `<wiki>/.lore-wiki.yml`:

```yaml
git:
  auto_push: true       # push at the session boundary
  auto_pull: true       # fetch + ff at SessionStart
```

Defaults: `auto_pull=true` — it's read-only on a clean tree, so there's
no surprise in leaving it on. `auto_push` defaults to
`git_sync.has_remote(wiki_dir)`: true for a wiki with a remote (a
shared vault, where a flag reaches a teammate only after a push), false
for a solo wiki with nowhere to push. A value written in the file
always wins over that default.

`git.auto_commit` is a schema field with no reader — `lore_core.flag`
commits every flag unconditionally at write time, through
`lore_core/session.py:commit_note`, regardless of this setting.

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
| Push: regenerable-artifact conflict | ours wins, `lore lint` reconciles | (silent) |
| Push: typed-note or session-note conflict | `MERGE_BLOCKED` — LLM-merge is parked, so `git merge --abort` runs and the tree returns clean | `push=merge-blocked` on the SessionEnd hook's spine event; `lore status` computes no alert for it today |
| Push: unknown-path conflict | abort merge, surface to user | `push=merge-blocked` on the SessionEnd hook's spine event; `lore status` computes no alert for it today |
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
