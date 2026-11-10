# ADR 0019: The lab notebook

- **Status:** Accepted
- **Date:** 2026-11-09
- **Deciders:** Christof Buchbender
- **Relates to:** PRD [0020](../prd/0020-index-first-context-and-lab-notebook.md)
  (index-first context and the lab notebook), epic
  [#555](https://github.com/buchbend/lore/issues/555); issue
  [#417](https://github.com/buchbend/lore/issues/417) (regime registry and
  lab-notebook brainstorm)
- **See also:** ADR [0014](0014-one-home-per-fact.md) (one home per fact),
  ADR [0017](0017-the-logbook.md) (the logbook, whose sinks the lab notebook
  reuses)

## Holds

- **Default:** the lab notebook is the home for "What did we try, and what
  came out?". One lab entry records one experiment.
- **Default:** the `experiment` skill writes the entry at its end. A person
  can also run `lore lab add` directly. Lore extracts no entry from a
  transcript.
- **Default:** an entry has five labelled parts: `Question`, `Setup`,
  `Result`, `Interpretation` and `Next`. `lore lab add` rejects an entry
  without the five labels.
- **Default:** Lore writes the stamp: date, author, repo, commit, dirty flag,
  branch and host, plus the command line and the data paths the caller
  names. The agent writes only the five parts.
- **Default:** an entry is dated and nobody edits it. A later entry in the
  same thread corrects it.
- **Default:** a dead end is a lab entry with status `dead-end`. A dead end
  in software work stays a closed issue, as the filing rule says.
- **Default:** Lore delivers an entry through the sinks of the scope. The
  list is `lab.sinks`, and `[file]` without the key.
- **Incidental:** the `file` sink writes one file per entry under
  `docs/lab/YYYY-MM/`. The word limit is 400.

## Revisit if

- Entries read as filler, such as an `Interpretation` that restates the
  `Result`.
- Lab work happens mostly outside a git repo, so the stamp holds no commit.
- Teams ask for plots or tables inside an entry.

## Background

Lore retired session notes (ADR 0007, session notes retired) because an
extraction without a deliberate author produced text nobody trusted. Facts
went to repo artifacts (ADR 0012, agents file facts as repo artifacts). Lab
work fits none of them. A calibration run or a trial is not a decision, not
a defect and not a how-to. Issue #417 recorded the gap on 2026-09-16.

Two outside observations shaped the decision. Agents that run many
experiments do not track by themselves what they tried, so an entry states
where its idea stands. Reviews of autonomous research agents found invented
numbers in narrated results. Lore therefore stamps the provenance, and the
agent writes only the reading of the result.

The entry starts at a deliberate act: a person starts the `experiment`
skill. The skill writes the question and the expected result before the run.
The entry then shows whether the result was a surprise.

## Alternatives considered

- **Log every run without a skill.** Rejected: the record fills with runs
  nobody asked a question about.
- **Extend the logbook with a second entry kind.** Rejected: the logbook
  serves a team lead who reads daily reports. A lab entry serves the person
  who repeats or builds on the experiment. One sink list for both would send
  each run to the daily report.
- **Lab entries in the wiki only.** Rejected as the default: the entry then
  sits away from the commit it describes. A scope can still add a sink that
  writes to the wiki.
- **The regime registry of issue #417 first.** Rejected for now: the lab
  notebook needs one row in the table of homes, not a new config surface.

## Amendments

None.
