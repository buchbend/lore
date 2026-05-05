"""Single source of truth for the canonical step heading + ID format.

The canonical form is ``### step-<N>: <title>`` — readable, anchorable, and
the same shape on disk, in wikilinks (``[[plan/slug#step-3]]``), and in
commit trailers (``Plan: slug#step-3``).

Three modules previously held their own copy of this regex (parser,
writer, registry); centralizing here lets us evolve the canonical shape
in exactly one place. Importers MUST NOT redefine the regex inline.

**Transition compatibility.** Plans created before the rename use the
legacy ``### s<N>:`` heading and ``s<N>`` step ID. Reading is permissive
(accepts both); writing is strict (always emits canonical). The
``lore plan migrate-ids`` command rewrites legacy plans wholesale; a
re-capture of any individual plan also migrates it piecemeal. The
trailer regex in the ExitPlanMode hook accepts both forms forever so
historical commit trailers stay actionable.
"""
from __future__ import annotations

import re

from .types import PlanStep

#: Strict canonical heading line — used for emission and validation.
#: ``re.IGNORECASE`` accepts ``Step-1`` and ``STEP-1`` on read; rendered
#: output always lowercases.
CANONICAL_STEP_RE = re.compile(r"^###\s+step-(\d+)\b", re.IGNORECASE)

#: Legacy ``### s<N>:`` heading shape. Read-only — never emitted by the
#: writer. Drop in a future release after telemetry shows no remaining
#: legacy plans in active vaults.
LEGACY_STEP_HEADING_RE = re.compile(r"^###\s+s(\d+)\b", re.IGNORECASE)

# Step-ID-shape regexes are module-private — callers go through
# :func:`is_legacy_step_id`, :func:`canonicalize_step_id`, and
# :func:`parse_step_id_ordinal` instead of importing the regexes.
_LEGACY_STEP_ID_RE = re.compile(r"^s(\d+)$", re.IGNORECASE)
_CANONICAL_STEP_ID_RE = re.compile(r"^step-(\d+)$", re.IGNORECASE)


def step_id_for(n: int) -> str:
    """Return the canonical step ID for ordinal ``n`` (1-indexed)."""
    if n < 1:
        raise ValueError(f"step ordinal must be >= 1, got {n}")
    return f"step-{n}"


def parse_step_id_ordinal(step_id: str) -> int | None:
    """Extract the ordinal ``N`` from a step ID, accepting both forms.

    Returns ``None`` for inputs that aren't recognizable step IDs.
    """
    if not step_id:
        return None
    m = _CANONICAL_STEP_ID_RE.match(step_id) or _LEGACY_STEP_ID_RE.match(step_id)
    return int(m.group(1)) if m else None


def is_legacy_step_id(step_id: str) -> bool:
    """True if ``step_id`` is the legacy ``s<N>`` form (not canonical)."""
    return bool(_LEGACY_STEP_ID_RE.match(step_id))


def canonicalize_step_id(step_id: str) -> str:
    """Convert legacy ``s<N>`` → canonical ``step-<N>``. Idempotent.

    Returns the input unchanged for already-canonical IDs and for
    unrecognized values (caller decides validation policy).
    """
    m = _LEGACY_STEP_ID_RE.match(step_id)
    if m:
        return f"step-{m.group(1)}"
    return step_id


def format_canonical_heading(step: PlanStep) -> str:
    """Render the canonical heading line for a step.

    Returns ``### step-<N>: <title>``. The step's ``id`` is canonicalized
    in case a caller passed a legacy form (``s1`` → ``step-1``). If
    ``step.title`` is empty, falls back to the step ID so a heading is
    always present.

    Raises ``ValueError`` if the step ID does not match either the
    canonical (``step-<N>``) or legacy (``s<N>``) shape — emitting an
    invalid heading would silently break downstream readers
    (registry, breadcrumbs) that match against
    :data:`CANONICAL_STEP_RE`.
    """
    sid = canonicalize_step_id(step.id)
    if not _CANONICAL_STEP_ID_RE.match(sid):
        raise ValueError(
            f"refusing to render heading for non-step ID {step.id!r}: "
            f"must match step-<N> or legacy s<N>"
        )
    title = step.title.strip() if step.title else sid
    return f"### {sid}: {title}"


