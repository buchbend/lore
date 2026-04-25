"""Curator C — adjacent-concept merge proposal pass (spec §6 step 1).

Scans the whole wiki for concept notes with substantial semantic overlap
and asks the LLM whether they should merge. Proposal-only in v1: writes
a new draft note with ``merge_candidate_sources: [[a]], [[b]]`` and
``draft: true``. Does NOT edit the originals.

Pre-filter: notes with shared tag(s) AND title-slug fuzzy ratio >= 0.6
(rapidfuzz if available, else stdlib difflib.SequenceMatcher).
Threshold: confidence >= 0.8 (inclusive) — below → skip.

Registers into ``curator_c._DEFRAG_PASSES`` so the integration skeleton
picks it up automatically.
"""

from __future__ import annotations

import difflib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lore_curator.c_passes import validate_llm_response


_MERGE_TOOL = {
    "name": "propose_merge",
    "description": "Judge whether two concept notes should merge and propose a merged version.",
    "input_schema": {
        "type": "object",
        "properties": {
            "should_merge": {"type": "boolean"},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
            "merged_title": {"type": "string"},
            "merged_description": {"type": "string"},
        },
        "required": ["should_merge", "confidence", "reason"],
    },
}

_FUZZ_THRESHOLD = 0.6
_CONFIDENCE_THRESHOLD = 0.8


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _parse_note(path: Path) -> tuple[dict, str] | None:
    """Return (frontmatter_dict, body_str) or None on parse failure."""
    try:
        from lore_core.schema import parse_frontmatter
        text = path.read_text(errors="replace")
        fm = parse_frontmatter(text)
        if fm is None:
            return None
        # Extract body: everything after the closing `---\n`.
        if text.startswith("---"):
            end = text.find("\n---", 3)
            body = text[end + 4:].lstrip("\n") if end != -1 else ""
        else:
            body = text
        return fm, body
    except Exception:
        return None


def generate_merge_candidates(wiki_path: Path) -> list[tuple[Path, Path]]:
    """Pre-filter candidate pairs by shared-tag + fuzzy-slug overlap.

    Testable independently of the LLM step.
    """
    notes: list[tuple[Path, dict, str]] = []
    for p in sorted(wiki_path.rglob("*.md")):
        if p.name.startswith("_"):
            continue
        parsed = _parse_note(p)
        if not parsed:
            continue
        fm, body = parsed
        if fm.get("type") not in ("concept", "decision"):
            continue
        notes.append((p, fm, body))

    pairs: list[tuple[Path, Path]] = []
    for i in range(len(notes)):
        pi, fmi, _ = notes[i]
        tags_i = set(fmi.get("tags") or [])
        slug_i = _slug(str(fmi.get("title") or pi.stem))
        for j in range(i + 1, len(notes)):
            pj, fmj, _ = notes[j]
            tags_j = set(fmj.get("tags") or [])
            # Tag-pre-filter rules:
            #   both empty → skip the tag check, rely on slug similarity
            #     (Curator B authors notes with tags: [] by default, so
            #     requiring shared tags would block the most common
            #     fragmentation source from ever reaching the LLM)
            #   one empty, one not → still skip. A hand-tagged canonical
            #     concept must not be auto-merged into a Curator-B-authored
            #     draft (always tags: []) without human review — the
            #     canonical note acts as a shield against drift. Some
            #     legitimate refinements get blocked here; those land on
            #     the user's manual triage path anyway.
            #   both tagged → require non-empty intersection (existing)
            if not tags_i and not tags_j:
                pass
            elif not (tags_i & tags_j):
                continue
            slug_j = _slug(str(fmj.get("title") or pj.stem))
            ratio = difflib.SequenceMatcher(None, slug_i, slug_j).ratio()
            if ratio >= _FUZZ_THRESHOLD:
                pairs.append((pi, pj))
    return pairs


