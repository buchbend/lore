from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SurfaceDef:
    name: str
    description: str        # body prose between heading and YAML block
    required: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    extract_when: str = ""  # free-text rule for the LLM
    plural: str | None = None
    slug_format: str | None = None
    extract_prompt: str | None = None
    # Marks the surface as owned by a specific writer. ``curator_a`` means
    # Curator B's extraction loop must skip it (the surface is filed
    # through a dedicated writer with its own path layout — e.g.
    # ``session_writer`` shards by date). Other values are reserved for
    # future authors and pass through unfiltered.
    authored_by: str | None = None


@dataclass(frozen=True)
class SurfacesDoc:
    schema_version: int
    surfaces: list[SurfaceDef]
    path: Path


class SurfacesError(ValueError):
    """Raised by lint paths; load_* never raises (warns + falls back)."""


def load_surfaces(wiki_dir: Path) -> SurfacesDoc | None:
    """Parse <wiki_dir>/SURFACES.md. Return None if absent.

    Forward-compatible: unknown keys + malformed sections warn and the
    rest of the file still loads.
    """
    path = wiki_dir / "SURFACES.md"
    if not path.exists():
        return None
    text = path.read_text()
    return _parse(text, path)


@lru_cache(maxsize=1)
def _load_packaged_standard() -> SurfacesDoc:
    """Read the shipped 'standard.md' template and parse it. Cached."""
    from importlib import resources
    text = resources.files("lore_core.surface_templates").joinpath("standard.md").read_text()
    return _parse(text, Path("<packaged:standard.md>"))


def load_surfaces_or_default(wiki_dir: Path) -> SurfacesDoc:
    """Like load_surfaces, returning the packaged 'standard' template when absent."""
    doc = load_surfaces(wiki_dir)
    return doc if doc is not None else _load_packaged_standard()


def is_curator_a_surface(s: SurfaceDef) -> bool:
    """True when ``s`` is filed by Curator A (and so off-limits to Curator B).

    Surfaces tagged ``authored_by: curator_a`` are filed by a dedicated
    writer with its own path layout (e.g. ``session_writer`` shards by
    date) — Curator B must not extract into them.

    Backward compat: an unmarked ``session`` surface (legacy SURFACES.md
    predating the flag) is treated as Curator A territory regardless,
    so vaults that haven't yet migrated SURFACES.md don't regress.
    """
    if s.authored_by == "curator_a":
        return True
    return s.authored_by is None and s.name == "session"


def extractable_surfaces(doc: SurfacesDoc) -> list[SurfaceDef]:
    """Return surfaces Curator B may extract into.

    Filters out surfaces owned by Curator A. Other ``authored_by`` values
    pass through so future writers don't silently disappear from the
    extraction vocabulary.
    """
    return [s for s in doc.surfaces if not is_curator_a_surface(s)]


def _parse(text: str, path: Path) -> SurfacesDoc:
    """Internal — split into preamble + sections, parse each."""
    schema_version = _parse_top_level_schema_version(text)
    sections = _split_sections(text)
    surfaces: list[SurfaceDef] = []
    for header, body in sections:
        sd = _parse_section(header, body, source=path)
        if sd is not None:
            surfaces.append(sd)
    return SurfacesDoc(
        schema_version=schema_version,
        surfaces=surfaces,
        path=path,
    )


_SCHEMA_RE = re.compile(r"^schema_version\s*:\s*(\d+)\s*$", re.MULTILINE)


def _parse_top_level_schema_version(text: str) -> int:
    """Find `schema_version: N` outside any section. Default 1."""
    # Only consider the preamble — text before the first `## `.
    end = text.find("\n## ")
    preamble = text if end == -1 else text[: end + 1]
    m = _SCHEMA_RE.search(preamble)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return 1