def extract_step_ids_verbatim(body: str) -> list[str]:
    """Return ordered step IDs found in a plan body, **verbatim**.

    Permissive: accepts both canonical (``### step-<N>:``) and legacy
    (``### s<N>:``) heading shapes. IDs are returned exactly as they
    appear in the file (``step-1`` from canonical headings, ``s1``
    from legacy ones).

    Verbatim is the right default when comparing against the
    frontmatter ``step_status`` dict — its keys are also verbatim
    until the plan is migrated, so verbatim IDs match verbatim keys.
    Use :func:`extract_canonical_step_ids` when you want normalized
    output regardless of on-disk shape.
    """
    ids: list[str] = []
    for line in body.split("\n"):
        m = CANONICAL_STEP_RE.match(line)
        if m:
            ids.append(f"step-{m.group(1)}")
            continue
        m = LEGACY_STEP_HEADING_RE.match(line)
        if m:
            ids.append(f"s{m.group(1)}")
    return ids


# Back-compat alias — internal callers may still use the original name.
# Prefer ``extract_step_ids_verbatim`` in new code; the explicit suffix
# disambiguates from ``extract_canonical_step_ids``.
extract_step_ids = extract_step_ids_verbatim


def extract_canonical_step_ids(body: str) -> list[str]:
    """Return ordered step IDs canonicalized to ``step-<N>``.

    Use this when you specifically want canonical form regardless of
    what the file looks like (e.g. for matching against a fresh
    ``step_status`` dict that has been migrated to canonical keys).
    """
    return [canonicalize_step_id(sid) for sid in extract_step_ids_verbatim(body)]


#: Start-of-line ``Files:`` directive (case-insensitive). Anchored so prose
#: mentions like *"the Files: section"* don't false-positive. Up to a few
#: leading spaces tolerated for nested-markdown contexts. Optional
#: backticks before the keyword and after the colon tolerate the markdown
#: idiom of rendering the directive as inline code, e.g. ``` `Files:` ```.
_STEP_FILES_HEADING_RE = re.compile(
    r"^[ \t]{0,4}`?files\s*:`?\s*(?P<inline>.*?)\s*$",
    re.IGNORECASE,
)

#: Bulleted continuation under a ``Files:`` block: ``- path`` or ``* path``.
#: Captures the path content (backticks stripped by the caller).
_STEP_FILES_BULLET_RE = re.compile(
    r"^[ \t]{0,4}[-*+]\s+(?P<path>.+?)\s*$"
)


def _strip_path_decoration(raw: str) -> str:
    """Strip surrounding backticks/whitespace from a single path token."""
    s = raw.strip()
    if s.startswith("`") and s.endswith("`") and len(s) >= 2:
        s = s[1:-1].strip()
    return s


#: Backticked tokens inside a bullet body (annotation style).
_BACKTICK_PATH_RE = re.compile(r"`([^`\n]+)`")

#: Bullet annotation separators in priority order. The plan-authoring
#: LLM frequently writes ``- `path` — explanation``; we slice on the
#: separator and keep the LHS.
_BULLET_ANNOTATION_SEPS = (" — ", " -- ")


