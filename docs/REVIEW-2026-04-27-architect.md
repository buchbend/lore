# Lore — Architect Review Against Vision

**Date:** 2026-04-26
**Reviewed at:** v0.10.2 (pyproject + plugin manifest in lockstep)
**Lens:** does the architecture support the stated vision (cross-host, cross-team, multi-wiki, cross-session pollination, pluggability, drill-down, transcripts)? Code quality is out of scope — see the 2026-04-25 three-lens report for that.

---

## 1. Vision alignment scorecard

| Vision pillar | Status | Evidence | Gap |
|---|---|---|---|
| **Multi-wiki / scope model** | **Strong** | Vault/wiki/scope split is explicitly defined and enforced by three orthogonal state files with clear roles; longest-prefix routing is host-local and O(log n) (`docs/architecture/state.md`, `lib/lore_core/state/attachments.py`, `lib/lore_core/scope_resolver.py`). | Surface vocabulary is **per-wiki** (`<wiki>/SURFACES.md`) — good. But MCP search is single-wiki by default (`lore_mcp/server.py:67` reindexes one wiki per call); cross-wiki "polymath" search is mode-gated to `recent` (`server.py:229`), not the standard search path. A user juggling four wikis will not get cross-wiki retrieval unless they explicitly opt in. |
| **Cross-host (vault sync)** | **Aspirational** | The design is git-as-sync: each wiki is its own git repo (`README.md:48-49`), `WikiConfig.git.{auto_commit, auto_push, auto_pull}` are declared in `lib/lore_core/wiki_config.py:14-17`, and `auto_commit` is implemented in `session_curator.py:558-585`. | **`auto_pull` is dead code** — `grep -rn "auto_pull"` in `lib/` returns only the dataclass field; no caller ever reads it. **`auto_push` is similarly unwired.** No SessionStart pull. No conflict-resolution strategy. No "fetch before edit" gate. Two hosts editing the same wiki today will produce merge conflicts the curator cannot resolve. |
| **Cross-team session-note streaming** | **Partial** | Team mode exists and is principled: `_users.yml` opt-in, sessions sharded into `sessions/<handle>/`, git-author-detection auto-recommends activation (`lib/lore_core/identity.py`). Curator B + threads.md regeneration walks all session notes regardless of author, so cross-team synergies *can* surface during daily abstraction (`daily_curator.py:467-490`). | The mechanism for "synergies surface automatically" is **next-day Curator B**, which is local to each host. If Alice's host runs Curator B, Bob's host doesn't see Alice's surfaces until Bob's host either pulls or runs its own Curator B over the merged graph. That works only if the wiki is reliably synced (see row 1) and Curator B is idempotent under pulls (untested under contention). |
| **Cross-session pollination without reload** | **Partial** | Real mechanism: SessionStart hook injects a status line + focus block + open items + last-note hints (`hooks.py:731-836`); MCP exposes `lore_search`/`lore_resume`/`lore_wikilinks` so the agent can pull more on demand. Surfaces extracted by Curator B become available at the next SessionStart. | "Without reload" is **not** real-time. New concepts a teammate introduces become available only after (a) their host pulls, (b) their Curator B runs, (c) the wiki repo is pushed, (d) my host pulls, (e) I start a new session. Five hops, all manual or daily-cadence. The vision's "available to other agents in later sessions without manual reload" is true in *spirit* (no human-in-the-loop), but cadence is **24h+ in the worst case**. Mid-session pollination requires a manual `/lore:resume`. |
| **Pluggability — adapters** | **Strong** | `Adapter` is a real `Protocol` with a small surface (`list_transcripts`, `read_slice_after_hash`, `is_complete`); registry is dict-based with four built-ins (Claude Code, Cursor, manual-send, VSCode Copilot) registered at import (`lib/lore_adapters/registry.py`). Adding a new host is a single class + one `register()` call. | No entry-point discovery yet (registry comment notes "deferred"); third parties must monkey-patch the registry or fork. Trivial to fix. |
| **Pluggability — sinks** | **Aspirational** | `BriefingSink` Protocol exists in `lib/lore_sinks/__init__.py`. Two sinks ship (markdown, matrix). | The sink dispatcher is **hardcoded `if sink.startswith("markdown:")`** in `daily_curator.py:442`; matrix is not actually wired into the auto-publish path; `_KNOWN_SINKS` in `briefing_cmd.py:64` is a static set. No registry, no protocol-driven dispatch. Adding a Slack/Discord/Linear sink today means editing `daily_curator.py`. The Protocol exists but isn't *used*. |
| **Pluggability — backends (issues/PRs)** | **Strong** | GitHub is the only implementation but the seam is correct: `<repo>/.lore.yml` declares `backend`, `issues`, `prs`, `wiki_source` (CHANGELOG 0.3.0); resolution flows through `attach accept` into `attachments.json`. Skills + MCP read these as opaque links (`lore_search`, `lore_read`) and never own issue state. | A Linear adapter today would require: implementing a single new backend identifier in `.lore.yml`, plus a `lore_backends/` package mirroring `lore_adapters/`. The package doesn't exist yet — **the abstraction is by convention, not by code.** "Plug-in friendly" for backends is a *design intent* with no enforcing seam. |
| **Drill-down semantics** | **Strong** | The chain is real and well-architected: surface → session note (via `synthesis_sources` frontmatter) → transcript (via `from_hash..to_hash` + `TranscriptHandle`). MCP exposes the chain (`lore_read`, `lore_wikilinks`, transcripts CLI). Status-line + 2000-char SessionStart cap + `lore status` / `lore doctor` give the user copy-paste-able next steps. | `lore_search` returns paths but not the wikilink-graph context by default; user has to chain `lore_search` → `lore_read` → `lore_wikilinks` themselves. A single `lore drill <query>` end-to-end demo would close the gap and is cheap. |
| **Transcript collection (cross-host)** | **Partial** | `lib/lore_core/transcript_sync.py` is **excellent infrastructure**: atomic writes, JSON tail-healing, `.gitignore` race-resistant (negation-rule abort), spawn-locked (`role="transcripts"`). It mirrors `~/.claude/projects/...*.jsonl` into `<wiki>/.transcripts/` per host. | **The mirror is host-local only.** `.transcripts/` is gitignored on purpose (`transcript_sync.py:42-50`) — raw transcripts never leave the host. So the cross-host story is: "summaries travel via git; transcripts stay local." That's a defensible privacy decision but it means **drill-down past the curator-redacted summary is not cross-host** unless the user manually copies `.transcripts/` over SSH. The README hints at "future restore-full-context workflow" (transcript_sync.py:8) but no spec exists. |
| **Layering fence (lore_runtime, Phase 1)** | **Strong** | Real one-way graph: `plugin/skills → lore_cli → lore_runtime → lore_core / lore_curator / lore_mcp / lore_search / lore_sinks / lore_adapters`. `tests/test_layering.py` is a regex-based static guard run every CI; covers TYPE_CHECKING and lazy imports. The shared helpers it hosts (`argv_main`, `run_render`) are both small and load-bearing — no dumping ground. | Worth it. Keep the fence. The remaining cross-layer pollution lives between `lore_curator` and `lore_core` (curator imports many private `_`-prefixed core functions, not a layering bug per se but a sign that core's public surface isn't intentional). Lower priority. |

