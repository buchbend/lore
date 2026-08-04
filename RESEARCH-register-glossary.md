# Research notes — register, glossary, agent output style

Two web surveys run 2026-08-04 as input to the level-1 design (join the
register to `CONTEXT.md`). Informative only. Kept verbatim so the PRD can
cite them; delete once the PRD lands.

Both agents fetched their links rather than citing from memory, and marked
where a claim came only from a search snippet.

---

## Survey A — prior art: glossary-enforced writing

### The reverse check exists: a spell checker pointed at two word lists

Vale's `spelling` check uses Hunspell dictionaries and flags any word absent
from the dictionaries supplied. Keys: `dictionaries`, `dicpath`, `append`,
`filters`, `ignore`, `custom`. Common English is the base dictionary; the
project glossary is the `ignore` list. A word in neither is flagged,
including terms nobody anticipated. <https://docs.vale.sh/checks/spelling>

One detail decides whether this works. Vale's built-in filters skip
mixed-case words ("MongoDB"), all-uppercase acronyms, and words containing
digits. Those are exactly the invented short names in question. By default
they are invisible. `custom: true` plus explicit filters is required.

cspell does the same with an explicit human review loop: define
`project-words.txt`, run `cspell --words-only --unique "**/*.md"`, read the
list, delete typos, keep the rest. <https://cspell.org/docs/getting-started>

A second reverse check needs no list. Google's Vale style ships
`Acronyms.yml`: any standalone 3-5 letter uppercase token requires a spelled
out form, with 58 exceptions (API, HTML, JSON, URL). Severity `suggestion`.
<https://raw.githubusercontent.com/errata-ai/Google/master/Google/Acronyms.yml>

What is **not** the reverse check: Vale vocabularies are exception lists.
`accept.txt` enforces spelling of known terms, `reject.txt` errors on known
bad ones. A word in neither is not flagged for being absent.
<https://docs.vale.sh/keys/vocabularies> — textlint-rule-terminology only
maps known-wrong spellings to right ones.
<https://github.com/sapegin/textlint-rule-terminology>

Nothing checks meaning. No tool verifies a term is used with the sense the
glossary gives it. "One term, one meaning" stays human.

### ASD-STE100 outside aerospace

Issue 9, January 2025: 53 writing rules in 9 sections, ~900 approved words,
each with one meaning and one part of speech. Aimed at readers with limited
English proficiency. <https://www.asd-ste100.org/about_STE.html>

The maintenance group endorses no tooling: "ASD and STEMG do not endorse,
certify, or authorize any software tools, including AI-based ones."
<https://asd-ste100.org/STEsoftware.html>

The one checker with public internals is TechScribe's, on LanguageTool,
GBP 400/user/year. Part-of-speech disambiguation plus rules, so "work" passes
as a noun and fails as a verb. Precision 0.86, recall 0.98, deliberately
over-flagging. The vendor states the buyer "must customize the rules" with
its own technical nouns and verbs. <https://www.simplified-english.co.uk/>
and <https://www.simplified-english.co.uk/design.html>

So even the commercial STE checker ships with a hole where the domain
glossary goes, and expects a human to fill it.

Software-industry evidence is thin. Write the Docs Australia 2023 ran an STE
workshop arguing for transfer, naming nothing kept or dropped.
<https://www.writethedocs.org/conf/australia/2023/workshop-ste/>

**No evidence found:** a named software team publishing "we adopted STE,
kept X, dropped Y, here is what happened".

### Controlled language programs that are enforced

EARS: Mavin and colleagues at Rolls-Royce, 2009, from analysing airworthiness
regulations. Listed adopters: Airbus, Bosch, Dyson, Honeywell, Intel, NASA,
Rolls-Royce, Siemens. Claims effectiveness for authors whose first language
is not English, with no study behind the claim.
<https://alistairmavin.com/ears/>

A mandate plus reporting does not produce good writing. Under the US Plain
Writing Act the Center for Plain Language grades 21 agencies. In 2022 nearly
two thirds earned an A for organisational compliance. Average grade for
actual writing: C, down from B-. No reader outcome is measured.
<https://centerforplainlanguage.org/2022-federal-plain-language-report-card/>

