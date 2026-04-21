"""Curator C — orphan wikilink repair pass (spec §6 step 3).

Scans all notes for ``[[wikilinks]]`` whose target slug doesn't exist
in the wiki. For each orphan, fuzzy-matches against existing note
slugs; if a unique candidate scores high enough, asks the LLM to
confirm. On confirmation with confidence >= 0.8:

  - With ``curator.curator_c.defrag_body_writes: true``:
    rewrites the link in place (body mutation — highest blast radius
    in Plan 5; gated separately from the main --defrag flag).

  - Default (``defrag_body_writes: false``):
    logs the proposed rewrite to
    ``$LORE_ROOT/.lore/curator-c.body-proposals.YYYY-MM-DD.log`` for
    user review. Body bytes are not touched.

Ambiguous matches (two candidates at similar ratio) → flag only, no
rewrite even with sub-flag on. Truly deleted targets (no fuzzy match
> 0.7) → flag only.
"""

from __future__ import annotations

import difflib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lore_curator.c_passes import validate_llm_response


_ORPHAN_TOOL = {
    "name": "confirm_rename",
    "description": "Confirm whether an orphaned wikilink is a rename of an existing note.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_rename": {"type": "boolean"},
            "canonical_slug": {"type": "string"},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["is_rename", "confidence"],
    },
}

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(\|([^\]]+))?\]\]")
_FUZZ_THRESHOLD = 0.7
_CONFIDENCE_THRESHOLD = 0.8


def _existing_slugs(wiki_path: Path) -> set[str]:
    """Return the set of note slugs (filename stems) in the wiki."""
    slugs = set()
    for p in wiki_path.rglob("*.md"):
        if p.name.startswith("_"):
            continue
        slugs.add(p.stem)
    return slugs


def find_orphan_links(wiki_path: Path) -> list[tuple[Path, str, int]]:
    """Return list of (note_path, orphan_slug, match_start_offset) for every
    wikilink in the wiki whose target doesn't resolve.

    Testable independently. Each occurrence is listed once — a note with
    three uses of the same orphan returns three entries.
    """
    slugs = _existing_slugs(wiki_path)
    results: list[tuple[Path, str, int]] = []
    for p in sorted(wiki_path.rglob("*.md")):
        if p.name.startswith("_"):
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        for m in _WIKILINK_RE.finditer(text):
            slug = m.group(1).strip()
            if slug and slug not in slugs:
                results.append((p, slug, m.start()))
    return results


def _best_fuzzy_match(orphan: str, candidates: set[str]) -> tuple[str | None, bool]:
    """Return (best_match, ambiguous). ambiguous=True when two candidates
    are within 0.05 of each other at the top.
    """
    if not candidates:
        return None, False
    scored = [
        (difflib.SequenceMatcher(None, orphan, c).ratio(), c)
        for c in candidates
    ]
    scored.sort(reverse=True)
    top_ratio, top_slug = scored[0]
    if top_ratio < _FUZZ_THRESHOLD:
        return None, False
    if len(scored) > 1 and scored[1][0] >= top_ratio - 0.05:
        return top_slug, True
    return top_slug, False


def _write_body_proposal_log(
    lore_root: Path, entries: list[dict]
) -> None:
    if not entries:
        return
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    log = lore_root / ".lore" / f"curator-c.body-proposals.{today}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log.open("a") as f:
            for e in entries:
                f.write(
                    f"{e.get('ts')} file={e.get('file')} "
                    f"orphan={e.get('orphan')} "
                    f"candidate={e.get('candidate')} "
                    f"status={e.get('status')}\n"
                )
    except OSError:
        pass


