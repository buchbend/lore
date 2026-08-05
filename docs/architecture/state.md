# Lore State Map

**Audience:** contributors who see three files named `_scopes.yml`,
`scopes.json`, and `attachments.json` and wonder which one wins.

## Vocabulary

Three terms get conflated in casual conversation; they mean different
things. User-facing copy and contributor docs should keep them
distinct:

- **Vault** — the top-level directory pointed to by `$LORE_ROOT`.
  Contains exactly one `wiki/` subdirectory, plus `.lore/` for
  derived state.
- **Wiki** — a mounted knowledge store at `vault/wiki/<name>/`. Each
  wiki is an independent git repo. A vault may host one or many.
- **Scope** — a colon-separated namespace inside a wiki
  (`ccat:data-center:data-transfer`). Hierarchical; routing is by
  longest-prefix.

When in doubt: vault is *the place*, wiki is *a store inside the
place*, scope is *a slot inside a store*.

The short answer: **none of them wins, because they don't conflict.**
Each file is a different layer of the same domain model. This document
explains what each one owns, who writes it, and how they collaborate
to answer the question "given this cwd, where do I write notes?"

---

## TL;DR — three files, three roles

| File | Owner | Regenerable? | Question it answers |
|------|-------|--------------|---------------------|
| `<wiki>/_scopes.yml` | wiki maintainer | yes (manually) | "What scopes does *this wiki* define?" |
| `$LORE_ROOT/.lore/scopes.json` | `lore attach accept` | yes (from accepted offers) | "What scopes are active in *this vault*?" |
| `$LORE_ROOT/.lore/attachments.json` | `lore attach accept` | **no** | "On *this host*, where does cwd X route to?" |

The three together answer: starting from a directory on disk → which
host attached it (`attachments.json`) → which scope chain it lives in
(`scopes.json`) → which catalog of scopes the chosen wiki defines
(`_scopes.yml`).

---

## 1. `<wiki>/_scopes.yml` — wiki's scope catalog

**Path:** `<lore_root>/wiki/<wiki>/_scopes.yml`
**Loaded by:** `lore_core/scopes.py:load_scopes_yml`
**Mutated by:** wiki maintainer (manual edit + commit)

A declarative tree of scope IDs the wiki recognizes, with optional
`repo:` annotations at leaves. Example:

```yaml
scopes:
  ccat:
    label: CCAT-prime
    children:
      data-center:
        label: Data Center
        children:
          data-transfer:
            repo: ccatobs/data-transfer
          computers:
            repo: ccatobs/system-integration
```

Walked by `walk_scope_leaves()` to produce `(scope_path, repo_slug)`
pairs for use in offer-suggestions and breadcrumb routing. Pure I/O,
no side effects.

This file is the **wiki's own statement** about its taxonomy. It is
checked into the wiki's git repo and travels with it.

---

## 2. `$LORE_ROOT/.lore/scopes.json` — vault scope registry

**Path:** `<lore_root>/.lore/scopes.json`
**Loaded by:** `lore_core/state/scopes.py:ScopesFile`
**Mutated by:** `lore attach accept` (when the user accepts a
`.lore.yml` offer)

A flat dict `{scope_id: ScopeEntry}` representing the **union of
accepted offers' scope chains** across all wikis on this vault.
`ScopeEntry` carries `label`, `wiki` (assigned at root, inherited),
and `description`.

Why JSON (not YAML)? Because it's regenerable, machine-written, and
needs fast round-trip — not human-edited.

**Regenerable** from the accepted offers: deleting `scopes.json` and
re-running `lore attach accept` for each attachment rebuilds it. This
is the Phase 5 rebuild pass mentioned in the docstring; in practice
you don't need it day-to-day.

This file answers: "in this vault, what's the active scope tree, and
which scope belongs to which wiki?"

---

## 3. `$LORE_ROOT/.lore/attachments.json` — host consent record

**Path:** `<lore_root>/.lore/attachments.json`
**Loaded by:** `lore_core/state/attachments.py:AttachmentsFile`
**Mutated by:** `lore attach accept`, `lore attach manual`,
`lore attach decline`

A list of records mapping an absolute filesystem path on **this host**
to a `(wiki, scope)` pair. The record of "did the user actually
consent to attach this directory?"

```json
{
  "attachments": [
    {
      "path": "/home/user/git/data-transfer",
      "wiki": "ccat",
      "scope": "ccat:data-center:data-transfer",
      "attached_at": "2026-04-22T10:33:00Z",
      "source": "offer",
      "offer_fingerprint": "..."
    }
  ],
  "declined": [...]
}
```

