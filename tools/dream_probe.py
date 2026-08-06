#!/usr/bin/env python3
"""Cross-session dream probe — is recurrence a better relevance signal than capture-time judgement?

Reads the N most recent transcripts of one wiki, runs ONE synthesis pass over
all of them together, and writes the proposed facts to a file. Answers one
question with real data: does looking backward across sessions surface things
that per-session capture structurally cannot see?

The probe is deliberately not wired into the curator, the CLI, or any hook.
It is an experiment, and it is meant to be deleted or promoted once it has
reported.

Three properties make the result meaningful rather than another summariser:

  • **Every claim carries a quote.** The synthesis reads transcripts, not
    composed prose, so each claim can point at the words that produced it.
    A claim without a quote is a claim nobody can check.
  • **Recurrence is recorded, never required.** A measured run showed the
    decision kind recurring at zero percent: a choice is stated once and
    never restated, while a trap is hit every week. Selecting on recurrence
    would discard exactly the facts carrying a team's reasoning.
  • **The vault settles what is already known.** Search retrieves the notes
    each observation might belong in, and one call places it: known, extends,
    contradicts, or new. The queue a human sees is what survives.

Output is a new file at --out. The probe never writes into a wiki and never
edits an existing note, so a bad run costs one `rm`.

Usage:
    python3 tools/dream_probe.py --wiki lore --sessions 12
    python3 tools/dream_probe.py --wiki lore --dry-run       # prompt only, no LLM
    python3 tools/dream_probe.py --wiki ccat --check run.json  # place a saved run
    python3 tools/dream_probe.py --self-check                # no vault needed
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

# ponytail: char budgets, not tokens — a tokeniser dependency buys accuracy
# nobody needs to decide whether a hypothesis is worth pursuing.
PER_SESSION_CHARS = 24_000
TOTAL_CHARS = 400_000

_TOOL_NAME = "propose_facts"


@dataclass
class SessionText:
    """One transcript reduced to the prose a reader would care about."""

    session_id: str
    when: datetime
    text: str
    turns: int
    truncated: bool


def load_session(path: Path, budget: int = PER_SESSION_CHARS) -> SessionText | None:
    """Reduce one transcript file to budgeted prose, or None if it holds none.

    Keeps user and assistant *text* only. Tool calls, tool results and
    reasoning blocks are dropped: they are the bulk of a transcript's bytes
    and almost none of its durable content. Preferences, gotchas and
    decisions are stated in words by one of the two speakers.

    Over budget, the middle is dropped rather than the tail. Which facts
    mattered is only knowable at the end of a session (ADR 0003), so the
    ending is the one part that must survive; the opening carries the
    framing, and the middle is where the process narration lives.
    """
    from lore_adapters.claude_code import ClaudeCodeAdapter
    from lore_core.types import TranscriptHandle

    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    handle = TranscriptHandle(
        integration="claude_code",
        id=path.stem,
        path=path,
        cwd=path.parent,
        mtime=mtime,
    )
    turns = [
        t
        for t in ClaudeCodeAdapter().read_slice(handle)
        if t.text and t.text.strip() and t.role in ("user", "assistant")
    ]
    if not turns:
        return None

    body = "\n\n".join(f"[{t.role}] {t.text.strip()}" for t in turns)
    truncated = len(body) > budget
    if truncated:
        head = budget // 3
        tail = budget - head
        dropped = len(body) - budget
        body = (
            f"{body[:head]}\n\n[... {dropped} chars of middle dropped ...]\n\n{body[-tail:]}"
        )

    return SessionText(
        session_id=path.stem[:8],
        when=mtime,
        text=body,
        turns=len(turns),
        truncated=truncated,
    )


def recent_sessions(
    wiki_dir: Path, count: int, total_budget: int = TOTAL_CHARS
) -> list[SessionText]:
    """Newest-first transcripts for one wiki, oldest-first once loaded.

    Stops early when the accumulated prose would exceed ``total_budget``.
    A local backend returns empty output on an oversized prompt rather than
    erroring, so the cap has to hold here — there is no downstream check
    that would catch the overflow.
    """
    paths = sorted(
        (wiki_dir / ".transcripts").glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:count]

    out: list[SessionText] = []
    used = 0
    for p in paths:
        s = load_session(p)
        if s is None:
            continue
        if used + len(s.text) > total_budget:
            print(
                f"  budget reached at {len(out)} sessions "
                f"({used:,} chars) — {len(paths) - len(out)} dropped",
                file=sys.stderr,
            )
            break
        out.append(s)
        used += len(s.text)

    out.reverse()  # oldest first: recurrence reads more naturally forward
    return out


def fact_schema() -> dict:
    return {
        "name": _TOOL_NAME,
        "description": "Report facts worth carrying out of these sessions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "facts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim": {
                                "type": "string",
                                "description": "One sentence. Active voice, named actor.",
                            },
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "preference",
                                    "gotcha",
                                    "decision",
                                    "contradiction",
                                    "recurring-friction",
                                ],
                            },
                            "sessions": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Session ids this claim is grounded in.",
                            },
                            "quotes": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Verbatim span per session, <=30 words each.",
                            },
                            "why_durable": {
                                "type": "string",
                                "description": "Why a reader three months out still needs this.",
                            },
                            "proposed_target": {
                                "type": "string",
                                "description": "Vault note this belongs in, or 'new note: <slug>'.",
                            },
                        },
                        "required": [
                            "claim",
                            "kind",
                            "sessions",
                            "quotes",
                            "why_durable",
                            "proposed_target",
                        ],
                    },
                }
            },
            "required": ["facts"],
        },
    }


def _norm(s: str) -> str:
    """Fold a span to its comparable core: unicode, punctuation, whitespace, case.

    Models re-emit quotes through their own tokeniser, so a verbatim span comes
    back with non-breaking hyphens, curly quotes and reflowed newlines. Those
    differences are not evidence of fabrication and must not read as such.
    """
    s = unicodedata.normalize("NFKC", s)
    for ch in "‐‑‒–—−":
        s = s.replace(ch, "-")
    for ch in "‘’‛":
        s = s.replace(ch, "'")
    for ch in "“”":
        s = s.replace(ch, '"')
    # Models wrap a quoted span in their own quote marks and close a truncated
    # one with an ellipsis. Both are packaging, not content, and neither may
    # cost the span its match.
    return " ".join(s.split()).casefold().strip("\"'…. ")  # noqa: B005 - a char set, intentionally


def verify(facts: list[dict], sessions: list[SessionText]) -> None:
    """Attach the sessions whose prose actually holds a quote from the observation.

    The model names the sessions behind a claim, and that naming is the whole
    recurrence signal — the one thing that cannot be taken on trust. Substring
    search settles it for free.

    Searches every session in the run, not only the ones the model named, so
    the check recomputes attribution instead of accepting or rejecting it.

    Mutates each observation in place, adding ``verified_sessions``. An
    observation confirmed in one session is a single-session observation
    however many sessions it named.
    """
    session_prose = {s.session_id: _norm(s.text) for s in sessions}
    for f in facts:
        hits: list[str] = []
        for q in f.get("quotes", []):
            # A model shortens a long quote with an ellipsis. Search the longest
            # surviving fragment: a shortened quote is still evidence, and
            # scoring it unfound would under-report real recurrence.
            fragments = [_norm(part) for part in re.split(r"\.\.\.|…", q)]
            search_span = max(fragments, key=len)[:160]
            if len(search_span) < 12:
                continue
            hits += [sid for sid, text in session_prose.items() if search_span in text]
        f["verified_sessions"] = sorted(set(hits))


# Words that carry no retrieval signal. Kept short on purpose: an aggressive
# list drops the domain words that make a query distinctive.
_STOP = frozenset("""
the a an and or but for nor with from into that this these those than then
when where which while who whom whose what have has had been being was were
are is not never always must should would could shall will can may might
each every some any all both more most other another such only own same
""".split())  # noqa: SIM905 - a word list reads better than a literal


def search_terms(claim: str, limit: int = 8) -> str:
    """Distinctive words from a claim, joined into one search query.

    Full-text search treats spaces as AND, so a whole sentence matches
    nothing. Identifiers and long words carry the signal, so the query keeps
    them and drops the connective tissue.
    """
    # A leading digit is allowed: a version, a port and an error code are
    # among the most distinctive terms a claim can carry.
    words = re.findall(r"[A-Za-z0-9_][A-Za-z0-9_.\-]{3,}", claim)
    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        low = w.casefold()
        if low in _STOP or low in seen:
            continue
        seen.add(low)
        out.append(w)
    return " ".join(out[:limit])


def vault_candidates(facts: list[dict], wiki: str, k: int = 3) -> None:
    """Attach the notes each observation might belong in.

    Retrieval stays deterministic. The model never picks which notes to
    consider, so it cannot invent a target that does not exist.
    """
    from lore_search.fts import FtsBackend

    backend = FtsBackend()
    backend.reindex(wiki=wiki)  # incremental: compares a hash per note
    for f in facts:
        # Over-fetch, then keep the topical layer only. Session notes outnumber
        # topical notes roughly sixteen to one in a working wiki, so an
        # unfiltered query returns nothing else. Deduplicating against a
        # session note is worthless anyway: the layer is being retired, and it
        # is the layer nobody reads.
        hits = [
            h
            for h in backend.search(search_terms(f["claim"]), wiki=wiki, k=k * 6)
            if not h.path.startswith("sessions/")
        ][:k]
        f["vault_hits"] = [
            {
                "path": h.path,
                "excerpt": (h.snippet or h.description or "")[:400],
            }
            for h in hits
        ]


_VAULT_TOOL = "place_observations"


def vault_schema() -> dict:
    return {
        "name": _VAULT_TOOL,
        "description": "Say where each observation belongs in the wiki.",
        "input_schema": {
            "type": "object",
            "properties": {
                "placements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "index": {
                                "type": "integer",
                                "description": "The observation's number, as given.",
                            },
                            "verdict": {
                                "type": "string",
                                "enum": ["known", "extends", "contradicts", "new"],
                            },
                            "note": {
                                "type": "string",
                                "description": "Candidate note path, empty when new.",
                            },
                            "reason": {
                                "type": "string",
                                "description": "One sentence naming the note text you judged.",
                            },
                        },
                        "required": ["index", "verdict", "note", "reason"],
                    },
                }
            },
            "required": ["placements"],
        },
    }


VAULT_PROMPT = """\
You are placing {n} observations into a wiki that already holds notes.