def _rewrite_orphan_in_file(path: Path, orphan: str, canonical: str) -> int:
    """Rewrite every ``[[orphan]]`` and ``[[orphan|display]]`` in file to
    ``[[canonical]]`` / ``[[canonical|display]]``. Returns count.

    Preserves surrounding whitespace and CRLF by doing single-token replace.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return 0
    text = raw.decode("utf-8", errors="replace")

    def _replace(match: re.Match[str]) -> str:
        target = match.group(1).strip()
        display = match.group(3)
        if target != orphan:
            return match.group(0)
        if display:
            return f"[[{canonical}|{display}]]"
        return f"[[{canonical}]]"

    new_text, count = _WIKILINK_RE.subn(_replace, text)
    if count == 0:
        return 0
    new_bytes = new_text.encode("utf-8")
    # Preserve trailing-newline sentinel exactly.
    path.write_bytes(new_bytes)
    return count


def orphan_links_pass(
    wiki_path: Path, *, anthropic_client: Any, dry_run: bool
) -> dict[str, int]:
    if anthropic_client is None:
        return {"orphan_skipped_no_llm": 1}

    from lore_core.config import get_lore_root
    from lore_core.wiki_config import load_wiki_config
    try:
        lore_root = get_lore_root()
    except Exception:
        return {"orphan_skipped_no_lore_root": 1}

    cfg = load_wiki_config(wiki_path)
    body_writes_enabled = cfg.curator.curator_c.defrag_body_writes

    slugs = _existing_slugs(wiki_path)
    orphans = find_orphan_links(wiki_path)
    # Collapse by (file, orphan) so repeated uses cause one LLM call each.
    seen: set[tuple[Path, str]] = set()
    unique = [(f, s) for f, s, _ in orphans if (f, s) not in seen and not seen.add((f, s))]

    rewritten = 0
    flagged = 0
    ambiguous = 0
    skipped_malformed = 0
    skipped_low_confidence = 0
    skipped_no_candidate = 0

    proposals: list[dict] = []
    now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    for note, orphan in unique:
        best, is_ambiguous = _best_fuzzy_match(orphan, slugs)
        if best is None:
            flagged += 1
            skipped_no_candidate += 1
            proposals.append({
                "ts": now_iso, "file": str(note.relative_to(wiki_path)),
                "orphan": orphan, "candidate": None, "status": "no-candidate",
            })
            continue
        if is_ambiguous:
            ambiguous += 1
            proposals.append({
                "ts": now_iso, "file": str(note.relative_to(wiki_path)),
                "orphan": orphan, "candidate": best, "status": "ambiguous",
            })
            continue

        prompt = (
            f"A note contains [[{orphan}]] which doesn't resolve. "
            f"Closest existing slug: {best!r}. "
            f"Is this a rename (same concept, slug drifted)?\n"
        )
        try:
            resp = anthropic_client.messages.create(
                model="middle",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
                tools=[_ORPHAN_TOOL],
                tool_choice={"type": "tool", "name": "confirm_rename"},
            )
        except Exception:
            skipped_malformed += 1
            continue

        block_input = None
        for block in getattr(resp, "content", []) or []:
            if getattr(block, "type", None) == "tool_use":
                block_input = getattr(block, "input", None)
                break

        verdict = validate_llm_response(
            block_input,
            required={"is_rename": bool, "confidence": (int, float)},
            ranges={"confidence": (0.0, 1.0)},
            lore_root=lore_root,
            pass_name="orphan_links",
        )
        if verdict is None:
            skipped_malformed += 1
            continue
        if not verdict.get("is_rename"):
            proposals.append({
                "ts": now_iso, "file": str(note.relative_to(wiki_path)),
                "orphan": orphan, "candidate": best, "status": "not-a-rename",
            })
            continue
        if verdict.get("confidence", 0.0) < _CONFIDENCE_THRESHOLD:
            skipped_low_confidence += 1
            continue

        if dry_run or not body_writes_enabled:
            proposals.append({
                "ts": now_iso, "file": str(note.relative_to(wiki_path)),
                "orphan": orphan, "candidate": best,
                "status": "proposed" if not body_writes_enabled else "proposed-dry-run",
            })
            if not body_writes_enabled:
                flagged += 1
            continue

        count = _rewrite_orphan_in_file(note, orphan, best)
        rewritten += count
        proposals.append({
            "ts": now_iso, "file": str(note.relative_to(wiki_path)),
            "orphan": orphan, "candidate": best, "status": f"rewritten:{count}",
        })

    # Always persist proposal log (both modes).
    _write_body_proposal_log(lore_root, proposals)

    return {
        "orphan_rewritten": rewritten,
        "orphan_flagged": flagged,
        "orphan_ambiguous": ambiguous,
        "orphan_skipped_malformed": skipped_malformed,
        "orphan_skipped_low_confidence": skipped_low_confidence,
        "orphan_skipped_no_candidate": skipped_no_candidate,
    }


def _register() -> None:
    from lore_curator import curator_c
    if orphan_links_pass not in curator_c._DEFRAG_PASSES:
        curator_c._DEFRAG_PASSES.append(orphan_links_pass)


_register()