def _extract_paths_from_token(content: str) -> list[str]:
    """Pull file paths out of one inline-or-bullet token.

    Handles four real-world shapes seen in plan bodies:

    * ``path`` (bare)
    * ```path``` (markdown-quoted)
    * ```path` — annotation`` (annotated bullet)
    * ```a`, `b` — annotation`` (multiple paths in one bullet)

    Backticked tokens take precedence — when any are present, they
    define the path set and surrounding prose is ignored. Otherwise we
    slice on the em-dash annotation separator and split the LHS on
    commas.
    """
    s = content.strip()
    if not s:
        return []
    # Slice on the em-dash annotation separator first so backticked
    # identifiers in the explanation (`_helper`, `MyClass`) don't get
    # mistaken for paths.
    for sep in _BULLET_ANNOTATION_SEPS:
        if sep in s:
            s = s.split(sep, 1)[0].strip()
            break
    if not s:
        return []
    backticked = _BACKTICK_PATH_RE.findall(s)
    if backticked:
        return [p.strip() for p in backticked if p.strip()]
    return [p.strip() for p in s.split(",") if p.strip()]


def extract_step_files(body: str) -> list[str]:
    """Return the ordered list of file paths declared in a step body.

    Supports two shapes:

    * **Inline comma list** — ``Files: lib/foo.py, lib/bar.py`` on a
      single line. Comma-separated values are stripped of whitespace
      and surrounding backticks.
    * **Bulleted list** — a bare ``Files:`` line followed by ``- path``
      or ``* path`` bullets, terminated by a blank line or non-bullet
      content. Same backtick stripping applies; bullets may carry an
      em-dash annotation (``- `lib/foo.py` — explanation``)
      whose RHS is ignored.

    The ``Files:`` heading must be at start-of-line (after optional
    indent) — prose mentions of *"the Files: section"* do not match.
    The directive itself may be wrapped in backticks (```Files:```)
    to render it as inline code.

    Returns an empty list when no ``Files:`` directive is found.
    """
    if not body:
        return []
    lines = body.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = _STEP_FILES_HEADING_RE.match(lines[i])
        if m is None:
            i += 1
            continue
        inline = m.group("inline")
        if inline:
            # Inline form — annotation-aware extraction handles
            # `Files: a, b` and `Files: \`a\` — annotation` alike.
            out.extend(_extract_paths_from_token(inline))
            return out
        # Bulleted form — consume contiguous bullet lines until blank or
        # non-bullet content.
        i += 1
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                break
            mb = _STEP_FILES_BULLET_RE.match(line)
            if mb is None:
                break
            out.extend(_extract_paths_from_token(mb.group("path")))
            i += 1
        return out
    return out


def migrate_legacy_body(body: str) -> tuple[str, int]:
    """Rewrite legacy ``### s<N>:`` headings to canonical ``### step-<N>:``.

    Returns ``(new_body, count)`` where ``count`` is the number of
    headings rewritten. ``count == 0`` means the body was already
    canonical and the returned string is byte-identical to the input —
    callers can short-circuit a write to preserve mtime.
    """
    lines = body.split("\n")
    rewrites = 0
    for i, line in enumerate(lines):
        m = LEGACY_STEP_HEADING_RE.match(line)
        if m is None:
            continue
        n = int(m.group(1))
        # Reconstruct the line with canonical heading + everything that
        # followed the legacy match (e.g. ``: title`` or ` [removed]`).
        suffix = line[m.end():]
        lines[i] = f"### step-{n}{suffix}"
        rewrites += 1
    if rewrites == 0:
        return body, 0
    return "\n".join(lines), rewrites


def migrate_legacy_step_status(fm: dict) -> int:
    """Rewrite legacy ``s<N>`` keys in the ``step_status`` dict.

    Mutates ``fm`` in place. Returns the number of keys rewritten.
    Idempotent — already-canonical keys are left alone.

    ``step_status_updated`` is a single ISO timestamp string (not a
    keyed dict), so it does NOT need migration. The authoritative
    writer is :mod:`step_status` and the consumer side reads it as a
    plain string.
    """
    existing = fm.get("step_status")
    if not isinstance(existing, dict):
        return 0
    rewrites = 0
    migrated: dict = {}
    for sid, value in existing.items():
        new_id = canonicalize_step_id(sid)
        if new_id != sid:
            rewrites += 1
        migrated[new_id] = value
    fm["step_status"] = migrated
    return rewrites
