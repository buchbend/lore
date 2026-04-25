# Scheduled cascade-threshold review — 2026-05-11

Saved on 2026-04-25 because Claude's session-cron doesn't persist
across session clears. Manually invoke this in ~2 weeks (or set a
system cron / calendar reminder pointing here).

## How to run

```
claude < .lore/scheduled_review_2026-05-11.md
```

or paste the prompt below into a new Claude Code session.

## Prompt

You are running on a 2-week follow-up to Lore v0.6.0 (Python
knowledge CLI in /home/buchbend/git/lore). On 2026-04-25 we promoted
the feature-based noteworthy cascade from opt-in to default after a
15-slice spot-check showed zero false-positives / false-negatives.
Your job is to verify this empirically using ~2 weeks of accumulated
real-traffic shadow-run data.

Read `/home/buchbend/git/vault/.lore/transcript-ledger.json` and glob
`/home/buchbend/git/vault/.lore/runs/*.jsonl` since 2026-04-25. Each
`cascade-verdict` event has label (trivial/uncertain/substantive),
reason, and features. Each `noteworthy` event has the LLM verdict.

Verify: cascade=substantive vs llm=True should be ~100%. cascade=trivial
is NOT verified by LLM — spot-check sample transcripts for hidden
substantive work. cascade=uncertain should split T/F roughly evenly.
If any disagreement >5%, propose threshold adjustments in
`lib/lore_core/noteworthy_features.py` (constants `_TRIVIAL_TURN_MAX`,
`_SUBSTANTIVE_EDIT_MIN`, `_SUBSTANTIVE_FILES_EDITED_MIN`,
`_TINY_ASSISTANT_TEXT_MAX`, `_SHELL_HEAVY_MIN`) and ship as v0.6.x.
If clean, report briefly and exit.

Don't add LLM calls. Don't touch Phase B/C/D code. TDD any change.
Code-reviewer agent before merging threshold changes.