**Not portable** between hosts (paths are absolute) and **not
regenerable** from any other artifact. This is the audit trail of user
decisions — losing it means asking the user to re-confirm every
attachment.

**Resolution is by longest-prefix match** on the `path` field — no
filesystem walk-up. `AttachmentsFile.longest_prefix_match(cwd)` runs
in O(n log n) over registered attachments.

This file answers: "on this host, when I'm in directory X, which
wiki+scope am I working in?"

### Shared-vault consent & leak semantics

A wiki with no git remote is **private by default** — nothing it holds
ever leaves the machine. A wiki with a git remote is a **shared
vault**: every teammate with read access to that remote can see every
composed session note committed there.

Attaching a scope to a shared vault is the moment sharing starts, so
it's the moment consent is asked. `lore attach accept` / `lore attach
manual` route through `_confirm_shared_vault()`
(`lore_cli/attach_cmd.py`), which checks `git_sync.has_remote()` on the
target wiki before writing the attachment row:

- **No remote** — no-op, nothing to consent to.
- **Remote, interactive terminal** — a y/N prompt, printed before the
  row is written.
- **Remote, non-interactive** — refused (exit 1) unless the caller
  passes `--confirm-shared`; non-interactive callers never get routed
  to a shared vault silently.

**The gate reduces but cannot eliminate leaks.** It stops accidental
first-time routing to a shared vault; it does not scan note content.
If a secret ends up in a composed note, git history retains it even
after the note is edited or deleted — the remedy is **rotating the
credential**, not scrubbing history.

**Transcripts and buffers never leave local state.** They live under
`<lore_root>/.lore/` (`transcript-ledger.json`, `buffers/`), a sibling
of `wiki/` — never a descendant — so a `git push` of a wiki directory
structurally cannot ship them. Each ledger entry carries a `linkage`
block — `repo`, `branch`, `prs`, `issues`, `commits`, `files` — written
by capture with no LLM call. It is what `lore_drill` reads to answer
"which sessions touched X" and what the SessionStart recap renders from.
The block is derived and rebuildable, and stays machine-local with the
rest of the ledger. Only composed, gate-passed notes
written into `wiki/<name>/sessions/` are ever pushed.

---

## How they collaborate

When a Claude Code session starts in `~/git/data-transfer`:

```
session_start.py:session_start_text
  ↓
scope_resolver.py:resolve_scope(cwd)
  ↓
attachments.json  →  (wiki=ccat, scope=ccat:data-center:data-transfer)
                     [host-local cwd → wiki+scope routing]
  ↓ if scope is unfamiliar:
scopes.json       →  ScopeEntry(label="Data Transfer", wiki="ccat")
                     [resolve scope metadata + wiki assignment]
  ↓ if a new offer is being suggested:
_scopes.yml       →  walk leaves to find a repo match
                     [check the wiki's catalog for a known slot]
```

In normal operation only `attachments.json` is consulted — the cheap
common path. The other two come into play during onboarding (`lore
attach`) and offer evaluation.

---

## Diagram

```
                        +----------------------+
                        |  <wiki>/_scopes.yml  |   wiki's declared
                        |  (per-wiki catalog)  |   taxonomy (manual)
                        +-----------+----------+
                                    |
                                    v  consulted during
                                       offer evaluation
+--------------------+        +----------+----------+
| Claude Code starts |        |  scopes.json        |   active scope tree
| in a cwd           |        |  (vault registry)   |   (regenerable)
+---------+----------+        +----------+----------+
          |                              ^
          v                              |
+----------+---------+      union of    |
|  attachments.json  +------------------+
|  (host-local)      |   accepted offers
+--------------------+
          |
          v
   wiki + scope for this cwd
```

---

## Concurrency-safety guarantees

Two state files outside the scope/attachments triple are written from
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
used by ``lore_core.lockfile.try_acquire_spawn_lock`` to serialise
detached curator launches. See those modules for the implementation
details — it's the canonical lockfile pattern used across the
codebase.

## Failure modes

- **`attachments.json` deleted** → cwd → wiki resolution fails;
  SessionStart status line shows "no wiki resolved." User must
  re-`lore attach` per directory. (Worst case — ask the user.)
- **`scopes.json` deleted** → Lore continues, but new offers can't
  resolve `wiki` from a scope chain that wasn't seen before. Run
  `lore attach accept` over each attachment to rebuild.
- **`_scopes.yml` missing/broken** → offer-suggestion is degraded
  (no repo-aware routing) but Lore still works for accepted
  attachments. Fix in the wiki repo.

The asymmetry is intentional: **accepted-attachment information must
survive everything else.** That's why it lives in its own file with
explicit consent records, not derived state.