Each observation below carries the notes a search found for it. Decide where
the observation belongs. Use only the notes shown. Never name a note that does
not appear under the observation you are judging.

Verdicts:

- known — a note already records the fact. Nothing to add. Prefer this verdict
  whenever the note carries the same fact in different words.
- extends — a note covers the topic but not the fact. Name that note.
- contradicts — a note states something the observation reverses or refutes.
  Name that note. A human resolves the conflict, so choose this verdict only
  when the two genuinely cannot both hold.
- new — no note shown covers the topic.

A candidate note that merely shares vocabulary with the observation is not a
match. Judge on the fact, not on the words.

"""


def check_against_vault(
    facts: list[dict], wiki: str, client, model: str
) -> None:
    """Classify every observation against the notes retrieved for it.

    One call for the whole run. Placement is a judgement about meaning, so it
    needs the model; which notes are eligible is not, so retrieval already
    settled that.
    """
    vault_candidates(facts, wiki)

    parts = [VAULT_PROMPT.format(n=len(facts))]
    for i, f in enumerate(facts):
        parts.append(f"\n--- OBSERVATION {i} ---\n{f['claim']}\n")
        if not f["vault_hits"]:
            parts.append("(search found no candidate notes)\n")
        for h in f["vault_hits"]:
            parts.append(f"  [note {h['path']}]\n  {h['excerpt']}\n")

    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        tools=[vault_schema()],
        tool_choice={"type": "tool", "name": _VAULT_TOOL},
        messages=[{"role": "user", "content": "".join(parts)}],
    )
    placements = {}
    for block in getattr(resp, "content", []):
        if getattr(block, "name", None) == _VAULT_TOOL:
            for p in block.input.get("placements", []):
                placements[p.get("index")] = p
            break

    for i, f in enumerate(facts):
        p = placements.get(i, {})
        f["vault_verdict"] = p.get("verdict", "unplaced")
        f["vault_note"] = p.get("note", "")
        f["vault_reason"] = p.get("reason", "")
        # A named note that retrieval never offered is a fabricated target.
        # Assign unconditionally: a re-check reuses the saved run, so a key
        # left from an earlier placement would outlive the verdict that set it.
        offered = {h["path"] for h in f["vault_hits"]}
        f["vault_note_unoffered"] = bool(f["vault_note"] and f["vault_note"] not in offered)


PROMPT_HEAD = """\
You are reading {n} complete work sessions between one person and a coding agent,
oldest first. Report what a teammate joining this work would need to know.

