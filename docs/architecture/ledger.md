# Build ledger and risk level

**Audience:** contributors who change the `build` skill or the
`lore workflow` verbs it calls.

PRD [0015](../prd/0015-slim-workflow-around-homes-and-holds.md) and ADR
[0014](../adr/0014-one-home-per-fact.md) define the ledger. This page is the
reference for its format, its homes and the verbs that read it. The code
lives in `lib/lore_workflow/ledger.py` and `lib/lore_workflow/risk.py`.

## Ledger lines

A ledger is Markdown. Each entry is one list item. The parser ignores every
other line, so a ledger file can hold a heading or prose.

```markdown
- [adr] open — Store tokens per skill phase
- [term] approved — breakpoint
- [left] filed buchbend/lore#123 — Retry the flaky capture test
- [left] dropped — Rename the board marker
- [resume] 2026-09-29T10:00:00Z — round 2 merged; next: wire the hook
```

- The kind sits in brackets: `adr`, `term`, `left` or `resume`.
- An `adr`, `term` or `left` line carries an outcome: `open`, `approved`,
  `dropped` or `filed <owner/repo#n>`.
- A `resume` line carries an ISO 8601 UTC timestamp. Its text holds the
  state, then `; next:` and the next ask.
- An em dash with one space on each side separates the text. The text runs
  to the end of the line.
- A line that starts with `- [` and breaks the format fails the check. The
  check does not skip the line.

## Homes

| Build mode | Ledger home |
|---|---|
| `loop`, `issue` | `$(git rev-parse --git-dir)/lore-ledger.md`. A linked worktree has its own git dir, so each worktree has its own ledger. |
| `epic` | A `## Ledger` section in the board comment. The section follows the table and comes before `## Notes`. The next heading ends the section. |

## Verbs

| Command | Effect |
|---|---|
| `lore workflow ledger-add --kind K --text T [--outcome O] [--next ASK] [--path P]` | Appends one line. The default path is the git-dir ledger of the current directory. A `resume` line gets the current time. `--path -` prints the line for a board comment. |
| `lore workflow ledger-set <index\|text> --outcome O [--path P]` | Sets the outcome of one line in place. The index is the number that `ledger-check` prints. The text selects exactly one line. |
| `lore workflow ledger-check [PATH\|-]` | Exits 1 and names each `open` line. Exits 0 when every line has an outcome. A missing ledger file exits 0. `-` reads a ledger or a whole board comment from standard input. |
| `lore workflow parse-board` | Adds a `ledger` key to its JSON output: one `{kind, outcome, timestamp, text}` object per line. |

## Breakpoint hooks

The hooks read the git-dir ledger of the session's working directory. They
resolve the git dir from the `.git` entry and start no child process.

- `lore hook pre-compact` adds one line that names the ledger path.
- `lore hook session-start` quotes the last resume line and offers to
  resume from it.
- Neither hook mentions a ledger when the worktree has none.

## Risk level

`lore workflow risk` reads a diff and prints `low` or `high`, then one line
per reason. `--json` prints `{level, reasons, notes, files, lines}`.

- Input: `--pr N` (runs `gh pr diff N`), `--range A..B` (runs `git diff`)
  or `--diff FILE|-`. When `gh` or `git` fails, the command exits 1 and
  names the failure.
- The level is `high` when the diff changes more than `max_lines` lines or
  more than `max_files` files.
- The level is `high` when a changed Python file sits in the top
  `fanin_top_fraction` of import fan-in. Fan-in counts the distinct Python
  files that import a module. The file list comes from the codemap
  discovery pass. The command notes the skip when the repository has no Python
  files.
- The level is `high` when the diff touches a migration, a schema file or a
  configuration schema, or adds a route decorator such as `@router.post(`.
- The level is `high` when a changed path matches a `sensitive_paths` glob.

The thresholds live under `workflow.risk` in the root configuration. See
[config.md](config.md). Example: `lore config get workflow.risk.max_lines`.