_SECTION_HEADER_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Return (name, body_lines_joined) per `## name` section."""
    matches = list(_SECTION_HEADER_RE.finditer(text))
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        out.append((name, body))
    return out


_YAML_FENCE_RE = re.compile(r"```yaml\s*\n(.*?)\n```", re.DOTALL)
_EXTRACT_WHEN_RE = re.compile(r"^\s*Extract when:\s*(.+?)\s*$", re.MULTILINE)


def _parse_section(name: str, body: str, *, source: Path) -> SurfaceDef | None:
    """Parse one section into a SurfaceDef, or return None if malformed-and-skipped."""
    yaml_match = _YAML_FENCE_RE.search(body)
    description = body[: yaml_match.start()].strip() if yaml_match else body.strip()

    required: list[str] = []
    optional: list[str] = []
    plural: str | None = None
    slug_format: str | None = None
    extract_prompt: str | None = None
    authored_by: str | None = None

    if yaml_match:
        yaml_text = yaml_match.group(1)
        try:
            parsed = yaml.safe_load(yaml_text) or {}
        except yaml.YAMLError as e:
            warnings.warn(
                f"surfaces: malformed YAML in section '{name}' at {source}: {e}",
                stacklevel=3,
            )
            return None
        if not isinstance(parsed, dict):
            warnings.warn(
                f"surfaces: YAML block in section '{name}' must be a mapping at {source}",
                stacklevel=3,
            )
            return None
        for key, value in parsed.items():
            if key == "required":
                required = list(value or [])
            elif key == "optional":
                optional = list(value or [])
            elif key == "plural":
                plural = str(value) if value is not None else None
            elif key == "slug_format":
                slug_format = str(value) if value is not None else None
            elif key == "extract_prompt":
                extract_prompt = str(value) if value is not None else None
            elif key == "authored_by":
                authored_by = str(value) if value is not None else None
            else:
                warnings.warn(
                    f"surfaces: unknown YAML key '{key}' in section '{name}' at {source}",
                    stacklevel=3,
                )

    extract_match = _EXTRACT_WHEN_RE.search(body[yaml_match.end() :] if yaml_match else body)
    extract_when = extract_match.group(1).strip() if extract_match else ""

    return SurfaceDef(
        name=name,
        description=description,
        required=required,
        optional=optional,
        extract_when=extract_when,
        plural=plural,
        slug_format=slug_format,
        extract_prompt=extract_prompt,
        authored_by=authored_by,
    )


def _yaml_dq_escape(s: str) -> str:
    """Escape a string for embedding inside a YAML double-quoted scalar."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def render_section(surface: SurfaceDef) -> str:
    """Render a single `## <name>` section. Output round-trips through _parse."""
    lines: list[str] = [f"## {surface.name}"]
    if surface.description:
        lines.append(surface.description)
    lines.append("")
    lines.append("```yaml")
    lines.append(f"required: [{', '.join(surface.required)}]")
    lines.append(f"optional: [{', '.join(surface.optional)}]")
    if surface.plural is not None:
        lines.append(f"plural: {surface.plural}")
    if surface.slug_format is not None:
        # Use YAML double-quoted form to preserve braces literally.
        lines.append(f'slug_format: "{_yaml_dq_escape(surface.slug_format)}"')
    if surface.extract_prompt is not None:
        # Block-scalar form for multi-line prompts.
        if "\n" in surface.extract_prompt:
            body = surface.extract_prompt.rstrip("\n")
            indented = "\n".join("  " + ln for ln in body.splitlines())
            lines.append("extract_prompt: |-")
            lines.append(indented)
        else:
            lines.append(f'extract_prompt: "{_yaml_dq_escape(surface.extract_prompt)}"')
    if surface.authored_by is not None:
        lines.append(f"authored_by: {surface.authored_by}")
    lines.append("```")
    if surface.extract_when:
        lines.append("")
        lines.append(f"Extract when: {surface.extract_when}")
    lines.append("")
    return "\n".join(lines) + "\n"


