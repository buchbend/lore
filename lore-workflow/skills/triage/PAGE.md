# The triage page

One self-contained HTML page. Embed `triage.json` and the repo state as one `<script
type="application/json">` block and render from it, so a rerun only swaps the data. No build
step, no external data calls.

## Sections, top to bottom

1. **Header: repo state.**
   - The branch state: default and release branch, level or `n ahead / m behind`.
   - Commits in the last month, open issues, open PRs, the oldest issue with its age.
   - Removed or replaced components, one line each.
   - One count chip per verdict. A click filters the lists below to that verdict; a second click
     clears the filter.
   - One "where we stand" panel per theme: two or three sentences from the lens that took it.
2. **Critical: act this week.** Each `Critical` entry with its link, why, evidence and next
   step. Empty: say "Nothing critical found."
3. **All issues by verdict.** One group per verdict, in the order of the verdict list in
   `SKILL.md`. Each entry shows:
   - `#n` linked to the issue, the title, the area tag;
   - the tags `verified` and `live check` when set;
   - why, evidence (each `path:line` linked to the file at the HEAD commit), dependencies, next
     step.

   Controls above the groups: a search box over number, title, why and evidence, and an area
   filter. Search, area filter and verdict chips combine.
4. **Risks no issue tracks.** Candidate new issues: the risk, evidence, the lens that saw it, a
   proposed verdict.
5. **Open PRs.** Number, title, branch, age, mergeable state, linked issues, and the
   recommendation `merge`, `rebuild`, `close` or `decide` with one line of reason.
6. **Questions for you.** Numbered decisions the user has to make, each naming the issues it
   unblocks.

## Decide on the page

The page lets the user step through many entries fast and hand the decisions back in one paste.

- **Decision per entry.** Each issue, PR and untracked risk gets a row of buttons:
  - issue: `agree` (take the proposed verdict), one button per other verdict, `discuss`;
  - PR: `merge`, `rebuild`, `close`, `decide`, `discuss`;
  - risk: `file`, `skip`, `discuss`.
- **Comment per entry.** A small text field under the buttons. A comment alone counts as
  `discuss`.
- **Answers to the questions.** A text field under each question in "Questions for you".
- **Stepping.** `j` / `k` move to the next or previous entry, `a` agrees, `1`–`6` pick a verdict
  in list order, `d` marks `discuss`, `c` focuses the comment. Keys do nothing while a text field
  has focus. An "undecided only" toggle hides decided entries. A counter shows `decided / total`.
- **Memory.** Keep the decisions in `localStorage`, keyed by repo and generation time, inside
  `try`/`catch`. The page works without it.

## The answer prompt

A fixed panel at the bottom holds a read-only text field that updates on every decision, and a
**Copy** button. Copy with `navigator.clipboard.writeText`. When that throws, select the text
and use `document.execCommand("copy")`, then say "Press Ctrl+C" if that fails too. The text
lists only decided entries, in page order:

```text
triage answer <owner>/<repo> @<short HEAD sha> — <decided> of <total> decided
#123 agree Close: done
#124 set Backlog — note: <comment>
#130 discuss — note: <comment>
PR #88 merge
PR #91 close — note: <comment>
risk 2 file — note: <comment>
Q1: <answer>
```

One line per entry. Replace line breaks inside a comment with spaces. The user pastes this text
into the session; `SKILL.md` § 9 reads it.

## Checks before you publish

- The count chips add up to the number of open issues.
- Each open issue appears once on the page.
- Each link points at `https://github.com/<owner>/<repo>/…`.
- The page reads at phone width.
- A decision, a comment and a question answer each show up in the answer prompt.
