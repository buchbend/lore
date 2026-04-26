"""Curator C — auto-supersession proposal pass (spec §6 step 2).

Scans decision notes for pairs where a newer decision appears to
contradict an older one in an overlapping scope, asks the LLM for a
contradiction verdict, and writes **proposal markers** to both notes:

- older gets ``supersede_candidate: [[newer]]``
- newer gets ``supersede_candidate_of: [[older]]``

Proposal-only per plan review (architect must-fix): does NOT flip the
real ``superseded_by`` field. User promotes manually once they've
reviewed the markers.

Conservative:
- ``canonical: true`` on the older note → skip (opt-out)
- confidence >= 0.85 required (inclusive)
- Circular-supersession guard: if newer already supersedes (explicit
  or proposed) the older via another chain, skip
"""

from __future__ import annotations

from datetime import datetime, date as _date
from pathlib import Path
from typing import Any

from lore_curator.c_passes import validate_llm_response
from lore_curator.c_adjacent_merge import _parse_note


_SUPERSEDE_TOOL = {
    "name": "judge_supersession",
    "description": "Judge whether a newer decision contradicts an older one in overlapping scope.",
    "input_schema": {
        "type": "object",
        "properties": {
            "contradicts": {"type": "boolean"},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["contradicts", "confidence", "reason"],
    },
}

_CONFIDENCE_THRESHOLD = 0.85


def _to_date(value) -> _date | None:
    if isinstance(value, _date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return _date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _scope_overlap(fm_a: dict, fm_b: dict) -> bool:
    """True if A and B overlap in scope/tags enough to be comparable decisions.

    v1 heuristic: share at least one tag OR both have empty tags (vault-wide).
    """
    ta = set(fm_a.get("tags") or [])
    tb = set(fm_b.get("tags") or [])
    if not ta and not tb:
        return True
    return bool(ta & tb)


def generate_supersede_candidates(wiki_path: Path) -> list[tuple[Path, Path]]:
    """Return (older, newer) pairs eligible for supersession check."""
    decisions: list[tuple[Path, dict, _date]] = []
    for p in sorted(wiki_path.rglob("*.md")):
        if p.name.startswith("_"):
            continue
        parsed = _parse_note(p)
        if not parsed:
            continue
        fm, _ = parsed
        if fm.get("type") != "decision":
            continue
        created = _to_date(fm.get("created"))
        if created is None:
            continue
        decisions.append((p, fm, created))

    decisions.sort(key=lambda x: x[2])  # oldest first
    pairs: list[tuple[Path, Path]] = []
    for i in range(len(decisions)):
        older_p, older_fm, older_d = decisions[i]
        for j in range(i + 1, len(decisions)):
            newer_p, newer_fm, newer_d = decisions[j]
            if newer_d <= older_d:
                continue
            if not _scope_overlap(older_fm, newer_fm):
                continue
            pairs.append((older_p, newer_p))
    return pairs


def _has_explicit_supersession_chain(older: Path, newer: Path) -> bool:
    """True if older already supersedes newer (explicit or candidate)."""
    parsed = _parse_note(older)
    if not parsed:
        return False
    fm, _ = parsed
    for key in ("supersedes", "supersede_candidate_of"):
        val = fm.get(key)
        if not val:
            continue
        # Accept string `[[slug]]` or list thereof.
        items = val if isinstance(val, list) else [val]
        for item in items:
            if isinstance(item, str) and newer.stem in item:
                return True
    return False


def _has_marker(note: Path, key: str, target: Path) -> bool:
    parsed = _parse_note(note)
    if not parsed:
        return False
    fm, _ = parsed
    val = fm.get(key)
    if not val:
        return False
    items = val if isinstance(val, list) else [val]
    return any(isinstance(i, str) and target.stem in i for i in items)


def _append_marker(path: Path, key: str, target_stem: str) -> None:
    """Append a `key: [[target_stem]]` entry into the frontmatter. Safe-edit."""
    text = path.read_text(errors="replace")
    if not text.startswith("---"):
        return
    end = text.find("\n---", 3)
    if end == -1:
        return
    fm_block = text[3:end + 1]
    body = text[end + 4:]
    link = f'"[[{target_stem}]]"'

    # If the key already exists in the frontmatter, append to its list form.
    lines = fm_block.splitlines()
    updated: list[str] = []
    key_found = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith(f"{key}:"):
            key_found = True
            # Existing value style: either inline (`key: [[x]]`) or list form.
            after = stripped[len(key) + 1:].strip()
            if after.startswith("["):  # YAML flow list — skip handling, inline add
                # Append as new list item under the key header.
                updated.append(f"{key}:")
                # Convert inline flow to block form (conservative).
                try:
                    import yaml as _yaml
                    parsed_list = _yaml.safe_load(after) or []
                    if not isinstance(parsed_list, list):
                        parsed_list = [parsed_list]
                except Exception:
                    parsed_list = []
                for item in parsed_list:
                    updated.append(f"  - {item}")
                updated.append(f"  - {link}")
            elif after.startswith("[["):
                # Scalar `[[x]]` form → convert to list.
                updated.append(f"{key}:")
                updated.append(f"  - {after}")
                updated.append(f"  - {link}")
            elif after == "":
                # Already a block list; copy the existing items then append.
                updated.append(line)
                i += 1
                while i < len(lines) and (lines[i].startswith("  -") or lines[i].strip() == ""):
                    updated.append(lines[i])
                    i += 1
                updated.append(f"  - {link}")
                continue
            else:
                # Unknown shape — skip editing to avoid corruption.
                updated.append(line)
        else:
            updated.append(line)
        i += 1

    if not key_found:
        # Append at end of frontmatter block.
        updated.append(f"{key}:")
        updated.append(f"  - {link}")

    new_fm = "\n".join(updated)
    new_text = f"---{new_fm}\n---{body}"
    path.write_text(new_text)


def auto_supersede_pass(
    wiki_path: Path, *, llm_client: Any, dry_run: bool
) -> dict[str, int]:
    if llm_client is None:
        return {"auto_supersede_skipped_no_llm": 1}

    from lore_core.config import get_lore_root
    try:
        lore_root = get_lore_root()
    except Exception:
        return {"auto_supersede_skipped_no_lore_root": 1}

    pairs = generate_supersede_candidates(wiki_path)
    proposed = 0
    skipped_low_confidence = 0
    skipped_canonical = 0
    skipped_malformed = 0
    skipped_circular = 0

    for older, newer in pairs:
        older_parsed = _parse_note(older)
        if not older_parsed:
            continue
        older_fm, _ = older_parsed
        if older_fm.get("canonical") is True:
            skipped_canonical += 1
            continue

        # Circular guard: older already supersedes newer → skip.
        if _has_explicit_supersession_chain(older, newer):
            skipped_circular += 1
            continue

        # Idempotence: if marker already present → skip.
        if _has_marker(older, "supersede_candidate", newer):
            continue

        # LLM verdict.
        prompt = (
            f"Two decision notes. Judge whether the newer one contradicts "
            f"the older one on an overlapping topic (i.e. the newer "
            f"supersedes the older).\n\n"
            f"--- OLDER: {older.name} ---\n{older.read_text(errors='replace')[:1500]}\n\n"
            f"--- NEWER: {newer.name} ---\n{newer.read_text(errors='replace')[:1500]}\n"
        )
        from lore_curator.c_passes import resolve_tier_for_pass
        model = resolve_tier_for_pass(
            wiki_path, pass_name="auto_supersede", preferred_tier="high",
            lore_root=lore_root,
        )
        try:
            resp = llm_client.messages.create(
                model=model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
                tools=[_SUPERSEDE_TOOL],
                tool_choice={"type": "tool", "name": "judge_supersession"},
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
            required={
                "contradicts": bool,
                "confidence": (int, float),
                "reason": str,
            },
            ranges={"confidence": (0.0, 1.0)},
            lore_root=lore_root,
            pass_name="auto_supersede",
        )
        if verdict is None:
            skipped_malformed += 1
            continue
        if not verdict.get("contradicts"):
            continue
        if verdict.get("confidence", 0.0) < _CONFIDENCE_THRESHOLD:
            skipped_low_confidence += 1
            continue

        if dry_run:
            proposed += 1
            continue

        # Write proposal markers on both notes.
        try:
            _append_marker(older, "supersede_candidate", newer.stem)
            _append_marker(newer, "supersede_candidate_of", older.stem)
        except OSError:
            return {
                "auto_supersede_disk_error": 1,
                "auto_supersede_proposed": proposed,
            }
        proposed += 1

    return {
        "auto_supersede_proposed": proposed,
        "auto_supersede_skipped_low_confidence": skipped_low_confidence,
        "auto_supersede_skipped_canonical": skipped_canonical,
        "auto_supersede_skipped_malformed": skipped_malformed,
        "auto_supersede_skipped_circular": skipped_circular,
    }


def _register() -> None:
    from lore_curator import defrag_curator
    if auto_supersede_pass not in defrag_curator._DEFRAG_PASSES:
        defrag_curator._DEFRAG_PASSES.append(auto_supersede_pass)


_register()