def _propose_merge(
    note_a: Path, note_b: Path, *, anthropic_client: Any, lore_root: Path
) -> dict | None:
    """Call LLM and return a validated proposal dict, or None on any issue."""
    parsed_a = _parse_note(note_a)
    parsed_b = _parse_note(note_b)
    if not parsed_a or not parsed_b:
        return None
    fm_a, body_a = parsed_a
    fm_b, body_b = parsed_b

    prompt = (
        "Two concept notes from a knowledge vault. Judge whether they "
        "describe the same concept and should merge.\n\n"
        f"--- Note A: {note_a.name} ---\n"
        f"{body_a[:2000]}\n\n"
        f"--- Note B: {note_b.name} ---\n"
        f"{body_b[:2000]}\n"
    )

    from lore_curator.c_passes import resolve_tier_for_pass
    # Resolve actual model ID from wiki config; degrades to middle if high=off.
    model = resolve_tier_for_pass(
        note_a.parent.parent,  # wiki_path
        pass_name="adjacent_merge",
        preferred_tier="high",
        lore_root=lore_root,
    )

    try:
        resp = anthropic_client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
            tools=[_MERGE_TOOL],
            tool_choice={"type": "tool", "name": "propose_merge"},
        )
    except Exception:
        return None

    # Extract tool-use block input.
    block_input: dict | None = None
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "tool_use":
            block_input = getattr(block, "input", None)
            break

    return validate_llm_response(
        block_input,
        required={
            "should_merge": bool,
            "confidence": (int, float),
            "reason": str,
        },
        ranges={"confidence": (0.0, 1.0)},
        lore_root=lore_root,
        pass_name="adjacent_merge",
    )


def adjacent_merge_pass(
    wiki_path: Path, *, anthropic_client: Any, dry_run: bool
) -> dict[str, int]:
    """Registered in _DEFRAG_PASSES. Returns summary counts."""
    if anthropic_client is None:
        return {"adjacent_merge_skipped_no_llm": 1}

    from lore_core.config import get_lore_root
    try:
        lore_root = get_lore_root()
    except Exception:
        return {"adjacent_merge_skipped_no_lore_root": 1}

    pairs = generate_merge_candidates(wiki_path)
    proposals_written = 0
    skipped_low_confidence = 0
    skipped_malformed = 0

    sessions_dir = wiki_path / "sessions"
    sessions_dir.mkdir(exist_ok=True)

    for note_a, note_b in pairs:
        proposal = _propose_merge(
            note_a, note_b, anthropic_client=anthropic_client, lore_root=lore_root
        )
        if proposal is None:
            skipped_malformed += 1
            continue
        if not proposal.get("should_merge"):
            continue
        if proposal.get("confidence", 0.0) < _CONFIDENCE_THRESHOLD:
            skipped_low_confidence += 1
            continue

        if dry_run:
            proposals_written += 1
            continue

        # Write a new draft note. Filename: merge-<run_id_short>-<slug>.md
        today = datetime.now(UTC).date().isoformat()
        title = proposal.get("merged_title") or f"merge of {note_a.stem} + {note_b.stem}"
        slug = _slug(title)[:40] or "merge"
        dest = sessions_dir / f"{today}-{slug}-merge.md"
        # Idempotence: if a draft with these exact sources already exists, skip.
        if _existing_proposal_has_sources(sessions_dir, note_a, note_b):
            continue
        # Resolve collision by appending short hash.
        if dest.exists():
            import hashlib
            h = hashlib.sha256(f"{note_a}{note_b}".encode()).hexdigest()[:6]
            dest = sessions_dir / f"{today}-{slug}-merge-{h}.md"

        frontmatter = (
            "---\n"
            f"type: concept\n"
            f"draft: true\n"
            f"created: {today}\n"
            f"last_reviewed: {today}\n"
            f"description: {proposal.get('merged_description') or title!r}\n"
            f"tags: []\n"
            f"merge_candidate_sources:\n"
            f"  - [[{note_a.stem}]]\n"
            f"  - [[{note_b.stem}]]\n"
            "---\n\n"
        )
        body = (
            f"# {title}\n\n"
            f"_Proposed merge of [[{note_a.stem}]] and [[{note_b.stem}]]._\n\n"
            f"Confidence: {proposal.get('confidence'):.2f}\n"
            f"Reason: {proposal.get('reason')}\n"
        )
        try:
            dest.write_text(frontmatter + body)
        except OSError:
            return {
                "adjacent_merge_disk_error": 1,
                "adjacent_merge_proposed": proposals_written,
            }
        proposals_written += 1

    return {
        "adjacent_merge_proposed": proposals_written,
        "adjacent_merge_skipped_low_confidence": skipped_low_confidence,
        "adjacent_merge_skipped_malformed": skipped_malformed,
    }


def _existing_proposal_has_sources(sessions_dir: Path, a: Path, b: Path) -> bool:
    """True if any existing draft already proposes the same merge pair."""
    a_link = f"[[{a.stem}]]"
    b_link = f"[[{b.stem}]]"
    for p in sessions_dir.glob("*-merge*.md"):
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        if "merge_candidate_sources" in text and a_link in text and b_link in text:
            return True
    return False


def _register() -> None:
    """Append to curator_c._DEFRAG_PASSES."""
    from lore_curator import curator_c
    if adjacent_merge_pass not in curator_c._DEFRAG_PASSES:
        curator_c._DEFRAG_PASSES.append(adjacent_merge_pass)


_register()
