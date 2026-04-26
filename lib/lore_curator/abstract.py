"""Abstract step — extract surfaces from a Cluster via high-tier LLM."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from lore_curator.cluster import Cluster
from lore_core.surfaces import SurfacesDoc


_HIGH_OFF_WARNING_ID = "abstract-high-tier-off-v1"

# Per-note body excerpt budget for the abstract prompt. Sized so a typical
# 5-10 note cluster fits comfortably under Claude's context budget while
# preserving enough substance for the LLM to write a non-summary body. The
# previous value (1000) lost too much signal — abstracted bodies regressed
# to recent-work summaries because the LLM never saw the actual content.
_ABSTRACT_BODY_PER_NOTE_CHARS = 4000


@dataclass(frozen=True)
class AbstractedSurface:
    surface_name: str           # one of surfaces_doc.surfaces' names
    title: str
    body: str
    extra_frontmatter: dict[str, Any] = field(default_factory=dict)
    # When set, the LLM judged this cluster's content extends an existing
    # surface (rather than warranting a new one). Curator B logs the
    # suggestion and skips writing a new note. Defrag/Curator C is the
    # right place to act on the merge proposal — Curator B's job is to
    # NOT fragment.
    merge_into: str | None = None


def abstract_cluster(
    *,
    cluster: Cluster,
    surfaces_doc: SurfacesDoc,
    source_notes_by_wikilink: dict[str, str],   # wikilink → note body for context
    llm_client: Any,
    model_resolver: Callable[[str], str],
    high_tier_off: bool = False,
    lore_root: Path | None = None,
    existing_surfaces: dict[str, list[dict]] | None = None,
) -> list[AbstractedSurface]:
    """Decide which surfaces (if any) to extract from this cluster.

    Empty cluster (no session_notes) → empty list, no LLM call.

    When high_tier_off:
      - falls back to middle-tier with a coarser prompt
      - emits a one-time per-session warning to <lore_root>/.lore/warnings.log
        (matches the pattern in noteworthy.py)

    `existing_surfaces` maps surface_name → list of `{wikilink, description}`
    dicts for surfaces of that type already in the wiki. When supplied, the
    LLM is told to prefer ``merge_into`` over creating a new note when the
    cluster extends an existing surface. Default {} means create-only
    behaviour (back-compat).
    """
    if not cluster.session_notes:
        return []

    if high_tier_off:
        _emit_high_off_warning_once(lore_root)
        tier = "middle"
    else:
        tier = "high"

    model = model_resolver(tier)
    surfaces_vocab = [s.name for s in surfaces_doc.surfaces]
    if not surfaces_vocab:
        return []

    prompt = _build_prompt(
        cluster,
        surfaces_doc,
        source_notes_by_wikilink,
        high_tier_off=high_tier_off,
        existing_surfaces=existing_surfaces or {},
    )
    tool = _abstract_tool_schema(surfaces_vocab)

    resp = llm_client.messages.create(
        model=model,
        max_tokens=2048,
        tools=[tool],
        tool_choice={"type": "tool", "name": "abstract"},
        messages=[{"role": "user", "content": prompt}],
    )
    data = _extract_tool_input(resp)
    return _parse_surfaces(data, valid_surfaces=set(surfaces_vocab))


def _build_prompt(
    cluster: Cluster,
    surfaces_doc: SurfacesDoc,
    source_notes_by_wikilink: dict[str, str],
    *,
    high_tier_off: bool,
    existing_surfaces: dict[str, list[dict]],
) -> str:
    lines = []
    if high_tier_off:
        lines.append(
            "You are abstracting a cluster of session notes into the wiki's "
            "declared surfaces. NOTE: running on middle-tier (high tier "
            "disabled by config); be conservative — only extract surfaces "
            "where the pattern is unambiguous."
        )
    else:
        lines.append(
            "You are abstracting a cluster of session notes into the wiki's "
            "declared surfaces. Use the surface vocabulary's `extract_when` "
            "rules to decide whether the cluster meets each surface's bar."
        )
    lines.append("")
    lines.append(f"CLUSTER: topic={cluster.topic!r}, scope={cluster.scope!r}")
    if cluster.suggested_surface:
        lines.append(f"  (clustering step suggested surface: {cluster.suggested_surface})")
    lines.append("")
    lines.append("AVAILABLE SURFACES:")
    for s in surfaces_doc.surfaces:
        lines.append(f"  - {s.name}: {s.description}")
        if s.extract_when:
            lines.append(f"    extract when: {s.extract_when}")
        if s.extract_prompt:
            lines.append(f"    guidance: {s.extract_prompt}")
    lines.append("")
    if any(existing_surfaces.values()):
        lines.append("EXISTING SURFACES (already in this wiki):")
        for surface_name, items in existing_surfaces.items():
            if not items:
                continue
            lines.append(f"  {surface_name}:")
            for item in items:
                wl = item.get("wikilink", "")
                desc = item.get("description", "") or ""
                lines.append(f"    - {wl} — {desc}")
        lines.append("")
        lines.append(
            "If this cluster's content extends or refines one of the EXISTING "
            "SURFACES above (rather than warranting a wholly new surface), set "
            "the `merge_into` field on that surface entry to the existing "
            "surface's wikilink instead of creating a new one. This is the "
            "preferred outcome when the topic is already represented — "
            "creating duplicates fragments the vault."
        )
        lines.append("")
    lines.append(f"SOURCE SESSION NOTES (each truncated to {_ABSTRACT_BODY_PER_NOTE_CHARS} chars):")
    for wl in cluster.session_notes:
        body = source_notes_by_wikilink.get(wl, "")
        snippet = body[:_ABSTRACT_BODY_PER_NOTE_CHARS] + (
            "…" if len(body) > _ABSTRACT_BODY_PER_NOTE_CHARS else ""
        )
        lines.append(f"--- {wl} ---")
        lines.append(snippet)
        lines.append("")
    lines.append(
        "Call the `abstract` tool with zero or more surfaces. Each surface's "
        "name MUST be one of the AVAILABLE SURFACES above. If the cluster "
        "doesn't meet any surface's extract-when bar, return an empty list."
    )
    lines.append(
        "The `body` field must contain ONLY the prose body of the note — "
        "do NOT prepend YAML frontmatter (no leading `---` block). The "
        "writer assembles frontmatter from the tool fields; emitting it "
        "in `body` produces a malformed note with two stacked frontmatter blocks."
    )
    return "\n".join(lines)


def _abstract_tool_schema(surfaces_vocab: list[str]) -> dict:
    return {
        "name": "abstract",
        "description": "Emit the surfaces to extract (possibly zero).",
        "input_schema": {
            "type": "object",
            "properties": {
                "surfaces": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "surface_name": {"type": "string", "enum": surfaces_vocab},
                            "title": {"type": "string"},
                            "body": {"type": "string"},
                            "extra_frontmatter": {"type": "object"},
                            "merge_into": {
                                "type": "string",
                                "description": (
                                    "Wikilink (e.g. '[[existing-slug]]') of an EXISTING "
                                    "surface this cluster extends rather than creates anew. "
                                    "When set, Curator B logs the suggestion and skips "
                                    "filing a new note; the defrag pass acts on it."
                                ),
                            },
                        },
                        "required": ["surface_name", "title", "body"],
                    },
                }
            },
            "required": ["surfaces"],
        },
    }


def _extract_tool_input(resp) -> dict:
    for block in getattr(resp, "content", []):
        btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
        if btype == "tool_use":
            inp = getattr(block, "input", None)
            if inp is None and isinstance(block, dict):
                inp = block.get("input")
            if isinstance(inp, dict):
                return inp
    return {"surfaces": []}


def _parse_surfaces(data: dict, *, valid_surfaces: set[str]) -> list[AbstractedSurface]:
    out: list[AbstractedSurface] = []
    for raw in data.get("surfaces", []) or []:
        if not isinstance(raw, dict):
            continue
        name = raw.get("surface_name")
        title = raw.get("title")
        body = raw.get("body")
        if not name or not title or not body:
            continue
        if name not in valid_surfaces:
            continue          # silently drop invalid surface names
        extra = raw.get("extra_frontmatter") or {}
        if not isinstance(extra, dict):
            extra = {}
        merge_into = raw.get("merge_into")
        if merge_into is not None and not isinstance(merge_into, str):
            merge_into = None
        if isinstance(merge_into, str) and not merge_into.strip():
            merge_into = None
        stripped_body = _strip_leading_frontmatter(str(body))
        if not stripped_body.strip():
            # Body was nothing but frontmatter — drop the surface rather
            # than write an empty-body note.
            continue
        out.append(AbstractedSurface(
            surface_name=str(name),
            title=str(title),
            body=stripped_body,
            extra_frontmatter=extra,
            merge_into=merge_into,
        ))
    return out


def _strip_leading_frontmatter(text: str) -> str:
    """Strip a leading YAML frontmatter block if the LLM accidentally included one.

    The abstraction LLM occasionally emits body content beginning with a
    `---\\n…\\n---\\n` prelude. Without this defensive strip, surface_filer
    wraps the body in a *second* frontmatter block, producing notes with
    two stacked `---` headers.

    Two cases left intact (return text unchanged):
      - No closing fence — never risk dropping legitimate content.
      - The candidate block doesn't yaml-parse to a dict. Markdown bodies
        that legitimately use `---` as a horizontal rule between sections
        would otherwise lose content; only strip when we can prove the
        leading block is actual frontmatter.
    """
    import yaml

    stripped = text.lstrip("\n")
    if not stripped.startswith("---"):
        return text
    after_open = stripped[3:]
    close_idx = after_open.find("\n---")
    if close_idx == -1:
        return text
    candidate = after_open[:close_idx]
    try:
        parsed = yaml.safe_load(candidate)
    except yaml.YAMLError:
        return text
    if not isinstance(parsed, dict):
        return text
    rest = after_open[close_idx + 4:]
    return rest.lstrip("\n")


def _emit_high_off_warning_once(lore_root: Path | None) -> None:
    """Write a one-time warning to <lore_root>/.lore/warnings.log if not seen."""
    if lore_root is None:
        return
    log_path = lore_root / ".lore" / "warnings.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists() and _HIGH_OFF_WARNING_ID in log_path.read_text():
        return
    msg = (
        f"[{_HIGH_OFF_WARNING_ID}] Curator B running without high-tier — "
        "adjacent-concept merging and supersession detection run at middle "
        "tier; expect coarser judgments."
    )
    with log_path.open("a") as f:
        f.write(msg + "\n")
