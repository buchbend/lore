# Review a PR

Read this when a PR opens in `issue` or `epic` mode, and for the epic PR.

## 1. Deterministic checks first

- `gh pr checks <n>` answers CI and ruff. A red check blocks the merge. The reviewer does not
  judge it.
- A check that fails on the base branch too is inherited. Name it in the board's Notes as a
  blocker and do not count it against this PR.

## 2. Risk level sets the review depth

Run `lore workflow risk <n> --json`. It prints `{level, reasons, notes, files, lines}`.

| Level | Reviewer tier | Correctness pass |
|---|---|---|
| `low` | `mid` | `code-review <n> low` |
| `high` | `strong` | `code-review <n> medium` |

You may raise the level: a subtle concurrency change, a data migration the tool did not see,
a feature the PRD calls risky. Never lower it. Record a raise and its reason on the board.

## 3. Spawn the reviewer

Resolve the tier with `lore tier resolve <tier>` and pass the result as the model. The reviewer
reads the diff and the linked sub-issue. In `epic` mode, keep one reviewer per batch and level.
Send each later PR of that batch to it with `SendMessage`, so it sees siblings side by side.

The reviewer judges three things only: correctness, acceptance criteria, scope. It runs
`code-review` on the PR first, never with `--comment` or `--fix`. A confirmed correctness
finding fails the line. Cleanup findings go in the note only.

The reviewer posts this block as a PR comment (`gh pr comment <n> --body-file …`) and returns it:

```
PR #<n>
risk: low | high — <reasons>
reviewer tier: <tier>
verdict: PASS | FAIL
- correctness (`code-review <n> <low|medium>`): pass | fail — <confirmed findings>
- tests map to acceptance criteria, failing test first: pass | fail — <note>
- scope (only the files this feature needs): pass | fail — <note>
fixes (only on FAIL): 1. <precise fix>  2. <precise fix>
```

Any `fail` line makes the verdict FAIL.

## 4. Act on the verdict

- **PASS** and green checks: merge.
- **FAIL:** send the numbered fixes to the teammate (in `issue` mode, fix them yourself). The
  same reviewer checks that PR again. At most two fix rounds.
- A round that does not move the verdict: send the [`debug`](../debug/SKILL.md) method, not
  "try again".
- Still FAIL after two rounds: mark the feature `blocked`, note it on the board, escalate.