def render_document(
    *, schema_version: int, surfaces: list[SurfaceDef], wiki: str | None = None
) -> str:
    """Render a full SURFACES.md file (preamble + sections)."""
    header = f"# Surfaces — {wiki}" if wiki else "# Surfaces"
    parts = [f"{header}\n", f"schema_version: {schema_version}\n"]
    for s in surfaces:
        parts.append("\n")
        parts.append(render_section(s))
    return "".join(parts)


# ---------------------------------------------------------------------------
# Shared validator
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SLUG_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_SLUG_BUILTINS = frozenset({"date", "title", "slug"})


def _surface_spec_issues(
    spec: dict,
    *,
    existing_names: set[str],
    existing_plurals: set[str],
    path_prefix: str = "surface",
) -> list[dict]:
    """Validate one surface spec dict. Returns a list of issue dicts."""
    issues: list[dict] = []
    name = spec.get("name", "")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        issues.append({
            "level": "error",
            "code": "invalid_name",
            "message": f"{path_prefix}.name must match ^[a-z][a-z0-9_]*$ (got {name!r})",
        })
        return issues  # name is load-bearing for the rest
    if name in existing_names:
        issues.append({
            "level": "error",
            "code": "duplicate_name",
            "message": f"surface '{name}' already exists",
        })
    required = list(spec.get("required") or [])
    optional = list(spec.get("optional") or [])
    if not isinstance(spec.get("required"), list) or not isinstance(spec.get("optional"), list):
        issues.append({
            "level": "error",
            "code": "invalid_fields",
            "message": f"{path_prefix}.required and .optional must be lists",
        })
        return issues
    overlap = set(required) & set(optional)
    if overlap:
        issues.append({
            "level": "error",
            "code": "required_optional_overlap",
            "message": f"{path_prefix}: required and optional overlap on {sorted(overlap)}",
        })
    plural = spec.get("plural")
    if plural is not None:
        if not isinstance(plural, str) or not _NAME_RE.match(plural):
            issues.append({
                "level": "error",
                "code": "invalid_plural",
                "message": f"{path_prefix}.plural must match ^[a-z][a-z0-9_]*$ (got {plural!r})",
            })
    effective_plural = plural if plural else f"{name}s" if not name.endswith("s") else name
    if effective_plural in existing_plurals:
        issues.append({
            "level": "error",
            "code": "plural_collision",
            "message": f"{path_prefix}.plural '{effective_plural}' collides with an existing surface's directory",
        })
    slug_format = spec.get("slug_format")
    if slug_format is not None:
        if not isinstance(slug_format, str):
            issues.append({
                "level": "error",
                "code": "invalid_slug_format",
                "message": f"{path_prefix}.slug_format must be a string",
            })
        else:
            allowed = _SLUG_BUILTINS | set(required) | set(optional)
            placeholders = set(_SLUG_PLACEHOLDER_RE.findall(slug_format))
            unknown = placeholders - allowed
            if unknown:
                issues.append({
                    "level": "error",
                    "code": "invalid_slug_format",
                    "message": f"{path_prefix}.slug_format uses unknown placeholders {sorted(unknown)}; allowed: {sorted(allowed)}",
                })
    extract_prompt = spec.get("extract_prompt")
    if extract_prompt is not None:
        if not isinstance(extract_prompt, str) or not extract_prompt.strip():
            issues.append({
                "level": "error",
                "code": "empty_extract_prompt",
                "message": f"{path_prefix}.extract_prompt must be a non-empty string when present",
            })
    return issues