---

## 2. Top 3 architectural risks

### Risk A — Cross-host sync is a config-shaped placeholder, not a feature

`auto_pull` and `auto_push` are dataclass fields with no callers. The whole vision rests on "git is the sync layer," and the install ships with git auto-commit on session-curator success but no pull-before-edit and no push after. The two-host failure mode today: Host A files `[[2026-04-26-foo]]` and commits; Host B starts a session, scaffolds the same slug (because the ledger said no recent session), commits with the same path — divergent histories on next pull.

**Mitigation sketch**:
1. Wire `git.auto_pull` into the SessionStart hook chain — fetch + fast-forward on a wiki repo with a clean working tree, no-op otherwise. Cost: ~30 lines + integration test against a bare git repo fixture.
2. Wire `git.auto_push` into Curator A's post-commit (where `_maybe_auto_commit` already lives, `session_curator.py:574`). Cost: 5 lines + `--no-verify`-aware test.
3. Define an explicit conflict policy: "session notes are append-only and date-slugged; surfaces are last-writer-wins and curator-resolvable on next defrag pass." Document in `docs/architecture/sync.md`. Cost: a new ADR-shaped doc.
4. Add a "dirty wiki on SessionStart" warning in the status line so users notice when they're stomping a partial sync. Cost: 1 status-line bit.

