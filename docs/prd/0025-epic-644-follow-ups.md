---
title: "Follow-ups of epic 644"
status: draft
epic: https://github.com/buchbend/lore/issues/TODO
repos:
  - buchbend/lore
---

# PRD 0025: Follow-ups of epic 644

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/TODO).
> The epic links here; this file is not embedded in the issue body.

## Problem

The reviews of epic 644 (larger epics and code mode) left three gaps. None of them blocked the merge.

- The context pack with an intent drops the focus docs of a branch. A superseded ADR or a shipped PRD can rank first.
- The build-order validator accepts any text in the `Lenses` column. The `--json` output does not print the lenses.
- `lore workflow after-merge` repeats parser code and prints nothing when the close call fails. The lens review shares the stage tag of the whole-epic review. The ADR status check reads the whole ADR body.

## Solution

- Issue 648 (intent pack keeps focus docs, ranks current docs first): the context pack holds the focus docs first. Current docs rank above old docs.
- Issue 649 (check and print the Lenses column): the validator checks each lens cell. `validate-build-order --json` prints the lenses of each row.
- Issue 650 (tidy after-merge, the lens stage tag and the ADR status check): four small changes in one pull request.

## Implementation decisions

- The issues 648, 649 and 650 hold the required behaviour and the acceptance criteria. This PRD adds no behaviour.
- The cap of 8 ranked docs stays. The focus docs count toward the cap.
- A lens is one lowercase word. A `Lenses` cell holds lenses split by commas.
- The lens review gets its own stage tag in the stages module. The epic tail names that tag.
- The ADR status check reads the `status` line of the ADR header only.

## Testing decisions

- Each acceptance criterion maps to one test of external behaviour.
- Prior art: `tests/test_context_pack.py`, `tests/test_workflow_build_order_validator.py` and `tests/test_workflow_after_merge.py`.

## Out of scope

- A semantic or embedding rank of the context pack.
- A match for words of 1 or 2 letters in the intent.
- A check that each lens in the table appears in the review plan.
- A change to the `--json` flag of `lore brief`.