def validate_draft(draft: dict, *, wiki_dir: Path) -> list[dict]:
    """Validate a draft-spec (single append or full init). Returns issue list (empty = ok)."""
    issues: list[dict] = []
    if draft.get("schema") != "lore.surface.draft/1":
        issues.append({
            "level": "error",
            "code": "unknown_schema",
            "message": f"unsupported draft schema: {draft.get('schema')!r}",
        })
        return issues
    op = draft.get("operation")
    if op == "append":
        existing = load_surfaces(wiki_dir)
        existing_names = {s.name for s in (existing.surfaces if existing else [])}
        existing_plurals = {
            (s.plural or (s.name if s.name.endswith("s") else f"{s.name}s"))
            for s in (existing.surfaces if existing else [])
        }
        spec = draft.get("surface") or {}
        issues.extend(_surface_spec_issues(
            spec,
            existing_names=existing_names,
            existing_plurals=existing_plurals,
        ))
    elif op == "init":
        surfaces = list(draft.get("surfaces") or [])
        existing_names: set[str] = set()
        existing_plurals: set[str] = set()
        for i, spec in enumerate(surfaces):
            sub = _surface_spec_issues(
                spec,
                existing_names=existing_names,
                existing_plurals=existing_plurals,
                path_prefix=f"surfaces[{i}]",
            )
            issues.extend(sub)
            # Track for intra-draft collision detection.
            if isinstance(spec.get("name"), str) and _NAME_RE.match(spec["name"]):
                existing_names.add(spec["name"])
                plural = spec.get("plural") or (
                    spec["name"] if spec["name"].endswith("s") else f"{spec['name']}s"
                )
                existing_plurals.add(plural)
    else:
        issues.append({
            "level": "error",
            "code": "unknown_operation",
            "message": f"draft.operation must be 'append' or 'init' (got {op!r})",
        })
    return issues


def rewrite_scopes_in_frontmatter(
    wiki_root: Path,
    mapping: dict[str, str],
) -> int:
    """Rewrite ``scope:`` and ``scopes: [...]`` frontmatter values across
    every Markdown note under ``wiki_root``.

    Phase 8 sibling primitive to ``state.attachments.rewrite_scopes``.
    Walks ``wiki_root.rglob("*.md")``, parses frontmatter, and applies
    ``mapping`` (old_id → new_id) to:

      - ``scope:`` (string field)
      - ``scopes:`` (list field)

    Subtree-aware: when an exact-match isn't in ``mapping``, also
    rewrites entries that *start with* a mapped prefix plus ``:``. This
    matches how attachments are rewritten — renaming ``ccat:data-center``
    cascades to ``ccat:data-center:ops-db``.

    Returns the number of files changed. Atomic per-file via
    :func:`lore_core.io.atomic_write_text`. Cross-file atomicity is
    *not* guaranteed; the caller orders writes (see Phase 8 spec).
    """
    from lore_core.io import atomic_write_text
    from lore_core.schema import parse_frontmatter, strip_frontmatter

    if not wiki_root.is_dir():
        return 0
    if not mapping:
        return 0

    def _apply(value: str) -> str:
        if value in mapping:
            return mapping[value]
        for old, new in mapping.items():
            prefix = old + ":"
            if value.startswith(prefix):
                return new + value[len(old):]
        return value

    changed = 0
    for path in wiki_root.rglob("*.md"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        fm = parse_frontmatter(text)
        if not fm:
            continue

        new_fm: dict = dict(fm)
        dirty = False

        scope_val = fm.get("scope")
        if isinstance(scope_val, str) and scope_val:
            replacement = _apply(scope_val)
            if replacement != scope_val:
                new_fm["scope"] = replacement
                dirty = True

        scopes_val = fm.get("scopes")
        if isinstance(scopes_val, list):
            new_list = [
                _apply(s) if isinstance(s, str) else s
                for s in scopes_val
            ]
            if new_list != scopes_val:
                new_fm["scopes"] = new_list
                dirty = True

        if not dirty:
            continue

        body = strip_frontmatter(text)
        try:
            import yaml

            dumped = yaml.safe_dump(
                new_fm, sort_keys=False, allow_unicode=True
            ).strip()
        except Exception:  # noqa: BLE001 - never block on YAML edge cases
            continue
        new_text = f"---\n{dumped}\n---\n\n{body.lstrip()}"
        try:
            atomic_write_text(path, new_text)
            changed += 1
        except OSError:
            continue

    return changed