The 0.10.x branch is the right place — no schema changes, additive only.

### Risk B — Cross-session pollination has no real-time path

The latency floor for "Alice introduces a new concept; Bob's agent uses it" is one full Curator B cadence + git round-trip + Bob's next SessionStart — 24-72h in practice. The architecture treats SessionStart as the only context-injection point and `lore_search` MCP as the only mid-session retrieval primitive. Both are correct decisions individually, but together they exclude the "eventually-consistent within a working day" path the vision implies.

The real gap is that **MCP search reindex is throttled per-wiki (`server.py:_REINDEX_THROTTLE_S = 5.0`) but not invalidated on pull**. If Bob's host pulls Alice's surfaces while Bob's MCP server is alive, Bob's agent can't see them until either the throttle expires *and* the agent issues a new search, or the MCP server is restarted.

**Mitigation sketch**:
- A file-watcher (or a checked-in `.lore-touch` file with mtime bumps) that invalidates the reindex throttle when the wiki repo changes underneath. Pure local fs-watch, no daemon. Cost: ~80 lines, plus `watchdog` as an optional dep.
- Add a `lore pull` command that does fetch + ff + reindex-touch in one step; documents the cross-host workflow until auto-pull lands.

### Risk C — Sinks are advertised as pluggable but aren't

`BriefingSink` Protocol exists; nothing dispatches against it. The auto-publish path in `daily_curator.py:440-451` is a hardcoded `if sink.startswith("markdown:"):` chain; the matrix sink is shipped but unreachable from the curator. The CLI `briefing publish --sink` (briefing_cmd.py:63) has its own hardcoded `_KNOWN_SINKS` set. Two parallel half-implementations.

This is a small, contained architectural lie that will get expensive when a user reasonably says "wire my Slack briefing" and discovers there's no extension point.

**Mitigation sketch**: 50-line PR.
1. Move the sink dispatch into `lore_sinks/__init__.py` as `dispatch(sink_uri: str, text: str) -> None`, looking up by `scheme:` prefix in a module-level registry mirroring the adapter registry's shape.
2. Register `markdown` and `matrix` at import time.
3. Both `daily_curator._maybe_publish_briefing` and `briefing_cmd.publish` go through `dispatch()`. `_KNOWN_SINKS` becomes `registered_sinks()`.
4. Then a Slack sink is one new module + one `register()` call.

---

## 3. Top 3 high-leverage moves

### Move 1 — Land a real cross-host sync layer (Risks A + B together)

**What**: Wire `auto_pull` + `auto_push`, add fs-watch reindex invalidation, ship a `docs/architecture/sync.md` ADR.
**Why it pays back**: This is the single biggest gap between the README pitch ("cross-host, cross-team session-note streaming") and what the code does. Until this lands, every team-mode demo lies about the vision. After it lands, the rest of the work (briefings, drill-down, multi-wiki) all become real-time enough to feel magical.
**Effort**: One phase. ~300 lines of code, ~150 lines of integration tests against a bare-repo fixture and a watchdog test, one ADR. No schema migration, no breaking changes. Could land as 0.11.0.

### Move 2 — Sink registry + entry-point discovery for adapters (closes pluggability)

