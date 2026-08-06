# Cross-session sweep over past transcripts

- Status: **conclusions retracted 2026-08-06.** An adversarial review refuted the placement result and the recurrence metric.
- Ran: 2026-08-06
- Tool: `tools/dream_probe.py`
- Wikis: ccat, lore
- Verdict: TODO — the owner has not ruled.

> **Read as evidence, not as doctrine.** An experiment records what one run
> measured on one day. A decision belongs in an ADR. Settled reasoning belongs
> in `docs/explanation/`.

> **Retraction.** Four adversarial reviews ran against the document on
> 2026-08-06. Two results do not stand, and a reader must not carry either
> forward. The ccat placement table measures a stale search index, not a wiki.
> The recurrence column counts a union over separate quotes, not a repeated
> fact. Both sections stay below with the refutation attached, because deleting
> a wrong result hides the correction that matters. See "What the review
> refuted".

---

## The question

Lore retires the session-note pipeline. Capture writes a transcript, and an
agent files an observation when a team-relevant fact appears. Two doubts remain.

- An agent judges a fact at the moment it holds the least context about the future.
- A human cannot review every candidate a busy wiki produces.

A sweep reads past sessions in one batch and files observations for facts no
artifact records. The experiment asks three questions.

- Does a batch pass find durable facts that per-session capture misses?
- Can code check the evidence, or must a human check every claim?
- How many observations reach a human after the wiki settles what it knows?

## What ran

1. `load_session` reduces a transcript to session prose: user and assistant text, with tool calls, tool results and reasoning removed.
2. `recent_sessions` fills a run to a character budget, newest transcripts first.
3. One synthesis call returns observations. Each carries a claim, a kind, the sessions behind it, and a verbatim quote per session.
4. `verify` searches each quote against the session prose it names. See [why quotes are verified, not trusted](../explanation/why-quotes-are-verified-not-trusted.md).
5. `vault_candidates` retrieves the topical notes each observation might belong in.
6. One placement call rules on each observation: known, extends, contradicts, or new.

## A transcript costs far more than its prose

Measured over 20 ccat sessions, 1,217,211 tokens in total.

| Part | Share | Tokens |
|---|---:|---:|
| tool results | 47.4% | 576,536 |
| tool calls | 35.2% | 428,782 |
| assistant prose | 10.0% | 121,137 |
| user prompts | 7.5% | 90,755 |

- Tool traffic holds 82.6% of a transcript.
- Prose alone cuts 1,217,211 tokens to 212,000.
- The agent narrates what a tool result taught it, so prose carries the interpretation.
- Error-only tool results cost 5,475 tokens across 61 blocks, or 0.9% of tool-result bytes.

An observation about a trap is born in an error. Adding error results alone
costs almost nothing. Adding successful results costs 571,061 tokens for file
dumps and command listings.

## The backend decides whether attribution is real

| Run | Observations | Confirmed | Confirmed in 2+ sessions | Named more than confirmed |
|---|---:|---:|---:|---:|
| ccat, 20 sessions, `claude -p` sonnet-4-6 | 18 | 14 | 3 | 7 |
| lore, 12 sessions, `claude -p` sonnet-4-6 | 11 | 9 | 0 | 3 |
| lore, 12 sessions, self-hosted GPT-OSS 120B | 9 | 3 | 1 | 8 |

- The self-hosted model named sessions it could not support in 8 of 9 observations.
- The hosted model did so in 3 of 11 observations.
- The self-hosted model mostly restated skill files it had read, not session content.
- The hosted model reported single-session facts and labelled them honestly.

A sweep therefore needs the verification step before it needs a better model.
Code catches a fabricated session list whatever produced it.

## Recurrence selects friction, never reasoning

Observations from the ccat run, by kind.

| Kind | Total | Recurrent |
|---|---:|---:|
| gotcha | 14 | 1 |
| decision | 2 | 0 |
| recurring-friction | 1 | 1 |
| preference | 1 | 1 |

- A decision recurs at zero percent. A team states a choice once and acts on it.
- A trap recurs, because an agent hits the same trap every week.
- Recurrence as a filter discards the class carrying a team's reasoning.

One decision-class observation records why a team accepted an operational
tradeoff, and names the staffing limit behind the choice. No pull request
records the reason. The observation describes a live access-control posture, so
a public document holds the shape and not the detail.

The sweep now records recurrence as a property and selects on durability.

## A time window sizes a run by the wrong variable

Seven-day windows over ccat, 2026-07-12 to 2026-08-05.

| Window opens | Sessions | Tokens |
|---|---:|---:|
| 07-12 | 81 | 426,054 |
| 07-19 | 13 | 153,846 |
| 07-26 | 22 | 344,784 |
| 08-02 | 4 | 7,584 |

- The largest window exceeds a 200,000-token context. The smallest holds less than one busy session.
- The spread across four windows reaches 56 times.
- ccat produced 120 sessions over 24 days. lore produced 12 over the same 24 days.

A token budget self-adjusts across both wikis from one rule. A calendar rule
suits one wiki and starves the other.

## The wiki settles less than expected