Report a fact when it holds beyond the session that produced it:

- preference — how this person wants work done, stated or demonstrated repeatedly
- gotcha — a trap in the environment or codebase that cost real time
- decision — a choice made and acted on, where the reasoning matters later
- contradiction — an earlier position that a later session reversed; give both
- recurring-friction — the same obstacle hit in more than one session

Rules:

- Judge a fact on whether it outlives its session, never on how often it recurs.
  A decision is stated once and never repeated. A trap is hit every week. Both
  belong here. Name every session a fact appears in, and stop there.
- Every fact carries a verbatim quote per session listed. Quote the transcript
  exactly. If you cannot quote it, do not report it.
- Report nothing that is already obvious from the code, the git history, or a
  file in the repository. This is for what those do not record.
- Skip the work itself. "PR #350 merged" is a log entry, not a fact.
- No fact about how the sessions went, how long they took, or how they ended.
- Say nothing you are inferring. If two sessions merely feel related, drop it.

Return between 0 and 25 facts. Zero is a valid, useful answer.

"""


def build_prompt(sessions: list[SessionText]) -> str:
    parts = [PROMPT_HEAD.format(n=len(sessions))]
    for s in sessions:
        parts.append(
            f"\n===== SESSION {s.session_id} — {s.when:%Y-%m-%d %H:%M} "
            f"({s.turns} turns{', truncated' if s.truncated else ''}) =====\n{s.text}\n"
        )
    return "".join(parts)


def _burden_lines(facts: list[dict]) -> list[str]:
    """Summarise what a human would actually have to review.

    The review burden is the point of the vault check. An observation the
    vault already records costs a reader nothing, so the queue is what
    remains after the known ones drop out.
    """
    placed = [f for f in facts if f.get("vault_verdict")]
    if not placed:
        return []
    tally = {v: sum(1 for f in placed if f.get("vault_verdict") == v)
             for v in ("known", "extends", "contradicts", "new", "unplaced")}
    queue = tally["extends"] + tally["contradicts"] + tally["new"]
    lines = [
        f"- vault check: {tally['known']} known, {tally['extends']} extend,"
        f" {tally['contradicts']} contradict, {tally['new']} new"
        + (f", {tally['unplaced']} unplaced" if tally["unplaced"] else ""),
        f"- review queue: {queue}/{len(placed)} survive deduplication"
        f" — {tally['contradicts']} need a human, {queue - tally['contradicts']} could land marked",
    ]
    ghosts = sum(1 for f in placed if f.get("vault_note_unoffered"))
    if ghosts:
        lines.append(f"- **{ghosts} placements name a note search never offered**")
    return lines


def render(
    facts: list[dict], sessions: list[SessionText], wiki: str, engine: str = "?"
) -> str:
    """Proposals as markdown. Multi-session facts first — that ordering IS the finding."""
    # Verified recurrence, never claimed recurrence — the model's own session
    # list is the assertion under test, so it cannot also be the measure.
    multi = [f for f in facts if len(f.get("verified_sessions", [])) > 1]
    single = [f for f in facts if len(f.get("verified_sessions", [])) <= 1]
    overclaimed = sum(
        1
        for f in facts
        if len(f.get("sessions", [])) > len(f.get("verified_sessions", []))
    )
    span = (
        f"{sessions[0].when:%Y-%m-%d} → {sessions[-1].when:%Y-%m-%d}" if sessions else "—"
    )

    lines = [
        f"# Dream probe — {wiki}",
        "",
        f"- sessions read: {len(sessions)} ({span})",
        f"- facts proposed: {len(facts)} — {len(multi)} multi-session,"
        f" {len(single)} single-session",
        f"- attribution overclaimed on {overclaimed}/{len(facts)} facts",
        *_burden_lines(facts),
        # Synthesis quality tracks the backend closely enough that a result is
        # unreadable without knowing which one produced it.
        f"- engine: {engine}",
        "",
        "Proposals only. Nothing here has been written to the vault.",
        "",
        "## Confirmed in more than one session",
        "",
        "Recurrence is a property of a fact, never the test for keeping one.",
        "A decision is stated once. A trap is hit every week.",
        "",
    ]
    if not multi:
        lines += ["_None._", ""]

    for f in multi + [None] + single:  # type: ignore[list-item]
        if f is None:
            lines += [
                "## Confirmed in one session",
                "",
                "The decision kind lands here almost always. Nobody restates a choice"
                " already made.",
                "",
            ]
            if not single:
                lines += ["_None._", ""]
            continue
        claimed = f.get("sessions", [])
        confirmed = f.get("verified_sessions", [])
        ghosts = [s for s in claimed if s not in confirmed]
        lines.append(f"### {f.get('claim', '(no claim)')}")
        lines.append("")
        lines.append(f"- kind: `{f.get('kind', '?')}`")
        lines.append(f"- sessions: {', '.join(confirmed) or '— none confirmed'}")
        if ghosts:
            lines.append(f"- **claimed but not found**: {', '.join(ghosts)}")
        if f.get("vault_verdict"):
            note = f.get("vault_note") or "—"
            mark = " ⚠ not offered by search" if f.get("vault_note_unoffered") else ""
            lines.append(f"- vault: **{f['vault_verdict']}** → {note}{mark}")
            lines.append(f"  - {f.get('vault_reason', '')}")
        else:
            lines.append(f"- target: {f.get('proposed_target', '—')}")
        lines.append(f"- durable because: {f.get('why_durable', '—')}")
        for q in f.get("quotes", []):
            lines.append(f"  > {q}")
        lines.append("")

    return "\n".join(lines)


def _default_out(lore_root: Path, wiki: str) -> Path:
    """A report holds verbatim private prose, so it defaults beside quarantine.

    The previous default was a bare filename, which resolved against the
    current directory. Running from a repository checkout put session quotes
    one ``git add -A`` away from a commit, and `buchbend/lore` is public.
    """
    out_dir = lore_root / ".lore" / "dream"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"dream-{wiki}-{datetime.now():%Y-%m-%d-%H%M}.md"


def _refuse_if_git_visible(out: Path) -> None:
    """Stop before writing session prose anywhere git would pick it up."""
    import subprocess

    parent = out.resolve().parent
    inside = subprocess.run(
        ["git", "-C", str(parent), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True, check=False,
    )
    if inside.stdout.strip() != "true":
        return
    ignored = subprocess.run(
        ["git", "-C", str(parent), "check-ignore", "-q", str(out)],
        capture_output=True, check=False,
    )
    if ignored.returncode != 0:
        raise SystemExit(
            f"refusing to write {out}: inside a git work tree and not ignored.\n"
            "A report quotes private session prose verbatim. Pass --out under "
            "an ignored path, or accept the default."
        )


def _client(args, lore_root: Path):
    """Resolve a backend through the curator's chain, never a PATH probe."""
    from lore_curator.llm_client import _resolve_backend, make_llm_client

    os.environ.setdefault("LORE_CLAUDE_TIMEOUT_S", str(args.timeout))
    backend = _resolve_backend(args.backend, lore_root)
    client = make_llm_client(backend=backend, lore_root=lore_root)
    return client, backend