**What**: Lift the existing adapter registry pattern up one level — make sinks discover via the same `register()` mechanism, and switch both adapters and sinks to setuptools entry-points so third-party packages can plug in via `pip install lore-sink-slack`.
**Why it pays back**: Pluggability is a stated vision pillar and a strategic moat. Today, "add a Linear adapter" is "fork lore." After this move it's "publish a package." That's the difference between a product and a tool. Plus the GitHub-vs-Linear question becomes real: backends become a third entry-point category, with `.lore.yml:backend` resolving against the registry. Closes the "is GitHub baked in" anxiety in one stroke.
**Effort**: One phase. Sink registry is ~100 lines (mostly tests). Entry-point discovery is ~50 lines + docs. Backend abstraction is a separate, bigger phase but the registry pattern is the precondition.

### Move 3 — Promote `_compat`-shaped CLI mounting to a documented "subpackage CLI contract"

**What**: Today `lore_curator`, `lore_mcp`, `lore_search` each ship a `typer.Typer` that `lore_cli` mounts via `lore_runtime.argv_main`. The pattern works but isn't documented as the seam — new contributors don't know "the way to add a CLI verb is to ship a typer subapp from your subpackage and register a mount point in `lore_cli/__main__.py`." The three-lens review flagged that `lore_cli` is becoming the runtime hub *because* the seam is implicit.
**Why it pays back**: Once the seam is named ("Lore subpackage CLI contract: ship a `typer.Typer` named `app` plus an entry-point for documentation"), the next new feature (briefings v2, sync, journal) lands as a clean subpackage by reflex. Without it, every new feature adds 200 lines to `lore_cli/hooks.py` and the layering fence quietly bows. This is preventive medicine.
**Effort**: Half a phase. Mostly a `docs/architecture/cli-contract.md` page and a refactor of two existing modules to follow the documented pattern, plus a layering-style test that asserts `lore_cli/__main__.py` only mounts subapps and doesn't define commands inline.

---

## 4. What is already great

- **The state-file split (`attachments.json` host-local, `scopes.json` vault-wide-regenerable, `<wiki>/_scopes.yml` wiki-owned-portable)** is a *textbook* application of "what travels with what." The design doc (`docs/architecture/state.md`) is the artifact every project should aspire to: precedence rules, regenerability annotations, failure-mode catalog. Anyone reading it for 10 minutes understands the system. This is the architectural backbone the rest of Lore can lean on.

- **The layering fence (`lore_runtime`) plus its grep-style static guard (`tests/test_layering.py`)** is the right shape: minimal new package, narrow contract, tested. Phase 1 actually broke the diamond, didn't just declare it broken. The fact that `lore_core`/`lore_curator`/`lore_mcp` can now be unit-tested without typer is invisible to users and load-bearing for everything that comes next (library mode, HTTP entrypoint, integration testing without process spawn).

- **`TranscriptHandle` + content-hash watermarks in the ledger** is the unsung hero. Mid-stream transcript mutation (Cursor rewriting earlier turns), partial reads, two hosts pointing at one transcript — none of these break the curator pipeline because the watermark is content-addressable, not offset-addressable. This decision is what makes drill-down even *theoretically* possible across hosts and adapters; without it, "summaries are lossy by design, the architecture is not" would be marketing.

---

## TL;DR — biggest single risk to the vision

**Cross-host sync is a config-shaped placeholder, not a feature.** The README sells "cross-host, cross-team," the dataclass declares `auto_pull: bool = True`, and no code reads it. Until SessionStart pulls and Curator A pushes, every multi-host or multi-user scenario silently relies on the user remembering to `git pull` and `git push` by hand — and the moment they forget, two hosts diverge in a way the curator can't repair. Fix this in 0.11.0 and the rest of the vision (cross-team pollination, drill-down, multi-wiki polymath) becomes plausible to demo. Leave it unfixed and Lore stays a beautifully-architected single-host tool that *describes* a multi-host product.