GOV.UK's own research background states "there is very little research that
underpins the choice of one convention over another". It cites nothing for
reading-age targets, sentence-length limits, or acronym rules.
<https://www.gov.uk/government/publications/govuk-content-principles-conventions-and-research-background/govuk-content-principles-conventions-and-research-background>

### Glossary in the repo, tied to artifacts

Auto-extraction rots: the Living Documentation Maven plugin generates a
glossary from a `@Glossary` annotation. Last release 0.3, January 2017.
<https://livingdocumentation.github.io/livingdoc-maven-plugin/glossary.html>

Auto-maintained vocabularies drift to one maintainer: `vale-at-rocky`, four
vocabulary folders, 0 stars, 89 commits, no CI.
<https://github.com/ambaradan/vale-at-rocky>

One tool enforces "term must be defined" as a build warning: Sphinx warns
when a `:term:` reference has no glossary entry, and `-n` makes cross
reference checking strict. Limit: only terms an author explicitly marked up.
<https://www.sphinx-doc.org/en/master/usage/referencing.html>

**Could not verify:** Acrolinx "term harvesting", the closest commercial
match. Both documentation URLs returned 404 and 403. It is auto-extraction
anyway.

### Docs-as-code enforcement, and how it fails

Contentsquare: Vale in the editor, GitHub Actions annotating PRs, local CLI.
Non-blocking. First run produced 673 errors, 12,910 warnings, 14,878
suggestions across 82 files — judged not actionable. Fix was cherry-picking
individual Google rules instead of adopting a whole style.
<https://engineering.contentsquare.com/2023/using-vale-to-help-engineers-become-better-writers/>

Meilisearch: Vale on every PR, sentence cap moved 45 to 40 with 35 as target.
Advice: few rules first, tune gradually, do not block PRs. Their docs no
longer use Vale after a site migration.
<https://www.meilisearch.com/blog/prose-linting-with-vale>

GitLab, the severity model worth copying: error fails CI and shows in the
diff; warning shows in the diff and does not fail; suggestion appears only in
local editors. The git hook reports errors only. Escape hatches documented
(`<!-- vale off -->`). They tell writers to add product names to the spelling
exceptions rather than rewrite the sentence.
<https://docs.gitlab.com/development/documentation/testing/vale/>

Datadog: custom `words.yml`, `oxfordcomma.yml`, `abbreviations.yml`. Rules
firing inside image shortcodes was a problem. Scale: 20,000+ docs PRs in
2023, on-call writer reviewing 40+/day.
<https://www.datadoghq.com/blog/engineering/how-we-use-vale-to-improve-our-documentation-editing-process/>

LWN on Vale 3.0 names Grafana Labs, GitLab, Angular, Fedora, Red Hat, and
warns that a strict `MinAlertLevel` in CI frustrates contributors when a pull
request fails over passive voice or an unexplained acronym.
<https://lwn.net/Articles/964075/>

**No evidence found:** any team running a prose linter over GitHub issue
bodies or PR descriptions in CI.

### Readability for non-native technical readers

Shubert, Spyridakis, Holmback, Coney (1995), JTWC 25(4):347-369. Airplane
maintenance procedures, Simplified English versus not. SE significantly
improved comprehension of the more complex documents, and readers located
information more easily. Reading time about equal. On the relevant point:
"the comprehensibility and content location scores for the native and
non-native speakers appear to be quite different, with the non-native
speakers benefiting from SE more than the native speakers." The authors add
this could not be tested statistically because of very different cell sizes.
<https://journals.sagepub.com/doi/10.2190/WG69-D74B-4DLL-2WBK>

Chervak, Drury, Ouellette (1996). 175 practising aircraft maintenance
technicians, 16 workcards. "Comprehension was significantly improved with
Simplified English, particularly for the Difficult workcards and for
non-native English speakers." Layout had no measurable effect.
<https://researchconnect.buffalo.edu/en/publications/simplified-english-for-aircraft-workcards/>
A widely repeated 18% to 14% error-rate figure could not be traced to the
abstract. Do not quote it.

**Sentence-length ceilings: no verified evidence found.** The repeated
"100% comprehension at 8 words, 90% at 14, under 10% at 43" appears in blog
posts and could not be traced to a primary source. The 20-word rule is
defensible as practice, not as a measured threshold.

**Abbreviation policy: no research found.** The only implemented policy
verified is Google's Vale acronym rule.

