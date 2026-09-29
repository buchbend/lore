# Use the fast path

**Goal:** implement one clear GitHub issue, or a series of small asks, with
the workflow's discipline and without the epic chain.

The fast path is `/lore-workflow:build` in `issue` mode or in `loop` mode. It
avoids two traps. The first is the whole chain for a change that does not
need it. The second is no workflow at all, which drops the tests, the
decisions and the docs.

## Pick the mode

| Mode | Use when |
|------|----------|
| `issue` | You hold one written, clear issue. |
| `loop` | You react to a running feature in fast rounds: screenshots, "make this red", a hotkey. |

When the change is clear only in your head, run `/lore-workflow:orient` and
say "brief me". Its light mode files the issue and hands it to `issue` mode.
When the work is several features, or its shape is unsettled, use the
[epic chain](run-an-epic.md).

## Before you start

- For `issue` mode: a single, clear GitHub issue. See
  [Write a good fast-path issue](write-a-fast-path-issue.md).
- The repo is onboarded ([Onboard a repo](onboard-a-repo.md)).

## Issue mode

1. **Run `/lore-workflow:build` in `issue` mode** with the issue.
2. **The skill reads the issue and the code map.** It spawns no explorers.
3. **The skill clarifies only when needed.** An ambiguous issue gets at most
   three questions. A clear issue gets none.
4. **The skill builds test-first** on one branch, `feat/<issue>-<slug>`, off
   the target branch. Everything goes into one PR.
5. **The skill updates the docs** on the same branch.
6. **One review runs** at the depth `lore workflow risk` sets: a `mid`-tier
   reviewer for a `low` diff, a `strong`-tier reviewer for a `high` diff.
7. **The skill asks about the ledger.** You approve, drop or file each ADR
   candidate, term candidate and left-over item. Approved ADRs and terms go
   into the same PR.
8. **The skill opens the PR.** Its body ends with the handover section.

## Loop mode

1. **Run `/lore-workflow:build` in `loop` mode.** The skill creates
   `loop/<slug>` off the branch your dev stack runs.
2. **Give an ask per round.** Each round is test-first and merges locally
   into the dev-stack branch. A background check runs the full suite.
3. **Say "wrap up"** to end the loop. You approve the ledger lines, the
   skill writes the ADRs, terms and docs, and it opens one wrap-up PR. That
   PR is the only thing the loop pushes.

## Done when

The PR is open, green, and linked to its issue, ready for you to merge. The
skill does not merge it, and it never merges on red.
