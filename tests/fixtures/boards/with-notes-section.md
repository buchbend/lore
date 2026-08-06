Epic #229 — supervision board. Edited in place across the run.

<!-- lore-orchestrate-epic:status v1 -->

| Feature | Issue | Tier | Batch | State | PR |
|---------|-------|------|-------|-------|-----|
| Load the config | ccatobs/widget#12 | AFK | 1 | merged | ccatobs/widget#40 |
| Parse the manifest | ccatobs/widget#13 | AFK | 1 | running | — |
| Export the report | ccatobs/widget#14 | HITL | 2 | queued | — |

## Notes

### Tier rationale

- Issue ccatobs/widget#14 — HITL. The report format reaches a customer; a
  human confirms the layout before merge.

### Block cause

- Issue ccatobs/widget#13 — blocked on a flaky fixture in CI. A rerun is
  scheduled.

### Crosscheck verdict

- Issue ccatobs/widget#12 — PASS. The schema matches the config loader's
  public contract.
