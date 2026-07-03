"""Stale-marker writer — slice 5 of PRD #65.

Writes ``status: stale``, ``stale_reason``, ``stale_by``, ``stale_at``
to a target note's frontmatter. Strictly **additive-only** per the
vault-wide edit policy (#37): never deletes user-set fields, never
overwrites pre-existing values without an explicit ``clear_stale``
call.

Body bytes are never touched. The writer operates on the YAML
frontmatter block as text, leaves the body alone, and preserves the
trailing newline.
"""

from __future__ import annotations

import re
from datetime import date as _date
from pathlib import Path


_STALE_FIELDS = ("status", "stale_reason", "stale_by", "stale_at")
_KEY_LINE_RE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*:")


class StaleMarkerError(ValueError):
    """Raised when a write would violate the additive-only contract."""


def _split_frontmatter(text: str) -> tuple[str, str] | None:
    """Return ``(fm_block, body)`` or ``None`` if no frontmatter.

    ``fm_block`` is the inner YAML text *between* the fences (no
    delimiters), ``body`` is everything after the closing ``---``.
    """
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    fm_inner = text[3:end + 1].lstrip("\n")
    body = text[end + 4:]
    if body.startswith("\n"):
        body = body[1:]
    return fm_inner, body


def _scan_keys(fm_block: str) -> dict[str, int]:
    """Return ``{top-level-key: line-index}`` for the FM block.

    Lines that aren't ``key: value`` shape (continuations, comments)
    are skipped. Indentation > 0 is treated as a continuation.
    """
    keys: dict[str, int] = {}
    for i, line in enumerate(fm_block.splitlines()):
        if not line or line[0] in " \t#-":
            continue
        m = _KEY_LINE_RE.match(line)
        if m:
            keys.setdefault(m.group("key"), i)
    return keys


def _format_value(key: str, value: object) -> str:
    """Render ``value`` as a YAML scalar suitable for one-line append."""
    if isinstance(value, _date):
        return value.isoformat()
    s = str(value)
    if "\n" in s:
        s = s.replace("\n", " ")
    if any(ch in s for ch in [":", "#", "[", "]", "{", "}", ","]):
        s = s.replace('"', '\\"')
        return f'"{s}"'
    return s


def _additively_set(fm_block: str, key: str, value: object) -> str:
    """Append ``key: value`` only if ``key`` is absent.

    Raises :class:`StaleMarkerError` when the key already exists with
    a different value (additive-only). Idempotent when the existing
    line matches verbatim.
    """
    rendered = f"{key}: {_format_value(key, value)}"
    keys = _scan_keys(fm_block)
    if key in keys:
        lines = fm_block.splitlines()
        existing = lines[keys[key]].strip()
        if existing == rendered:
            return fm_block  # idempotent
        # Re-check loose equality on the value side for status:stale
        # variants like ``status: stale`` already present.
        if existing.startswith(f"{key}:"):
            cur = existing[len(key) + 1:].strip().strip('"').strip("'")
            new = _format_value(key, value).strip('"').strip("'")
            if cur == new:
                return fm_block
        raise StaleMarkerError(
            f"refusing to overwrite existing field {key!r} "
            f"(current={existing!r}, new={rendered!r}); call "
            f"clear_stale() first."
        )
    if fm_block and not fm_block.endswith("\n"):
        fm_block = fm_block + "\n"
    return fm_block + rendered + "\n"


def _drop_keys(fm_block: str, keys_to_drop: set[str]) -> str:
    """Return ``fm_block`` with the given top-level keys removed.

    Removes the ``key: value`` line *and* any indented continuation
    lines (block-list items, multi-line scalars).
    """
    lines = fm_block.splitlines()
    out: list[str] = []
    skip = False
    for line in lines:
        if skip:
            if line and (line[0] in " \t" or line.startswith("- ")):
                continue
            skip = False
        m = _KEY_LINE_RE.match(line)
        if m and m.group("key") in keys_to_drop:
            skip = True
            continue
        out.append(line)
    result = "\n".join(out)
    if fm_block.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


def mark_stale(
    note_path: Path,
    reason: str,
    handle: str,
    today: _date | None = None,
) -> None:
    """Write the four-field stale verdict additively.

    ``status: stale``, ``stale_reason: <reason>``, ``stale_by: <handle>``,
    ``stale_at: <today>``. Raises :class:`StaleMarkerError` if any of
    the four fields already exists with a different value (use
    :func:`clear_stale` first to remove a prior verdict — additive-
    only contract).

    Idempotent: writing the same verdict twice is a no-op.

    Body bytes are never touched. ``last_reviewed``, ``description``,
    ``tags`` and any user-authored fields are preserved verbatim.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise StaleMarkerError("reason is required and must be non-empty")
    today = today or _date.today()
    text = note_path.read_text(errors="replace")
    split = _split_frontmatter(text)
    if split is None:
        raise StaleMarkerError(
            f"note has no YAML frontmatter: {note_path}"
        )
    fm_block, body = split

    fm_block = _additively_set(fm_block, "status", "stale")
    fm_block = _additively_set(fm_block, "stale_reason", reason.strip())
    fm_block = _additively_set(fm_block, "stale_by", handle)
    fm_block = _additively_set(fm_block, "stale_at", today)

    new_text = "---\n" + fm_block + "---\n" + body
    note_path.write_text(new_text)


def clear_stale(note_path: Path) -> None:
    """Remove only the four stale fields. Other frontmatter is untouched.

    No-op when none of the four fields are present (still safe to
    call). Body bytes are never touched.
    """
    text = note_path.read_text(errors="replace")
    split = _split_frontmatter(text)
    if split is None:
        return
    fm_block, body = split
    new_fm = _drop_keys(fm_block, set(_STALE_FIELDS))
    if new_fm == fm_block:
        return
    new_text = "---\n" + new_fm + "---\n" + body
    note_path.write_text(new_text)