def recheck(args, lore_root: Path, sessions: list[SessionText]) -> int:
    """Re-verify a saved run and place it against the vault. No synthesis call.

    Placement is worth iterating on, and re-synthesising a run to change one
    downstream stage wastes the expensive half of the pipeline.
    """
    facts = json.loads(args.check.read_text())
    verify(facts, sessions)

    client, backend = _client(args, lore_root)
    if client is None:
        print("no LLM backend available", file=sys.stderr)
        return 1
    engine = f"{type(client).__name__} · backend={backend or 'auto'} · {args.model}"
    print(f"engine: {engine}", file=sys.stderr)

    check_against_vault(facts, args.wiki, client, args.model)

    out = args.out or args.check.with_suffix(".checked.md")
    _refuse_if_git_visible(out)
    from lore_core.io import atomic_write_text

    atomic_write_text(args.check, json.dumps(facts, indent=2))
    atomic_write_text(out, render(facts, sessions, args.wiki, engine))
    for line in _burden_lines(facts):
        print(line.replace("**", ""))
    print(f"\n{out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wiki", default="lore", help="wiki name under $LORE_ROOT/wiki/")
    ap.add_argument("--sessions", type=int, default=12, help="how many recent transcripts")
    ap.add_argument("--out", type=Path, help="output file (default dream-<wiki>-<date>.md)")
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument(
        "--backend", default=None, help="subscription | api | openai (default: your config)"
    )
    ap.add_argument("--timeout", type=int, default=900, help="seconds for a claude -p call")
    ap.add_argument("--dry-run", action="store_true", help="build the prompt, skip the LLM")
    ap.add_argument("--self-check", action="store_true", help="run asserts and exit")
    ap.add_argument(
        "--check",
        type=Path,
        help="re-verify a saved .json and run the vault check, without re-synthesising",
    )
    args = ap.parse_args()

    if args.self_check:
        return self_check()

    lore_root = Path(os.environ.get("LORE_ROOT", Path.home() / "lore"))
    wiki_dir = lore_root / "wiki" / args.wiki
    if not (wiki_dir / ".transcripts").is_dir():
        print(f"no transcripts at {wiki_dir}/.transcripts", file=sys.stderr)
        return 1

    sessions = recent_sessions(wiki_dir, args.sessions)
    if not sessions:
        print("no readable transcripts", file=sys.stderr)
        return 1

    if args.check:
        return recheck(args, lore_root, sessions)

    prompt = build_prompt(sessions)
    print(
        f"{len(sessions)} sessions, {len(prompt):,} chars "
        f"(~{len(prompt) // 4:,} tokens)",
        file=sys.stderr,
    )

    if args.dry_run:
        print(prompt)
        return 0

    from lore_curator.llm_client import _resolve_backend, make_llm_client

    # A whole-vault synthesis prompt is far larger than a per-chunk extraction,
    # so the client's 300s default trips before the call finishes.
    os.environ.setdefault("LORE_CLAUDE_TIMEOUT_S", str(args.timeout))

    # Resolve through the curator's own chain (flag → env → .lore/config.yml)
    # rather than make_llm_client's auto-probe, which prefers whatever binary
    # is on PATH and would quietly ignore a configured backend.
    backend = _resolve_backend(args.backend, lore_root)
    if backend not in ("subscription", "api") and len(prompt) > 120_000:
        print(
            f"  warning: {len(prompt):,} chars against backend {backend!r}. "
            "A self-hosted model may return empty rather than error at this "
            "size — lower --sessions if you get 0 facts.",
            file=sys.stderr,
        )
    client = make_llm_client(backend=backend, lore_root=lore_root)
    if client is None:
        print("no LLM backend available — set LORE_LLM_BACKEND or --backend", file=sys.stderr)
        return 1

    engine = f"{type(client).__name__} · backend={backend or 'auto'} · {args.model}"
    print(f"engine: {engine}", file=sys.stderr)

    resp = client.messages.create(
        model=args.model,
        max_tokens=8192,
        tools=[fact_schema()],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        messages=[{"role": "user", "content": prompt}],
    )

    facts: list[dict] = []
    for block in getattr(resp, "content", []):
        if getattr(block, "name", None) == _TOOL_NAME:
            facts = block.input.get("facts", [])
            break
    if not facts:
        print("backend returned no facts — check the prompt size", file=sys.stderr)

    verify(facts, sessions)

    out = args.out or _default_out(lore_root, args.wiki)
    _refuse_if_git_visible(out)

    # JSON first. It is the only artifact --check can reuse, and a failure
    # writing the report would otherwise discard a whole synthesis call.
    out.with_suffix(".json").write_text(json.dumps(facts, indent=2), encoding="utf-8")
    out.write_text(render(facts, sessions, args.wiki, engine), encoding="utf-8")

    multi = sum(1 for f in facts if len(f.get("verified_sessions", [])) > 1)
    print(f"\n{out}: {len(facts)} facts, {multi} multi-session (verified)")
    print(f"json: {out.with_suffix('.json')}")
    # A backend that answers in prose is converted to an empty result upstream,
    # so an empty run must not exit like a run that genuinely found nothing.
    return 0 if facts else 2


