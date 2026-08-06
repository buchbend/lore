# Why quotes are verified, not trusted

**Audience:** anyone reading a sweep report who finds fewer sessions under an
observation than the model named, and wants to know which list to believe.

> **Status.** The sweep is a prototype. `tools/dream_probe.py` runs from a
> shell, and no hook, no CLI and no curator calls it. Nothing below is shipped
> behaviour.

---

## Attribution is the evidence

A sweep reads past sessions and files an observation for every team-relevant
fact no artifact records. An observation carries a one-sentence claim, a short
body and its attribution.

- The model proposes an observation and names the sessions that produced it.
- The naming is the evidence for the observation.
- Recurrence across sessions is the signal a sweep exists to find.
- A model that names sessions incorrectly produces an observation nobody can check.
- A human meets the observation later in the review walk, with the transcripts gone.

## What the check does

`verify` (`tools/dream_probe.py:233`) runs five steps over the observations a
model returned.

### It builds a lookup of session prose

`verify` keys the prose of each session by the identifier `recent_sessions`
assigned. Session prose is the reduced text of a session: `load_session`
(`tools/dream_probe.py:65`) keeps user and assistant prose and drops tool
calls, tool results and reasoning. The lookup holds exactly the text the model
was shown.

### It normalises both sides

`_norm` (`tools/dream_probe.py:213`) folds a span to its comparable core:

- `_norm` applies NFKC unicode normalisation.
- `_norm` folds six dash characters to a hyphen.
- `_norm` folds curly quote marks to straight quote marks.
- `_norm` collapses each run of whitespace to one space.
- `_norm` casefolds the span.
- `_norm` strips wrapping quote marks and trailing ellipses.

A model re-emits a quote through its own tokeniser. Every difference in the
list above is packaging, not fabrication.

### It picks one search span per quote

- `verify` splits a quote on its ellipsis, because the model shortened a long quote.
- `verify` keeps the longest fragment as the search span.
- `verify` caps the search span at 160 characters.
- `verify` discards a span under 12 characters as too short to be evidence.

### It searches every session in the run

A run is the set of sessions read in one sweep. `verify` searches every session
in the run, not only the sessions the model named. The check recomputes
attribution rather than accepting or rejecting a claim. An observation naming
sessions A and B, whose quote sits in C and D, comes back attributed to C and D.

### It reports the union of the matches

`verify` records the union of the matching sessions as the confirmed sessions.
A count of recurrence uses confirmed sessions only.

## What the check assumes

- The model quotes text it was shown, so a quote must appear in the session prose the sweep sent.
- Whitespace, unicode and quote-mark differences carry no meaning.
- A 12-character span is long enough to be evidence.
- A 160-character span is long enough to be distinctive.
- Session prose holds the durable content, so a quote drawn from tool output cannot confirm.

## What the check proves, and what it does not

- Proves: the search span exists in the session prose the check attributes it to.
- Does not prove: the claim summarises its quote fairly. Model judgement stays unchecked.
- Bias: the check under-counts and never over-counts.

A second session that restates a fact in different words does not match the
first session's quote. Real recurrence is therefore at least the reported
number.

## What three runs measured

| Run | Observations | Confirmed | Confirmed in 2+ sessions | Named more sessions than confirmed |
|---|---|---|---|---|
| ccat wiki, 20 sessions, `claude -p` sonnet-4-6 | 18 | 14 | 3 | 7 |
| lore wiki, 12 sessions, `claude -p` sonnet-4-6 | 11 | 10 | 0 | 3 |
| lore wiki, 12 sessions, self-hosted GPT-OSS 120B | 9 | 3 | 1 | 8 |

- The self-hosted model named sessions it could not support in 8 of 9 observations.
- The hosted model named sessions it could not support in 3 of 11 observations.
- The check is what makes the gap between the two models visible.

All three runs executed on 2026-08-06. No run output sits in the repository.
Each report quotes private session prose, so a run writes its report outside
any wiki and outside any tracked directory. A reader reproduces a figure by
running the prototype again.

## Two defects the check itself had

The check shipped with two defects, and each scored real observations as
unconfirmed.

- The model wrapped quotes in literal double-quote characters. Exact matching then failed. One run reported zero confirmed observations where three were real.
- The model shortened a long quote with an ellipsis. Exact matching scored one observation as confirmed in zero sessions. The fact appears in seven sessions.
- The shortened-quote case concerned an observation about untracked configuration files holding credentials. The observation names a repository and filenames, so a public document does not repeat it.

Both defects support one conclusion:

- Model judgement fails unpredictably.
- Deterministic code fails reproducibly.
- A case exposes the defect, and a developer repairs the code in minutes.

## One observation, five differently worded quotes

A run confirmed one observation in five separate sessions: the sandbox blocked
`gh` through its secure computing mode (seccomp) and user namespace (userns)
restrictions. Each session reported the failure in different words, and the
model quoted each report separately.

- `verify` searches each quote independently against every session in the run.
- Each quote confirms the session whose prose holds the quote.
- The confirmed set is the union of the five single-quote results.
- No single quote has to appear in all five sessions.

## Names in the code

The code predates the vocabulary above, so a reader meets older identifiers.

| Term in this document | Identifier in the code |
|---|---|
| observation | `facts`, and each entry of the `facts` list |
| confirmed sessions | `verified_sessions` |
| a session the check did not confirm | `ghosts`, rendered as "claimed but not found" |
| sweep | `dream_probe.py`, the dream probe |

## See also

- `tools/dream_probe.py` — the prototype. Read `_norm`, `verify`, `load_session` and `recent_sessions`.
- [Why one flag, and not a session note](why-the-flag-is-the-crossing.md) — why one fact at a time crosses to the team layer. That document calls an observation a flag.
- [Why a session note is written only at the end](why-notes-are-written-at-session-end.md) — the rule that code, not the model, authors what a line claims.
