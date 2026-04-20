"""Abstract step — extract surfaces from a Cluster via high-tier LLM."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from lore_curator.cluster import Cluster
from lore_core.surfaces import SurfacesDoc, SurfaceDef


_HIGH_OFF_WARNING_ID = "abstract-high-tier-off-v1"


@dataclass(frozen=True)
class AbstractedSurface:
    surface_name: str           # one of surfaces_doc.surfaces' names
    title: str
    body: str
    extra_frontmatter: dict[str, Any] = field(default_factory=dict)


def abstract_cluster(
    *,
    cluster: Cluster,
    surfaces_doc: SurfacesDoc,
    source_notes_by_wikilink: dict[str, str],   # wikilink → note body for context
    anthropic_client: Any,
    model_resolver: Callable[[str], str],
    high_tier_off: bool = False,
    lore_root: Path | None = None,
) -> list[AbstractedSurface]:
    """Decide which surfaces (if any) to extract from this cluster.

    Empty cluster (no session_notes) → empty list, no LLM call.

    When high_tier_off:
      - falls back to middle-tier with a coarser prompt
      - emits a one-time per-session warning to <lore_root>/.lore/warnings.log
        (matches the pattern in noteworthy.py)
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

    prompt = _build_prompt(cluster, surfaces_doc, source_notes_by_wikilink, high_tier_off=high_tier_off)
    tool = _abstract_tool_schema(surfaces_vocab)

    resp = anthropic_client.messages.create(
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
    lines.append("")
    lines.append("SOURCE SESSION NOTES (truncated):")
    for wl in cluster.session_notes:
        body = source_notes_by_wikilink.get(wl, "")
        snippet = body[:1000] + ("…" if len(body) > 1000 else "")
        lines.append(f"--- {wl} ---")
        lines.append(snippet)
        lines.append("")
    lines.append(
        "Call the `abstract` tool with zero or more surfaces. Each surface's "
        "name MUST be one of the AVAILABLE SURFACES above. If the cluster "
        "doesn't meet any surface's extract-when bar, return an empty list."
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
        out.append(AbstractedSurface(
            surface_name=str(name),
            title=str(title),
            body=str(body),
            extra_frontmatter=extra,
        ))
    return out


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