This section is genuinely thin: two studies, both aviation, both 1990s, is
the entire verified evidence base.

---

## Survey B — how teams steer agent prose style

### The instruction-file layer

`AGENTS.md` is plain Markdown, no schema. Its spec says nothing about prose
style. <https://agents.md/>

Documented limits: Claude Code has no cap but warns that bloated files cause
Claude to ignore actual instructions
(<https://code.claude.com/docs/en/best-practices>); Copilot caps instructions
at two pages and requires they not be task specific
(<https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions>);
Cursor asks for under 500 lines per rule
(<https://cursor.com/docs/context/rules>); Windsurf allows 12,000 characters
per workspace rule file (<https://docs.devin.ai/desktop/cascade/memories>).

Published evidence:

- ETH Zurich (Gloaguen, Mündler, Müller, Raychev, Vechev; Feb 2026, rev. Jun
  2026): context files "do not generally improve task success rates, while
  increasing inference cost by over 20% on average", for both LLM-generated
  and developer-committed files. Measures coding task success, not prose
  quality. <https://arxiv.org/abs/2602.11988>
- McMillan (May 2026), 1,650+ Claude Code sessions: none of file size,
  instruction position, file architecture, or contradictions had a measurable
  effect on adherence. What did: decay within a session, about 5.6% lower
  odds of compliance per generated function.
  <https://arxiv.org/abs/2605.10039>
- Chatlatanagulchai et al. (Sep 2025), 253 `CLAUDE.md` files: content
  clusters on commands, implementation notes, architecture. No prose-style
  breakdown. <https://arxiv.org/abs/2509.14744>

Length practice: HumanLayer recommends under 300 lines and keeps their own
under sixty, citing a rough 150-200 instruction ceiling for frontier models.
<https://www.humanlayer.dev/blog/writing-a-good-claude-md>

`agent-style` (Yue Zhao, USC): 21 rules, 12 canonical and 9 from observed LLM
tics — over-bulleting, dash overuse, repetitive sentence openers, transition
word overuse, summary closers, inconsistent terminology. Author-reported
violation reductions of 45/45/82% across three models, explicitly
"directional, not statsig". <https://github.com/yzhao062/agent-style>

### Built-in output-style features

Claude Code output styles modify the system prompt: "change how Claude
responds, not what Claude knows." Plugins can ship them, and
`force-for-plugin: true` applies one whenever the plugin is enabled.
<https://code.claude.com/docs/en/output-styles>

Three limits: read once at session start, so a change needs `/clear`; **they
never reach subagents**, which run their own system prompt; and they remain
advisory. Anthropic's steering guide states that CLAUDE.md, rules, skills and
output styles are advisory, and "a real guardrail needs to be deterministic,
and the enforcement methods are hooks and permissions."
<https://claude.com/blog/steering-claude-code-skills-hooks-rules-subagents-and-more>

Equivalents elsewhere are thin. The only one found is CodeRabbit's
`tone_instructions`, free text changing the voice of review comments.
<https://www.coderabbit.ai/blog/tone-customizations-roast-your-code>

### Generation-time versus CI linting

Mature teams run both with no machinery beyond the linter binary — Datadog
(editor, Action, local) and GitLab (CI jobs, editor integration, optional
pre-push hooks, with documented per-file disables).
<https://docs.gitlab.com/development/documentation/testing/>

Agent-fixes-its-own-lint is claimed almost entirely by vendors. Fern
describes their agent parsing a Vale error log into a correction commit, with
no named external teams and no data.
<https://buildwithfern.com/post/docs-linting-guide>

Anthropic's skill-authoring guide documents the draft/lint/revise pattern
with a style guide as validator: draft, review against the checklist, note
each issue with a section reference, revise, re-check, "only proceed when all
requirements are met."
<https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices>

**No evidence found:** agents adding suppressions or disabling prose rules to
reach green. Unknown, not absent.

Reported friction is human, not agent: LWN warns that flagging minor
violations in PRs may discourage participation, and that custom rules need
constant updating. <https://lwn.net/Articles/964075/>

### Vocabulary — the cautionary read

**No evidence found** of any team letting an agent write terms back into a
vocabulary list unreviewed. Datadog keeps rules and vocabulary in an open
repo so teams contribute terms by pull request, reviewed by people.

The `nix.dev` PR introducing Vale is the sharpest small-team record. False
positives on proper nouns pushed the author to cut scope to "only the
(hopefully) uncontroversial bits", deferring spellcheck entirely. A reviewer
argued for actively maintaining the vocabulary rather than disabling the
spellchecker. Merged Nov 2023.
<https://github.com/NixOS/nix.dev/pull/798>

Inference from the surveying agent: vocabulary is where a prose linter throws
most false positives, so it is where the pull to auto-append is strongest —
and an agent appending its own invented term silently disables the rule that
would have caught the invention.

### Templates and structured output

GitHub issue forms are YAML with typed fields and `validations: required`,
enforced at submission, so the writer cannot skip a required field.
<https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms>

Anthropic's advice distinguishes strict from flexible templates, and
recommends input/output example pairs over describing a style in prose.
<https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices>

Agents skip templates, three confirmed reports: Cursor background agents
ignore PR title and description instructions
(<https://forum.cursor.com/t/agent-mostly-ignores-instructions-about-pr-title-description/144792>),
Codex Cloud's Create PR button bypasses `pull_request_template.md`
(<https://github.com/openai/codex/issues/6750>), and VS Code's GitHub Pull
Request extension does not read the template when generating
(<https://github.com/orgs/community/discussions/181819>).

All three failures occur when the *tool* composes the body, not when the
agent shells out to `gh pr create` with a body it wrote.

Cost of hard structure: Tam et al. (Aug 2024) find that constraining output
to JSON/XML produces "a significant decline in LLMs reasoning abilities", and
stricter constraints degrade performance more.
<https://arxiv.org/abs/2408.02442>

**No evidence found** for a head-to-head comparison of a fixed skeleton
against prose rules.

### Review-specific style

Conventional Comments is the one widely-used house format and costs nothing —
a text convention, not a tool. Template: `<label> [decorations]: <subject>`
then optional discussion. Labels: praise, nitpick, suggestion, issue, todo,
question, thought, chore, note. Rationale: "comments that are easy to grok
and grep"; prefixing with a label makes intention clear and changes tone.
Explicitly machine-parseable. <https://conventionalcomments.org/>

Copilot code review imposes its own shape and refuses format changes.
GitHub's list of what does not work includes attempts to change the
formatting of Copilot comments and vague asks. What works: "short, imperative
rules are more effective than long paragraphs".
<https://github.blog/ai-and-ml/github-copilot/unlocking-the-full-power-of-copilot-code-review-master-your-instructions-files/>

CodeRabbit splits what-to-flag (`reviews.path_instructions`, globs, AST-grep)
from voice (`tone_instructions`).
<https://docs.coderabbit.ai/guides/review-instructions>
Greptile is explicit that custom rules control what gets flagged, not comment
format. <https://www.greptile.com/docs/code-review/custom-standards>

One first-hand non-vendor account: Kaz Sato runs a `docs-reviewer` subagent
emitting Critical / Warnings / Suggestions, with standards embedded in the
subagent config rather than a separate style file, then fixes interactively.
<https://medium.com/google-cloud/supercharge-tech-writing-with-claude-code-subagents-and-agent-skills-44eb43e5a9b7>
This is the mirror of the output-style limit above: because subagents do not
inherit output styles, style has to live in the subagent's own definition.

### Known failure modes at small scale

Anthropic names "the over-specified CLAUDE.md" as a standard failure and
prescribes ruthless pruning: if the model already does something correctly
without the instruction, delete it or convert it to a hook.
<https://code.claude.com/docs/en/best-practices>

A first-hand account: a 200-line CLAUDE.md ignored, 258 knowledge-base files
written and never retrieved; the author cut to about 20 rules, moved
enforcement into hooks, and reset the target from 100% to 80% compliance.
<https://dev.to/minatoplanb/i-wrote-200-lines-of-rules-for-claude-code-it-ignored-them-all-4639>

Instruction files can silently stop being read, with no error, when product
support changes underneath.
<https://learn.microsoft.com/en-ie/answers/questions/5832308/github-copilot-code-review-on-github-com-seems-to>

**No evidence found** on: any published measurement of prose quality with
versus without a style file, and any team publishing before/after data on
agent-written issue or PR text.
