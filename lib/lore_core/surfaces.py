from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class SurfacesDoc:
    schema_version: int
    surfaces: list[SurfaceDef]
    path: Path


class SurfacesError(ValueError):
    """Raised by lint paths; load_* never raises (warns + falls back)."""


_DEFAULT_STANDARD = SurfacesDoc(
    schema_version=2,
    surfaces=[
        SurfaceDef(
            name="concept",
            description="Cross-cutting idea or pattern across sessions.",
            required=["type", "created", "last_reviewed", "description", "tags"],
            optional=["aliases", "superseded_by", "draft"],
            extract_when="pattern appears across 3+ session notes",
        ),
        SurfaceDef(
            name="decision",
            description="A trade-off made — alternatives, path chosen.",
            required=["type", "created", "last_reviewed", "description", "tags"],
            optional=["superseded_by", "implements"],
            extract_when="session note records a trade-off decision",
        ),
        SurfaceDef(
            name="session",
            description="Work session log filed by Curator A.",
            required=["type", "created", "last_reviewed", "description"],
            optional=["scope", "tags", "draft", "source_transcripts"],
        ),
    ],
    path=Path("<built-in default>"),
)


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


def load_surfaces_or_default(wiki_dir: Path) -> SurfacesDoc:
    """Like load_surfaces, returning the built-in standard set when absent."""
    doc = load_surfaces(wiki_dir)
    return doc if doc is not None else _DEFAULT_STANDARD


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
    )
