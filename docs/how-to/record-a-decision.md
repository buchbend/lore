# Record a decision

**Goal:** write an ADR that says how strong each part of the decision is. Check
it with `lore lint adr`. Change it later without a new record.

## Before you start

- A decision that meets all three criteria: hard to reverse, surprising without
  context, the result of a real trade-off. Anything else does not need an ADR.
- `lore` installed. The check runs on a checkout of the repo.

## Steps

1. **Copy the template.** The template lives in
   `lore-workflow/skills/grilling/ADR-FORMAT.md`. Number the file one above the
   highest in `docs/adr/`: `docs/adr/NNNN-slug.md`. Add the stem `NNNN-slug` to
   the first `{toctree}` block in `docs/adr/index.md`.

2. **Fill in `Holds`.** Sort each part of the decision into one of three kinds:

   ```md
   ## Holds

   - **Invariant** (test: `tests/test_x.py::test_y`): <what must stay true>.
   - **Default:** <what to do unless the task gives a reason>.
   - **Incidental:** <how it was built at the time; change freely>.
   ```

   An invariant line names the test that enforces it. Without a test, write the
   line as a default. A section with only defaults and incidentals is valid.

3. **Fill in `Revisit if`.** One line per condition that reopens the decision.

4. **Write the other sections.** Context, Decision, Consequences, Alternatives
   considered and Status explain the decision. An agent reads them as
   background, and only invariants bind it. Keep mechanism detail out of
   Decision and put it in `Holds` as an incidental line.

5. **Run the check.**

   ```
   lore lint adr
   ```

   The command reads `docs/adr/` under the current directory, or under the
   `root` argument. It exits 1 and names the file and line of each problem: a
   missing `Holds` section, or an invariant whose test file or test name does not
   exist. `--json` prints the report as JSON. ADRs 0001 to 0013 are skipped. CI
   runs the same check.

6. **Check the wording.** The writing rules flag `always`, `never`,
   `no exceptions`, `fixed` and `must` in docs prose. Keep them out of the text
   outside an invariant line. Run `lore style show writing-rules` for the rules.

## Change a decision later

A rule that turned out too strict or too loose becomes a dated line under
`## Amendments`:

```md
## Amendments

- 2026-09-29: <what changed and why>.
```

Edit the `Holds` line the amendment changes in the same commit. Write a
superseding ADR only when the whole decision reverses; then set the old ADR's
status to `Superseded by ADR-NNNN`.

## Related

- [Conventions](../conventions.md) for where each kind of fact lives.
