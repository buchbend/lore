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

## Checks before you publish

- The count chips add up to the number of open issues.
- Each open issue appears once on the page.
- Each link points at `https://github.com/<owner>/<repo>/…`.
- The page reads at phone width.
