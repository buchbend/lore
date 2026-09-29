# File facts as artifacts

**Goal:** record a team-relevant fact in the repo artifact that has a reader.
Then nobody digs the fact out of a chat window later.

Lore writes nothing into a wiki on its own. Agents and people file facts with
`gh` and the `lore-workflow:file-issue` skill. Lore ships no filing command.
The SessionStart directive tells the agent the filing rule below.

## Before you start

- `gh` is installed and signed in (`gh auth status`).
- You know the repo that owns the fact.

## Pick the artifact

| The fact is | File it as |
| :--- | :--- |
| A gap between the docs and the code, a trap, or a missing fact | An issue on the owning repo |
| A trivial fix close to the work in hand | A fix on the session branch, named in the PR body |
| About an existing issue or PR | A comment on that issue or PR |
| A dead end and its reason | An issue you open, closed as not planned, with the reason in the close comment |
| A decision | A pull request holding the ADR draft. A human merges it. |
| Across several repos | An issue on the org knowledge repo |
| For a wiki topic note | A pull request on the wiki repo with the edit |

An ADR or PRD negotiated with the user in a `grilling` or domain-modeling
session is the one exception. That skill writes the file directly, because
the user was in the loop.

## File it

1. Search first, so you do not open a duplicate. Call `lore_search` with the refs behind the fact. See [Search notes and issues](search-notes-and-issues.md).
2. Draft the text to the [writing rules](customize-the-writing-rules.md). Run `lore style show writing-rules`.
3. Post it through the `file-issue` skill, or with `gh` directly.
4. Name the refs behind the fact: a file path, a run, a commit, a PR.

Every issue an agent opens carries the `agent-filed` label. The `file-issue`
skill creates the label on demand. Every comment an agent posts opens with a
line that names it as agent-filed. An agent closes only issues it opened
itself.

## Check what you filed

At session end the agent lists every issue and PR it opened or commented on
in its final message. Triage happens in the tracker:

- Open means under triage.
- Closed as fixed means done.
- Closed as not planned means a dead end.

The next SessionStart does not replay the list. Find the artifacts with
`gh issue list --label agent-filed`.

## Report a retrieval miss (opt-in)

A retrieval miss is a fact a Lore tool did not return. The agent found the fact
reading files or running commands. Reporting is off by default.
To turn it on, set these keys in `$LORE_ROOT/.lore/config.yml`:

```yaml
feedback:
  retrieval_misses: true
  retrieval_misses_repo: buchbend/lore
```

While `retrieval_misses` is true, the `orient`, `implement-issue` and `tdd`
skills run one check at session end. The agent files one issue per miss on
`retrieval_misses_repo`. The issue names the fact, the tools tried and the
turn count. While the key is false, no skill files on that repo.

## Clean up flag blocks from an older install

Earlier versions wrote flag blocks into wiki notes. Lore no longer reads or
writes them. To remove them from every wiki note:

```bash
lore migrate flag-blocks           # dry run: lists what would change
lore migrate flag-blocks --apply   # removes the blocks
```

The command leaves the rest of each note untouched. Git history keeps the
removed blocks.
