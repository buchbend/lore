# ADR 0011: The flag review walk runs in a local, ephemeral browser page

- Status: Accepted
- Date: 2026-08-08
- Context: issue [409](https://github.com/buchbend/lore/issues/409),
  ADR [0004](0004-authority-phrasing-is-code-stamped.md),
  ADR [0008](0008-flag-lands-marked-unreviewed.md),
  ADR [0009](0009-privacy-boundary-is-locality.md)

## Context

A flag block carries a code-stamped ref verdict: `✓`, `(unchecked)` or
`(not found)`. The ref verdict decides how much the lead may claim
(ADR 0004). It is the reviewer's main input.

The terminal walk prints that verdict as text inside the origin line, in
the same colour as the rest of the block. On host saiyajin,
`lore flag list --wiki private` reported 15 pending flags carrying all
three ref verdicts. A reader cannot separate them by eye, so the reviewer
re-reads each origin line to recover a signal the code already computed.

ADR 0009 states that Lore ships no server. ADR 0009 argues about
cross-machine transcript access: no sync, no backup, no remote reader.

## Decision

- `lore flag review` starts an HTTP listener on address 127.0.0.1 and an
  ephemeral port, then opens the page in the user's browser.
- The page renders each flag's ref verdict as a colour, and the
  code-stamped lead prefix as a separate label.
- The page groups the pending flags by owning note.
- The page applies verdicts by calling `lore_core.flag.accept`,
  `lore_core.flag.decline` and `lore_core.flag.retarget`. No second
  verdict path exists, so the spine events and the note writes stay
  identical to the terminal walk.
- A token in the page URL gates every request. The listener refuses a
  request carrying a wrong token.
- The listener exits when the user presses Done, and when the last flag
  leaves the page.
- `lore flag review --tty` runs the terminal prompts. The command also
  runs the terminal prompts when no browser resolves on the host.

## Consequences / Trade-offs

Easier: the reviewer sorts 15 flags by ref verdict at a glance. The
reviewer never retypes a 12-character flag identifier. The retarget field
completes from the wiki's existing notes, so a typo no longer creates a
note by accident.

Harder: Lore now binds a port. The listener holds the process for the
length of the review, so the command no longer returns per verdict. The
page's behaviour lives in browser code, which the Python suite cannot
reach — the suite covers the renderer, the verdict calls and the listener,
and leaves the click handlers untested.

The locality boundary holds. The listener binds loopback only, it carries
a per-run token, and it exits with the review. Lore still ships no
network service, no sync and no remote reader, so ADR 0009 stands.

## Alternatives considered

- Colour the terminal output instead. Rejected: the walk shows one flag
  per prompt, so colour alone does not let the reviewer compare flags or
  see the shape of the queue.
- Write a static HTML file and keep the verdicts in the terminal.
  Rejected: the reviewer would read in one surface and act in another,
  and would retype a 12-character identifier for each verdict.
- Ship a persistent local service for every Lore surface. Rejected: a
  process outliving the task contradicts ADR 0009's argument, and Lore
  has one surface that needs a page.

## Status

Accepted.