| Run | known | extends | contradicts | new |
|---|---:|---:|---:|---:|
| ccat, 18 observations | 0 | 3 | 0 | 15 |
| lore, 11 observations | 0 | 0 | 0 | 11 |
| control, 3 observations | 2 | 0 | 0 | 1 |

The control carries two facts lifted from `capture-retrieve-never-ask.md` and
one invented fact about a Kuiper belt object. The placement call returned known
for both real facts, named the correct note, and returned new for the invented
one. The classifier works, so a zero dedup rate is a measurement.

Three ccat observations found the note they extend.

- An operational tradeoff rationale routes to `projects/ops-db-api.md`.
- A step-ca renewal trap routes to `projects/system-integration/certificate-authority.md`.
- A GitHub App token requirement routes to `projects/chai-software/chai-gui-platform.md`.

### Retrieval must skip the session layer

- ccat holds 488 session notes against 29 topical notes.
- An unfiltered search returned session notes for every observation.
- A session note is the retired layer, so a match against one settles nothing.

## What the review refuted

Four reviews attacked the document on 2026-08-06. Each finding below was
reproduced against the code, the raw output or the search index.

**The ccat placement table measures a stale index.** The search index holds 510
rows for ccat. 487 of them point at files a parallel change deleted, and the
`observatory/` notes are absent. 14 of the 18 ccat observations reached the
placement call with no candidate note at all, so a `new` verdict was forced
rather than judged. Of the four observations that did receive candidates, three
came back `extends`.

**The control cannot certify that table.** The control ran against the lore
wiki, whose index is clean. A healthy-index control says nothing about a result
produced against a broken one.

**The recurrence column does not count recurrence.** No single quote in any
multi-session observation matched two sessions. Each count is a union over
separate quotes, one session each. Whether the quotes support one claim stays
the model's inference, which the check was built to remove.

**The over-count guarantee is false.** The check accepts a 12-character span
and searches every session, so a common phrase confirms sessions nobody
claimed. The document states the check under-counts and never over-counts. It
does both.

**Truncation, not budget, discarded the corpus.** The per-session cap dropped
67% of the ccat prose while 117,000 characters of the total budget stayed
unused. The document files the problem under unproven. The run had already
measured it.

**The recurrence-by-kind result rests on two observations.** Two decisions, one
preference, one recurring-friction. The same twelve sessions produced zero
decisions under one model and two under another, so the kind mix describes the
extractor.

**The accidental finding is harness output.** All five quotes paraphrase the
agent's own stock sandbox message, which the prompt told the model not to
report.

**The document cites no accepted decision.** ADR 0008 rejects a staged queue by
name, and the proposed split is one. ADR 0007 rejects an LLM pass that harvests
what an LLM wrote, and the sweep reads majority-model prose. Neither appears in
the text.

**The reports were one `git add -A` from publication.** The repository is
public and the default output path was not ignored. Fixed in the tool.

## What the run supports

Three claims survive the review.

- A batch pass finds some durable facts. A reviewer reading all 38 observations kept 4. Two are unambiguous: a step-ca renewal that a green timer never proves, and a cross-repo build that installs from a default branch.
- Code must check attribution, whatever the check's own defects. One model named sessions it could not support in 8 of 9 observations, and only a mechanical check made the gap visible.
- Recurrence must not select an observation. The kind table no longer carries the argument. A reviewer's count does: 33 of 38 observations describe the agent or its tools, and every ranking mechanism favours repetition.

Two claims are withdrawn.

- A token budget over a calendar window. The window spread depends on where the grid starts, and the comparison pitted an uncapped window against a capped budget.
- The risk split. ADR 0008 rejects the queue it proposes, and the human-gated branch never fired.

One claim was never a finding. `flag.propose_target` already excludes the
session layer. The prototype reimplemented shipped behaviour.

## What the run leaves unproven

- Deduplication sits at zero percent today, so a review queue does not yet converge. Whether the rate rises as the topical layer fills needs weeks of running.
- Nobody has read sweep output as a working reader. The experiment measures production, never readership.
- The contradiction verdict never fired across 29 observations, so the staleness benefit stays a claim.
- A run truncates every session to a fixed size. One 193,000-character session can crowd out shorter ones, and a per-session share of the run would fix it. No run has tested the fix.
- Verification matches a quote, so it detects a repeated wording. A fact restated in different words reads as single-session.
- Opus never ran. Sonnet's residual error rate may or may not justify the cost.

## One finding the experiment rediscovered

An observation confirmed in five ccat sessions reports that the secure
computing mode (seccomp) and user namespace (userns) restrictions on the
developer machine block `gh`. The agent retries outside the sandbox every time.

An earlier draft called the observation new. The claim was false. A memory file
recorded the same failure on 2026-08-04, two days before the run, and the same
file already held two further observations the runs report. The correction
matters more than the finding: a sweep that does not read where a team already
writes reports rediscovery as discovery, and every yield figure above inherits
the error.

## Reproducing a figure

```
python3 tools/dream_probe.py --wiki ccat --sessions 20 --backend subscription
python3 tools/dream_probe.py --wiki ccat --sessions 20 --check <run>.json
python3 tools/dream_probe.py --self-check
```

No run output sits in the repository. Each report quotes private session prose,
so a run writes outside any wiki and outside any tracked directory.
