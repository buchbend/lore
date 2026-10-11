# Lore State Map

**Audience:** contributors who need to know which file answers "given this
cwd, which wiki applies?" and what Lore keeps on the local machine.

## Vocabulary

Three terms get conflated in casual conversation; they mean different
things. User-facing copy and contributor docs should keep them
distinct:

- **Vault** — the top-level directory pointed to by `$LORE_ROOT`.
  Contains exactly one `wiki/` subdirectory, plus `.lore/` for
  derived state.
- **Wiki** — a mounted knowledge store at `vault/wiki/<name>/`. Each
  wiki is an independent git repo. A vault may host one or many.
- **Scope** — a folder named in the person's config, such as
  `~/git/ccatobs`. Every repo below it gets the scope's wiki; the longest
  matching folder wins (ADR 0016).

When in doubt: vault is *the place*, wiki is *a store inside the
place*, scope is *a folder of repos that share a store*.

---

## TL;DR — what routes a repo

| File | Owner | Question it answers |
|------|-------|---------------------|
| `~/.config/lore/config.yml` | the person (`lore setup scope`, `lore config`) | "Which scope covers this folder, and which wiki does it use?" |
| `<repo>/.lore.yml` | the repo, committed | "Which exceptions does this repo carry?" |
| `<wiki>/_scopes.yml` | wiki maintainer | "Which repos does this wiki's taxonomy name?" (a tag catalog; nothing routes on it) |

`resolve_route(cwd)` (`lib/lore_core/route.py`) answers the question in
this order: a flag or environment variable, the nearest `.lore.yml`
between the working directory and the repo root, the scope with the
longest matching folder, a scope whose `remote` list matches the repo's
git remote, then the person's `defaults`. It is a pure lookup with a
per-process cache; it writes nothing.

The old `$LORE_ROOT/.lore/attachments.json` and `scopes.json` are gone.
`lore setup` writes one scope per old attachment into the person's config
and deletes both files.

---

## The transcript ledger stays local

A wiki with no git remote is **private by default** — nothing it holds
ever leaves the machine. A wiki with a git remote is a **shared
vault**: every teammate with read access to that remote can see every
note committed there. Lore writes to a wiki only on purpose (ADR 0012).

**The transcript ledger never leaves local state.** It lives at
`<lore_root>/.lore/transcript-ledger.json`, a sibling of `wiki/` —
never a descendant — so a `git push` of a wiki directory structurally
cannot ship it. Each ledger entry carries a `linkage`
block — `repo`, `branch`, `prs`, `issues`, `commits`, `files` — written
by capture with no LLM call. It is what `lore_drill` reads to answer
"which sessions touched X" and what the SessionStart recap renders from.
The block is derived and rebuildable, and stays machine-local with the
rest of the ledger. Only the wiki's notes are ever pushed.

---

## Concurrency-safety guarantees

Two state files are written from
hot paths and need to survive multiple concurrent Claude sessions:

- **`$LORE_ROOT/.lore/spine.jsonl`** — the append-only event spine.
  All background-pipeline telemetry shares one envelope here (hook
  events are the first producer; curator/drain/janitor follow).
  Concurrent appends are safe by POSIX semantics: every write is
  ``O_APPEND``-atomic for records ≤ ``PIPE_BUF`` (typically 4096
  bytes), and envelope records are well under that. No application-
  level lock needed for appends.
- **`$LORE_ROOT/.lore/spine.rotate.lock`** — sibling flock target
  for the rotation race. When the spine crosses ``max_size_mb``, two
  writers would otherwise both call ``os.replace()`` and lose a
  rotation window. ``SpineWriter`` takes a non-blocking
  ``fcntl.LOCK_EX`` here; losers skip and retry on the next emit.

The same pattern (``LOCK_EX | LOCK_NB`` on a sibling lock file) is
used by ``lore_core.lockfile.try_acquire_spawn_lock`` to serialise a
detached background-process launch — transcript sync at SessionStart
today. The curator's own turn-threshold auto-spawn read this same lock
before the heartbeat that decided when to take it retired with the
compose pipeline; `lore wiki curate` now runs only on demand, taking no
lock itself. See those modules for the implementation details — it's
the canonical lockfile pattern used across the codebase.

## Failure modes

- **`~/.config/lore/config.yml` lost** → no scope routes any repo; the
  SessionStart banner names `lore setup scope`. Re-run it per folder.
- **`<repo>/.lore.yml` malformed** → its keys are ignored; the scope and
  defaults still apply.
- **`_scopes.yml` missing/broken** → only the tag catalog degrades;
  routing is unaffected.