def self_check() -> int:
    """Asserts over the budgeting and rendering — the only non-obvious logic here."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "abcdef12-0000-0000-0000-000000000000.jsonl"
        rows = [
            {"type": "user", "message": {"role": "user", "content": "x" * 30_000}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "ENDING"},
                        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                    ],
                },
            },
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "content": "HUGE TOOL OUTPUT"}]}},
        ]
        p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

        s = load_session(p, budget=1000)
        assert s is not None
        assert s.session_id == "abcdef12", s.session_id
        assert s.truncated, "30k of text under a 1k budget must truncate"
        assert len(s.text) < 1400, f"budget overshoot: {len(s.text)}"
        assert s.text.endswith("ENDING"), "the ending must survive truncation"
        assert "HUGE TOOL OUTPUT" not in s.text, "tool results must be dropped"
        assert "ls" not in s.text, "tool calls must be dropped"
        assert s.turns == 2, s.turns

        empty = Path(td) / "empty.jsonl"
        empty.write_text('{"type":"summary"}\n', encoding="utf-8")
        assert load_session(empty) is None, "a transcript with no prose yields None"

    session_prose = [
        SessionText("aaaa", datetime(2026, 8, 1), "the worktrees need a symlink here", 1, False),
        SessionText("bbbb", datetime(2026, 8, 5), "the worktrees need a symlink too", 1, False),
        SessionText("cccc", datetime(2026, 8, 6), "nothing relevant at all in here", 1, False),
    ]
    facts = [
        # Real recurrence: the span is in both named sessions.
        {"claim": "M", "kind": "preference", "sessions": ["aaaa", "bbbb"],
         "quotes": ["worktrees need a symlink"], "why_durable": "w", "proposed_target": "t"},
        # Overclaimed: grounded in aaaa only, but cccc was named too.
        {"claim": "S", "kind": "gotcha", "sessions": ["aaaa", "cccc"],
         "quotes": ["nothing relevant"], "why_durable": "w", "proposed_target": "t"},
    ]
    verify(facts, session_prose)
    assert facts[0]["verified_sessions"] == ["aaaa", "bbbb"], facts[0]["verified_sessions"]
    assert facts[1]["verified_sessions"] == ["cccc"], facts[1]["verified_sessions"]

    # Unicode mangling by the model must not read as fabrication.
    uni = [{"claim": "U", "sessions": ["aaaa"],
            "quotes": ["the  worktrees\nneed a symlink"], "quotes_note": "reflowed"}]
    verify(uni, session_prose)
    assert "aaaa" in uni[0]["verified_sessions"], "whitespace must be folded, not fail the match"
    dash = [{"claim": "D", "sessions": ["aaaa"], "quotes": ["worktrees need a symlink"]}]
    verify(dash, [SessionText("aaaa", datetime(2026, 8, 1), "worktrees need a symlink", 1, False)])
    assert dash[0]["verified_sessions"] == ["aaaa"]

    # A quote the model shortened still has to confirm on its longest
    # surviving fragment — scoring it unfound would under-report recurrence.
    shortened = [{"claim": "E", "sessions": ["aaaa"],
               "quotes": ["the worktrees ... need a symlink here"]}]
    verify(shortened, session_prose)
    assert "aaaa" in shortened[0]["verified_sessions"], "shortened quotes must match on a fragment"

    # A query keeps identifiers and drops connective words, or search ANDs a
    # whole sentence together and matches nothing.
    q = search_terms("The ruff 0.16.0 release widened its default rule set and broke CI")
    assert "ruff" in q and "0.16.0" in q, q
    assert " the " not in f" {q} " and " and " not in f" {q} ", q
    assert len(q.split()) <= 8, q
    assert search_terms("a b c") == "", "short words carry no signal"

    burden = [
        {"vault_verdict": "known"},
        {"vault_verdict": "extends"},
        {"vault_verdict": "contradicts"},
        {"vault_verdict": "new"},
    ]
    text = " ".join(_burden_lines(burden))
    assert "3/4 survive deduplication" in text, text
    assert "1 need a human" in text, text
    assert _burden_lines([{"claim": "x"}]) == [], "no vault check means no burden lines"

    md = render(facts, session_prose, "lore")
    assert md.index("### M") < md.index("## Confirmed in one session") < md.index("### S"), \
        "facts confirmed in more than one session must sort above the rest"
    assert "1 multi-session, 1 single-session" in md
    assert "attribution overclaimed on 1/2 facts" in md, md[:400]
    assert "claimed but not found**: aaaa" in md, "ghost attribution must be named"

    print("self-check ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
