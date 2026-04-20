# Surface Authoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace typed-args surface authoring with LLM-assisted skills (`/lore:surface-new`, `/lore:surface-init`) driven by two MCP tools and a deterministic `lore surface commit <draft.json>` primitive. Add `plural`, `slug_format`, `extract_prompt` keys to SURFACES.md and collapse the duplicated `_DEFAULT_STANDARD` source of truth.

**Architecture:** Three layers — skills drive conversation, MCP tools (`lore_surface_context`, `lore_surface_validate`) gather + validate, CLI `commit` writes. `lore surface add` / `lore surface init` become thin launchers that exec `claude "/lore:surface-<verb> <wiki>"`. Schema additions are backwards-compatible (optional fields; parser warns-and-skips unknown keys). Shared core (`lore_core.surfaces`) holds parser + renderer + validator; MCP validate and CLI commit call the same library functions.

**Tech Stack:** Python 3.11+, typer (CLI), pytest, jsonschema (optional for draft validation), stdlib `subprocess` (launcher), `mcp` SDK (already in use). No new runtime dependencies.

**Spec reference:** `docs/superpowers/specs/2026-04-20-surface-authoring-design.md` (commit `a63cacc`).

**Phases:**
- **A. Core schema + shared library** (T1–T5): parser/renderer/validator in `lore_core.surfaces`; `_DEFAULT_STANDARD` collapse.
- **B. Consumer migrations** (T6–T8): `surface_filer` uses new keys; Curator B uses `extract_prompt`.
- **C. `lore surface commit` CLI** (T9–T11): write primitive + tests.
- **D. MCP tools** (T12–T14): `lore_surface_context`, `lore_surface_validate`.
- **E. Lint additions** (T15): new-key validation cases.
- **F. CLI launchers** (T16–T17): `add` / `init` become thin exec shims.
- **G. Skills** (T18–T19): `/lore:surface-new`, `/lore:surface-init`.
- **H. Docs + integration** (T20–T21): README update; end-to-end fixture test.

Each task is independently committable. After every commit, run `pytest -q` at the repo root — must stay green.

**Pre-flight checks** (do these before Task 1; they are not part of the plan's tests):

- `git log --oneline -1` prints `a63cacc docs(spec): surface authoring design — skills + MCP + commit CLI`
- `git status --short` — note that the repo has existing uncommitted work from a prior session (README.md, surface_cmd.py, etc.). **Do not modify or revert these.** They are independent in-progress work and would cause conflicts. Work on clean slices of each file.
- `pytest -q` — current green count is ~527.

---

## Phase A — Core schema + shared library

### Task 1: Parser — add the three new SurfaceDef fields

**Files:**
- Modify: `lib/lore_core/surfaces.py` — `SurfaceDef` dataclass + `_parse_section`
- Modify: `tests/test_surfaces.py` — parser round-trip tests

- [ ] **Step 1: Write failing tests**

Add to `tests/test_surfaces.py`:

```python
def test_surfaces_parse_plural_key():
    text = (
        "# Surfaces\nschema_version: 2\n\n"
        "## paper\nA paper.\n\n"
        "```yaml\nrequired: [type]\noptional: []\nplural: papers\n```\n"
    )
    from lore_core.surfaces import _parse
    from pathlib import Path
    doc = _parse(text, Path("<test>"))
    assert doc.surfaces[0].plural == "papers"


def test_surfaces_parse_slug_format_key():
    text = (
        "# Surfaces\nschema_version: 2\n\n"
        "## paper\nA paper.\n\n"
        "```yaml\nrequired: [type, citekey]\noptional: []\nslug_format: \"{citekey}\"\n```\n"
    )
    from lore_core.surfaces import _parse
    from pathlib import Path
    doc = _parse(text, Path("<test>"))
    assert doc.surfaces[0].slug_format == "{citekey}"


def test_surfaces_parse_extract_prompt_key():
    text = (
        "# Surfaces\nschema_version: 2\n\n"
        "## paper\nA paper.\n\n"
        "```yaml\nrequired: [type]\noptional: []\nextract_prompt: \"Prefer citekey.\"\n```\n"
    )
    from lore_core.surfaces import _parse
    from pathlib import Path
    doc = _parse(text, Path("<test>"))
    assert doc.surfaces[0].extract_prompt == "Prefer citekey."


def test_surfaces_parse_new_keys_absent_defaults_to_none():
    text = (
        "# Surfaces\nschema_version: 2\n\n"
        "## concept\nA concept.\n\n"
        "```yaml\nrequired: [type]\noptional: []\n```\n"
    )
    from lore_core.surfaces import _parse
    from pathlib import Path
    s = _parse(text, Path("<test>")).surfaces[0]
    assert s.plural is None
    assert s.slug_format is None
    assert s.extract_prompt is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_surfaces.py -k "plural_key or slug_format_key or extract_prompt_key or new_keys_absent" -v`

Expected: FAIL — `AttributeError: 'SurfaceDef' object has no attribute 'plural'` (similar for the others).

- [ ] **Step 3: Extend SurfaceDef and parser**

In `lib/lore_core/surfaces.py`, replace the `SurfaceDef` dataclass:

```python
@dataclass(frozen=True)
class SurfaceDef:
    name: str
    description: str
    required: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    extract_when: str = ""
    plural: str | None = None
    slug_format: str | None = None
    extract_prompt: str | None = None
```

In `_parse_section`, inside the `for key, value in parsed.items()` loop, add branches **before** the unknown-key warning:

```python
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
```

Add `plural: str | None = None`, `slug_format: str | None = None`, `extract_prompt: str | None = None` as locals initialized to `None` before the `if yaml_match:` block. Pass them through to `SurfaceDef(...)` at the return.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_surfaces.py -q`

Expected: all tests pass (new + existing).

- [ ] **Step 5: Commit**

```bash
git add lib/lore_core/surfaces.py tests/test_surfaces.py
git commit -m "feat(surfaces): parser supports plural/slug_format/extract_prompt keys"
```

---

### Task 2: Renderer — `render_section` and `render_document`

**Files:**
- Modify: `lib/lore_core/surfaces.py` — add two new functions
- Modify: `tests/test_surfaces.py` — renderer round-trip tests

- [ ] **Step 1: Write failing tests**

Add to `tests/test_surfaces.py`:

```python
def test_render_section_minimal():
    from lore_core.surfaces import SurfaceDef, render_section
    s = SurfaceDef(
        name="concept",
        description="Cross-cutting idea.",
        required=["type", "created"],
        optional=["draft"],
    )
    out = render_section(s)
    assert out.startswith("## concept\n")
    assert "Cross-cutting idea." in out
    assert "required: [type, created]" in out
    assert "optional: [draft]" in out
    assert out.endswith("\n")


def test_render_section_with_new_keys():
    from lore_core.surfaces import SurfaceDef, render_section
    s = SurfaceDef(
        name="paper",
        description="Publication.",
        required=["type", "citekey"],
        optional=[],
        extract_when="paper discussed",
        plural="papers",
        slug_format="{citekey}",
        extract_prompt="Prefer citekey.",
    )
    out = render_section(s)
    assert "plural: papers" in out
    assert 'slug_format: "{citekey}"' in out or "slug_format: '{citekey}'" in out
    assert "extract_prompt: " in out
    assert "Extract when: paper discussed" in out


def test_render_section_roundtrips_through_parser():
    from lore_core.surfaces import SurfaceDef, render_section, _parse
    from pathlib import Path
    s = SurfaceDef(
        name="paper",
        description="A publication.",
        required=["type", "citekey", "title"],
        optional=["draft"],
        plural="papers",
        slug_format="{citekey}",
        extract_prompt="Use citekey.",
    )
    section = render_section(s)
    doc_text = f"# Surfaces\nschema_version: 2\n\n{section}"
    parsed = _parse(doc_text, Path("<test>")).surfaces[0]
    assert parsed.name == "paper"
    assert parsed.required == ["type", "citekey", "title"]
    assert parsed.plural == "papers"
    assert parsed.slug_format == "{citekey}"
    assert parsed.extract_prompt == "Use citekey."


def test_render_document_with_header_and_sections():
    from lore_core.surfaces import SurfaceDef, render_document
    a = SurfaceDef(name="concept", description="A.", required=["type"], optional=[])
    b = SurfaceDef(name="decision", description="B.", required=["type"], optional=[])
    out = render_document(schema_version=2, surfaces=[a, b], wiki="science")
    assert out.startswith("# Surfaces — science\n")
    assert "schema_version: 2" in out
    assert "## concept" in out
    assert "## decision" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_surfaces.py -k "render_" -v`

Expected: FAIL — `ImportError: cannot import name 'render_section'`.

- [ ] **Step 3: Add renderers**

In `lib/lore_core/surfaces.py`, append:

```python
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
        escaped = surface.slug_format.replace('"', '\\"')
        lines.append(f'slug_format: "{escaped}"')
    if surface.extract_prompt is not None:
        # Block-scalar form for multi-line prompts.
        if "\n" in surface.extract_prompt:
            body = surface.extract_prompt.rstrip("\n")
            indented = "\n".join("  " + ln for ln in body.splitlines())
            lines.append("extract_prompt: |")
            lines.append(indented)
        else:
            escaped = surface.extract_prompt.replace('"', '\\"')
            lines.append(f'extract_prompt: "{escaped}"')
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_surfaces.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add lib/lore_core/surfaces.py tests/test_surfaces.py
git commit -m "feat(surfaces): add render_section/render_document (round-trips through parser)"
```

---

### Task 3: Shared validator — `validate_draft`

**Files:**
- Modify: `lib/lore_core/surfaces.py` — add `Issue` + `validate_draft`
- Modify: `tests/test_surfaces.py` — validator tests

- [ ] **Step 1: Write failing tests**

Add to `tests/test_surfaces.py`:

```python
def _minimal_append_draft(**overrides):
    base = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "append",
        "surface": {
            "name": "paper",
            "description": "A paper.",
            "required": ["type", "created", "description", "tags"],
            "optional": ["draft"],
        },
    }
    base["surface"].update(overrides)
    return base


def test_validate_draft_happy_path_append(tmp_path):
    from lore_core.surfaces import validate_draft
    (tmp_path / "SURFACES.md").write_text(
        "# Surfaces\nschema_version: 2\n\n## concept\nA.\n\n```yaml\nrequired: [type]\noptional: []\n```\n"
    )
    issues = validate_draft(_minimal_append_draft(), wiki_dir=tmp_path)
    assert issues == []


def test_validate_draft_rejects_duplicate_name(tmp_path):
    from lore_core.surfaces import validate_draft
    (tmp_path / "SURFACES.md").write_text(
        "# Surfaces\nschema_version: 2\n\n## paper\nA.\n\n```yaml\nrequired: [type]\noptional: []\n```\n"
    )
    issues = validate_draft(_minimal_append_draft(), wiki_dir=tmp_path)
    assert any(i["code"] == "duplicate_name" for i in issues)


def test_validate_draft_rejects_required_optional_overlap(tmp_path):
    from lore_core.surfaces import validate_draft
    d = _minimal_append_draft(required=["type", "draft"], optional=["draft"])
    issues = validate_draft(d, wiki_dir=tmp_path)
    assert any(i["code"] == "required_optional_overlap" for i in issues)


def test_validate_draft_rejects_bad_name_shape(tmp_path):
    from lore_core.surfaces import validate_draft
    d = _minimal_append_draft(name="My Fancy Surface!")
    issues = validate_draft(d, wiki_dir=tmp_path)
    assert any(i["code"] == "invalid_name" for i in issues)


def test_validate_draft_rejects_bad_plural_shape(tmp_path):
    from lore_core.surfaces import validate_draft
    d = _minimal_append_draft(plural="Papers Galore!")
    issues = validate_draft(d, wiki_dir=tmp_path)
    assert any(i["code"] == "invalid_plural" for i in issues)


def test_validate_draft_rejects_plural_collision(tmp_path):
    from lore_core.surfaces import validate_draft
    (tmp_path / "SURFACES.md").write_text(
        "# Surfaces\nschema_version: 2\n\n## paper\nA.\n\n```yaml\nrequired: [type]\noptional: []\n```\n"
    )
    d = _minimal_append_draft(name="study", plural="papers")
    issues = validate_draft(d, wiki_dir=tmp_path)
    assert any(i["code"] == "plural_collision" for i in issues)


def test_validate_draft_rejects_unknown_slug_format_placeholder(tmp_path):
    from lore_core.surfaces import validate_draft
    d = _minimal_append_draft(slug_format="{nonsense}")
    issues = validate_draft(d, wiki_dir=tmp_path)
    assert any(i["code"] == "invalid_slug_format" for i in issues)


def test_validate_draft_accepts_known_slug_format_placeholders(tmp_path):
    from lore_core.surfaces import validate_draft
    d = _minimal_append_draft(
        required=["type", "created", "description", "tags", "citekey"],
        slug_format="{citekey}-{date}",
    )
    issues = validate_draft(d, wiki_dir=tmp_path)
    assert issues == []


def test_validate_draft_rejects_empty_extract_prompt(tmp_path):
    from lore_core.surfaces import validate_draft
    d = _minimal_append_draft(extract_prompt="")
    issues = validate_draft(d, wiki_dir=tmp_path)
    assert any(i["code"] == "empty_extract_prompt" for i in issues)


def test_validate_draft_init_operation(tmp_path):
    from lore_core.surfaces import validate_draft
    d = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "init",
        "schema_version": 2,
        "surfaces": [
            {"name": "a", "description": "A", "required": ["type"], "optional": []},
            {"name": "b", "description": "B", "required": ["type"], "optional": []},
        ],
    }
    issues = validate_draft(d, wiki_dir=tmp_path)
    assert issues == []


def test_validate_draft_init_detects_internal_collision(tmp_path):
    from lore_core.surfaces import validate_draft
    d = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "init",
        "schema_version": 2,
        "surfaces": [
            {"name": "concept", "description": "", "required": ["type"], "optional": []},
            {"name": "concept", "description": "", "required": ["type"], "optional": []},
        ],
    }
    issues = validate_draft(d, wiki_dir=tmp_path)
    assert any(i["code"] == "duplicate_name" for i in issues)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_surfaces.py -k "validate_draft" -v`

Expected: FAIL — `ImportError: cannot import name 'validate_draft'`.

- [ ] **Step 3: Implement `validate_draft`**

In `lib/lore_core/surfaces.py`, append:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_surfaces.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add lib/lore_core/surfaces.py tests/test_surfaces.py
git commit -m "feat(surfaces): validate_draft — shared validator for append/init drafts"
```

---

### Task 4: Collapse `_DEFAULT_STANDARD` → read packaged `standard.md`

**Files:**
- Modify: `lib/lore_core/surfaces.py` — remove `_DEFAULT_STANDARD`, reimplement `load_surfaces_or_default`
- Modify: `tests/test_surfaces.py` — tests asserting equivalence

- [ ] **Step 1: Write failing test**

Add to `tests/test_surfaces.py`:

```python
def test_load_surfaces_or_default_reads_packaged_standard(tmp_path):
    from lore_core.surfaces import load_surfaces_or_default
    doc = load_surfaces_or_default(tmp_path)  # no SURFACES.md → fallback
    names = [s.name for s in doc.surfaces]
    assert names == ["concept", "decision", "session"]
    # And schema_version from the template header is 2
    assert doc.schema_version == 2


def test_load_surfaces_or_default_cache_returns_same_object(tmp_path):
    from lore_core.surfaces import load_surfaces_or_default
    a = load_surfaces_or_default(tmp_path)
    b = load_surfaces_or_default(tmp_path)
    # Same surfaces list content (caching is internal; we just assert equivalence).
    assert [s.name for s in a.surfaces] == [s.name for s in b.surfaces]
```

- [ ] **Step 2: Run tests to verify they fail or pass accidentally**

Run: `pytest tests/test_surfaces.py -k "load_surfaces_or_default" -v`

Expected: existing behavior may incidentally pass the `names` assertion because `_DEFAULT_STANDARD` has the same names. That's fine — this task is a refactor. Proceed.

- [ ] **Step 3: Remove `_DEFAULT_STANDARD`; reimplement the fallback**

In `lib/lore_core/surfaces.py`:

Delete the `_DEFAULT_STANDARD = SurfacesDoc(...)` block entirely (lines ~31-56 in the current file).

Replace `load_surfaces_or_default` with:

```python
from functools import lru_cache


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
```

- [ ] **Step 4: Run the full test suite**

Run: `pytest -q`

Expected: all passing. No regression in curator or schema tests that depend on the default standard set.

- [ ] **Step 5: Commit**

```bash
git add lib/lore_core/surfaces.py tests/test_surfaces.py
git commit -m "refactor(surfaces): load 'standard' fallback from packaged template (single source of truth)"
```

---

### Task 5: Make `standard.md` template carry the new keys on one surface

**Files:**
- Modify: `lib/lore_core/surface_templates/standard.md` — add `plural:` and `extract_prompt:` to `concept` and `decision` as examples; leave `session` plain for coverage of "no new keys" path

- [ ] **Step 1: Edit the template**

`lib/lore_core/surface_templates/standard.md`:

```markdown
# Surfaces — <wiki>
schema_version: 2

## concept
Cross-cutting idea or pattern across sessions.

```yaml
required: [type, created, last_reviewed, description, tags]
optional: [aliases, superseded_by, draft]
plural: concepts
extract_prompt: "A cross-cutting idea. Extract when the same idea appears across 3+ session notes."
```

Extract when: pattern appears across 3+ session notes.

## decision
A trade-off made — alternatives considered, path chosen.

```yaml
required: [type, created, last_reviewed, description, tags]
optional: [superseded_by, implements]
plural: decisions
extract_prompt: "A trade-off with alternatives considered. Title reflects the choice."
```

Extract when: a session note records a trade-off decision.

## session
Work session log filed by Curator A.

```yaml
required: [type, created, last_reviewed, description]
optional: [scope, tags, draft, source_transcripts]
```
```

- [ ] **Step 2: Run the full suite**

Run: `pytest -q`

Expected: all green. The `load_surfaces_or_default` parser now sees `plural` + `extract_prompt` on `concept` / `decision`.

- [ ] **Step 3: Commit**

```bash
git add lib/lore_core/surface_templates/standard.md
git commit -m "feat(templates): standard template uses plural + extract_prompt on concept/decision"
```

---

## Phase B — Consumer migrations

### Task 6: `surface_filer._directory_for` — use `plural` override

**Files:**
- Modify: `lib/lore_curator/surface_filer.py` — replace `_pluralise` usage with `_directory_for`
- Modify: `tests/test_surface_filer.py` — add directory-override test

- [ ] **Step 1: Write failing test**

Add to `tests/test_surface_filer.py`:

```python
def test_file_surface_uses_plural_override(tmp_path):
    from lore_core.surfaces import SurfaceDef, SurfacesDoc
    from lore_curator.surface_filer import file_surface
    from pathlib import Path
    surface_def = SurfaceDef(
        name="study",
        description="",
        required=["type", "created", "description"],
        optional=[],
        plural="studies",
    )
    doc = SurfacesDoc(schema_version=2, surfaces=[surface_def], path=Path("<test>"))
    filed = file_surface(
        surface_name="study",
        title="My study",
        body="",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
    )
    # Directory must be 'studies/', not 'studys/'
    assert filed.path.parent.name == "studies"


def test_file_surface_defaults_to_pluralise_when_no_override(tmp_path):
    from lore_core.surfaces import SurfaceDef, SurfacesDoc
    from lore_curator.surface_filer import file_surface
    from pathlib import Path
    surface_def = SurfaceDef(
        name="concept",
        description="",
        required=["type", "created", "description"],
        optional=[],
    )
    doc = SurfacesDoc(schema_version=2, surfaces=[surface_def], path=Path("<test>"))
    filed = file_surface(
        surface_name="concept",
        title="X",
        body="",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
    )
    assert filed.path.parent.name == "concepts"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_surface_filer.py -k "plural_override or defaults_to_pluralise" -v`

Expected: `studies` test FAILS (the current `_pluralise` returns `studys`).

- [ ] **Step 3: Replace `_pluralise` call with `_directory_for`**

In `lib/lore_curator/surface_filer.py`:

Keep `_pluralise(name)` as a helper (it's still the fallback). Add **before** `_pluralise`:

```python
def _directory_for(surface_def: SurfaceDef) -> str:
    """Directory name for surfaces of this type — honours the `plural` override."""
    return surface_def.plural or _pluralise(surface_def.name)
```

In `file_surface`, change:

```python
subdir = wiki_root / _pluralise(surface_name)
```

to:

```python
subdir = wiki_root / _directory_for(surface_def)
```

Note: `surface_def` is already resolved earlier in the function via `_find_surface_def`. Ensure `_directory_for` uses it, not `surface_name`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_surface_filer.py -q && pytest -q`

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add lib/lore_curator/surface_filer.py tests/test_surface_filer.py
git commit -m "feat(surface_filer): honour SurfaceDef.plural for directory naming"
```

---

### Task 7: `surface_filer._slug` — support `slug_format` interpolation

**Files:**
- Modify: `lib/lore_curator/surface_filer.py` — extend `_slug` + caller to pass ctx
- Modify: `tests/test_surface_filer.py` — slug_format tests

- [ ] **Step 1: Write failing tests**

Add to `tests/test_surface_filer.py`:

```python
def test_file_surface_uses_slug_format_with_frontmatter_field(tmp_path):
    from lore_core.surfaces import SurfaceDef, SurfacesDoc
    from lore_curator.surface_filer import file_surface
    from pathlib import Path
    surface_def = SurfaceDef(
        name="paper",
        description="",
        required=["type", "citekey", "title", "created", "description"],
        optional=[],
        plural="papers",
        slug_format="{citekey}",
    )
    doc = SurfacesDoc(schema_version=2, surfaces=[surface_def], path=Path("<test>"))
    filed = file_surface(
        surface_name="paper",
        title="ISM structure of galaxies",
        body="",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
        extra_frontmatter={"citekey": "smith2024ism", "title": "ISM structure of galaxies"},
    )
    assert filed.path.name == "smith2024ism.md"


def test_file_surface_slug_format_fallback_when_missing_key(tmp_path):
    from lore_core.surfaces import SurfaceDef, SurfacesDoc
    from lore_curator.surface_filer import file_surface
    from pathlib import Path
    surface_def = SurfaceDef(
        name="paper",
        description="",
        required=["type", "citekey", "created", "description"],
        optional=[],
        slug_format="{citekey}",
    )
    doc = SurfacesDoc(schema_version=2, surfaces=[surface_def], path=Path("<test>"))
    # Don't provide citekey in extras — falls back to title-slug.
    filed = file_surface(
        surface_name="paper",
        title="Untitled paper",
        body="",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
        extra_frontmatter={"citekey": "fallback-key"},  # required, must be present
    )
    assert filed.path.name == "fallback-key.md"


def test_file_surface_no_slug_format_uses_title_slug(tmp_path):
    from lore_core.surfaces import SurfaceDef, SurfacesDoc
    from lore_curator.surface_filer import file_surface
    from pathlib import Path
    surface_def = SurfaceDef(
        name="concept",
        description="",
        required=["type", "created", "description"],
        optional=[],
    )
    doc = SurfacesDoc(schema_version=2, surfaces=[surface_def], path=Path("<test>"))
    filed = file_surface(
        surface_name="concept",
        title="Hot Load Standing Waves",
        body="",
        sources=[],
        wiki_root=tmp_path,
        surfaces_doc=doc,
    )
    assert filed.path.name == "hot-load-standing-waves.md"
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_surface_filer.py -k "slug_format" -v`

Expected: FAIL — slug_format path doesn't exist yet, the file is named from the title slug.

- [ ] **Step 3: Extend `_slug` and wire it through**

In `lib/lore_curator/surface_filer.py`:

Rename the current `_slug(title)` to `_slug_title(title)` (it's only used to slug prose). Then add:

```python
def _slug_for(
    title: str,
    surface_def: SurfaceDef,
    ctx: dict[str, Any],
) -> str:
    """Build the note slug. Honours SurfaceDef.slug_format when set and all
    placeholders resolve from ctx; otherwise falls back to title slug."""
    if surface_def.slug_format:
        try:
            resolved = surface_def.slug_format.format(**ctx)
            if resolved:
                # Clamp to a filesystem-safe slug — same rules as title slug.
                return _slug_title(resolved)
        except (KeyError, IndexError):
            pass
    return _slug_title(title)
```

In `file_surface`, replace:

```python
slug = _slug(title)
```

with:

```python
# Build ctx from extras + frontmatter defaults so {citekey}, {date}, etc. resolve.
slug_ctx: dict[str, Any] = {
    "title": title,
    "slug": _slug_title(title),
    "date": (now or datetime.now(UTC)).date().isoformat(),
}
slug_ctx.update(extra_frontmatter or {})
slug = _slug_for(title, surface_def, slug_ctx)
```

(Move this block to just before the existing `path = subdir / f"{slug}.md"` line.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_surface_filer.py -q && pytest -q`

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add lib/lore_curator/surface_filer.py tests/test_surface_filer.py
git commit -m "feat(surface_filer): slug_format interpolation with ctx fallback to title slug"
```

---

### Task 8: Curator B — include `extract_prompt` in abstraction prompt

**Files:**
- Modify: `lib/lore_curator/abstract.py` — thread `extract_prompt` into prompt builder
- Modify: `tests/test_curator_abstract.py` (or wherever the abstract prompt tests live — grep first)

- [ ] **Step 1: Locate the abstraction prompt builder**

Run: `grep -rn "abstract\|abstract_cluster" lib/lore_curator/ | head`

Identify the function that builds the user-facing prompt string for the LLM in the abstract step. Read it to understand how per-surface information (description, required fields, extract_when) is currently threaded in.

- [ ] **Step 2: Write a failing test**

Add a test to the existing curator abstract test file (path depends on Step 1 — typical name: `tests/test_curator_abstract.py`):

```python
def test_abstract_prompt_includes_extract_prompt_when_set(tmp_path, monkeypatch):
    """When a surface has extract_prompt, it appears in the LLM abstract call."""
    from lore_core.surfaces import SurfaceDef, SurfacesDoc
    from pathlib import Path
    surface_def = SurfaceDef(
        name="paper",
        description="A paper.",
        required=["type", "citekey", "title"],
        optional=[],
        extract_prompt="Prefer citekey over title for slug.",
    )
    doc = SurfacesDoc(schema_version=2, surfaces=[surface_def], path=Path("<test>"))
    # Call the prompt-builder directly (not the LLM). Replace build_abstract_prompt
    # with the actual symbol from Step 1.
    from lore_curator.abstract import build_abstract_prompt
    prompt_text = build_abstract_prompt(
        cluster_notes=[],
        surfaces_doc=doc,
        target_surface="paper",
    )
    assert "Prefer citekey over title for slug." in prompt_text


def test_abstract_prompt_omits_extract_prompt_when_absent():
    """A surface with no extract_prompt does not inject stray text."""
    from lore_core.surfaces import SurfaceDef, SurfacesDoc
    from pathlib import Path
    surface_def = SurfaceDef(
        name="concept",
        description="A concept.",
        required=["type"],
        optional=[],
    )
    doc = SurfacesDoc(schema_version=2, surfaces=[surface_def], path=Path("<test>"))
    from lore_curator.abstract import build_abstract_prompt
    prompt_text = build_abstract_prompt(
        cluster_notes=[],
        surfaces_doc=doc,
        target_surface="concept",
    )
    # No per-surface override marker should appear
    assert "Surface-specific guidance:" not in prompt_text
```

Note: if `build_abstract_prompt` has a different name/shape in the codebase, adapt the test to the real public entry point. The *assertion* — that `extract_prompt` text is included when set — stays the same.

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_curator_abstract.py -k "extract_prompt" -v` (adjust path/file name to match Step 1).

Expected: FAIL — either missing function or the prompt doesn't include the new text.

- [ ] **Step 4: Thread `extract_prompt` into the prompt**

In the abstract prompt builder (identified in Step 1), when assembling the per-surface description block, add:

```python
    if surface_def.extract_prompt:
        parts.append("Surface-specific guidance:")
        parts.append(surface_def.extract_prompt)
        parts.append("")
```

Place this block near the existing description/extract_when rendering so the LLM sees it in context. Use identical indentation / section placement to the existing `extract_when` handling.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_curator_abstract.py -q && pytest -q`

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add lib/lore_curator/abstract.py tests/test_curator_abstract.py
git commit -m "feat(curator): surface extract_prompt threads into abstraction prompt"
```

---

## Phase C — `lore surface commit` CLI

### Task 9: `lore surface commit <draft.json>` — append operation

**Files:**
- Modify: `lib/lore_cli/surface_cmd.py` — add `commit` subcommand (append path only for now)
- Modify: `tests/test_cli_surface.py` — append tests

- [ ] **Step 1: Write failing tests**

Add to `tests/test_cli_surface.py`:

```python
import json


def test_surface_commit_append_on_missing_file(tmp_path, monkeypatch):
    """commit with operation=append creates a minimal file and appends the surface."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "append",
        "surface": {
            "name": "paper",
            "description": "A paper.",
            "required": ["type", "created", "description", "tags"],
            "optional": ["draft"],
            "plural": "papers",
        },
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    result = runner.invoke(app, ["commit", str(draft_path)])
    assert result.exit_code == 0, result.output + result.stderr
    content = (tmp_path / "wiki" / "x" / "SURFACES.md").read_text()
    assert "schema_version: 2" in content
    assert "## paper" in content
    assert "plural: papers" in content


def test_surface_commit_append_rejects_duplicate(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "x"
    _make_surfaces_md(
        wiki_dir,
        "# Surfaces\nschema_version: 2\n\n## paper\nX.\n\n```yaml\nrequired: [type]\noptional: []\n```\n",
    )
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "append",
        "surface": {"name": "paper", "description": "Y.", "required": ["type"], "optional": []},
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    result = runner.invoke(app, ["commit", str(draft_path)])
    assert result.exit_code == 1
    assert "duplicate_name" in result.stderr or "already exists" in result.stderr.lower()


def test_surface_commit_force_overrides_duplicate(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "x"
    _make_surfaces_md(
        wiki_dir,
        "# Surfaces\nschema_version: 2\n\n## paper\nX.\n\n```yaml\nrequired: [type]\noptional: []\n```\n",
    )
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "append",
        "surface": {"name": "paper", "description": "Y (updated).", "required": ["type"], "optional": []},
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    result = runner.invoke(app, ["commit", str(draft_path), "--force"])
    assert result.exit_code == 0, result.output + result.stderr
    # With --force on duplicate append, the command appends the section again;
    # user can hand-clean if they want a replace instead. Document this.
    content = (wiki_dir / "SURFACES.md").read_text()
    assert content.count("## paper") == 2


def test_surface_commit_rejects_invalid_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    draft = {"schema": "wrong/1", "wiki": "x", "operation": "append", "surface": {}}
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    result = runner.invoke(app, ["commit", str(draft_path)])
    assert result.exit_code == 1
    assert "unknown_schema" in result.stderr or "unsupported" in result.stderr.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_surface.py -k "commit" -v`

Expected: FAIL — `commit` subcommand not registered.

- [ ] **Step 3: Implement `commit` append path**

In `lib/lore_cli/surface_cmd.py`, after the `cmd_add` function, add:

```python
@app.command("commit")
def cmd_commit(
    ctx: typer.Context,
    draft_path: Path = typer.Argument(..., help="Path to the draft.json file."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Bypass duplicate/existing-file checks and write anyway.",
    ),
) -> None:
    """Write a surface draft (append or init) to the target wiki's SURFACES.md."""
    from lore_core.surfaces import (
        SurfaceDef,
        render_section,
        render_document,
        validate_draft,
    )

    if not draft_path.exists():
        err_console.print(f"[red]draft file not found: {draft_path}[/red]")
        raise typer.Exit(1)
    try:
        draft = json.loads(draft_path.read_text())
    except json.JSONDecodeError as e:
        err_console.print(f"[red]draft is not valid JSON: {e}[/red]")
        raise typer.Exit(1)

    wiki = draft.get("wiki")
    if not wiki:
        err_console.print("[red]draft.wiki is required[/red]")
        raise typer.Exit(1)
    wiki_dir = _resolve_wiki_dir(wiki)
    wiki_dir.mkdir(parents=True, exist_ok=True)

    issues = validate_draft(draft, wiki_dir=wiki_dir)
    blocking = [
        i for i in issues
        if not (force and i["code"] in {"duplicate_name", "plural_collision"})
    ]
    if blocking:
        for i in blocking:
            err_console.print(f"[red]✗ {i['code']}[/red]: {i['message']}")
        raise typer.Exit(1)

    surfaces_path = wiki_dir / "SURFACES.md"
    op = draft["operation"]
    if op == "append":
        spec = draft["surface"]
        surface_def = SurfaceDef(
            name=spec["name"],
            description=spec.get("description", ""),
            required=list(spec.get("required") or []),
            optional=list(spec.get("optional") or []),
            extract_when=spec.get("extract_when", ""),
            plural=spec.get("plural"),
            slug_format=spec.get("slug_format"),
            extract_prompt=spec.get("extract_prompt"),
        )
        if not surfaces_path.exists():
            atomic_write_text(surfaces_path, _BARE_HEADER)
        text = surfaces_path.read_text()
        if not text.endswith("\n"):
            text += "\n"
        atomic_write_text(surfaces_path, text + "\n" + render_section(surface_def))
        err_console.print(f"[green]committed surface '{surface_def.name}' to {surfaces_path}[/green]")
        print(json.dumps({
            "schema": "lore.surface.commit/1",
            "data": {"operation": "append", "path": str(surfaces_path), "name": surface_def.name},
        }, indent=2))
    elif op == "init":
        # Handled in Task 10.
        err_console.print("[red]init operation not yet implemented[/red]")
        raise typer.Exit(1)
    else:
        err_console.print(f"[red]unknown operation: {op!r}[/red]")
        raise typer.Exit(1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_surface.py -q && pytest -q`

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add lib/lore_cli/surface_cmd.py tests/test_cli_surface.py
git commit -m "feat(cli): lore surface commit <draft.json> — append operation"
```

---

### Task 10: `lore surface commit` — init operation

**Files:**
- Modify: `lib/lore_cli/surface_cmd.py` — wire the init path
- Modify: `tests/test_cli_surface.py` — init tests

- [ ] **Step 1: Write failing tests**

Add to `tests/test_cli_surface.py`:

```python
def test_surface_commit_init_writes_full_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "science",
        "operation": "init",
        "schema_version": 2,
        "surfaces": [
            {"name": "concept", "description": "X.", "required": ["type"], "optional": []},
            {"name": "paper", "description": "Y.", "required": ["type", "citekey"], "optional": [], "plural": "papers", "slug_format": "{citekey}"},
        ],
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    result = runner.invoke(app, ["commit", str(draft_path)])
    assert result.exit_code == 0, result.output + result.stderr
    content = (tmp_path / "wiki" / "science" / "SURFACES.md").read_text()
    assert content.startswith("# Surfaces — science\n")
    assert "## concept" in content
    assert "## paper" in content
    assert "plural: papers" in content


def test_surface_commit_init_refuses_if_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    _make_surfaces_md(tmp_path / "wiki" / "x", "# Surfaces\nschema_version: 2\n")
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "init",
        "schema_version": 2,
        "surfaces": [{"name": "concept", "description": "", "required": ["type"], "optional": []}],
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    result = runner.invoke(app, ["commit", str(draft_path)])
    assert result.exit_code == 1
    assert "already exists" in result.stderr.lower()


def test_surface_commit_init_force_overwrites(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    _make_surfaces_md(tmp_path / "wiki" / "x", "# Surfaces\nschema_version: 2\n\n## old\nO.\n\n```yaml\nrequired: [type]\noptional: []\n```\n")
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "init",
        "schema_version": 2,
        "surfaces": [{"name": "fresh", "description": "F.", "required": ["type"], "optional": []}],
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    result = runner.invoke(app, ["commit", str(draft_path), "--force"])
    assert result.exit_code == 0, result.output + result.stderr
    content = (tmp_path / "wiki" / "x" / "SURFACES.md").read_text()
    assert "## old" not in content
    assert "## fresh" in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_surface.py -k "commit_init" -v`

Expected: FAIL — init path prints "not yet implemented".

- [ ] **Step 3: Implement init path**

In `lib/lore_cli/surface_cmd.py`, replace the `elif op == "init":` block inside `cmd_commit` with:

```python
    elif op == "init":
        if surfaces_path.exists() and not force:
            err_console.print(
                f"[red]SURFACES.md already exists at {surfaces_path} (use --force to overwrite)[/red]"
            )
            raise typer.Exit(1)
        specs = draft.get("surfaces") or []
        surface_defs = [
            SurfaceDef(
                name=s["name"],
                description=s.get("description", ""),
                required=list(s.get("required") or []),
                optional=list(s.get("optional") or []),
                extract_when=s.get("extract_when", ""),
                plural=s.get("plural"),
                slug_format=s.get("slug_format"),
                extract_prompt=s.get("extract_prompt"),
            )
            for s in specs
        ]
        text = render_document(
            schema_version=draft.get("schema_version", 2),
            surfaces=surface_defs,
            wiki=wiki,
        )
        atomic_write_text(surfaces_path, text)
        err_console.print(
            f"[green]initialized {surfaces_path} with {len(surface_defs)} surface(s)[/green]"
        )
        print(json.dumps({
            "schema": "lore.surface.commit/1",
            "data": {
                "operation": "init",
                "path": str(surfaces_path),
                "surfaces": [s.name for s in surface_defs],
            },
        }, indent=2))
```

Also update the `blocking` filter at the top so `existing_file` (hypothetical future code) is force-overridable if needed. Current behavior is fine — validator doesn't add an existing-file issue; the file-exists check lives in the init block above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_surface.py -q && pytest -q`

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add lib/lore_cli/surface_cmd.py tests/test_cli_surface.py
git commit -m "feat(cli): lore surface commit — init operation writes full SURFACES.md"
```

---

### Task 11: Receipt JSON shape — lock the format with a contract test

**Files:**
- Modify: `tests/test_cli_surface.py` — contract test

- [ ] **Step 1: Write the test**

Add to `tests/test_cli_surface.py`:

```python
def test_surface_commit_receipt_shape_append(tmp_path, monkeypatch):
    """The receipt JSON on stdout matches the documented schema."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "append",
        "surface": {"name": "paper", "description": "A.", "required": ["type"], "optional": []},
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    result = runner.invoke(app, ["commit", str(draft_path)])
    assert result.exit_code == 0
    receipt = json.loads(result.stdout)
    assert receipt["schema"] == "lore.surface.commit/1"
    assert receipt["data"]["operation"] == "append"
    assert receipt["data"]["name"] == "paper"
    assert receipt["data"]["path"].endswith("SURFACES.md")


def test_surface_commit_receipt_shape_init(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "init",
        "schema_version": 2,
        "surfaces": [
            {"name": "a", "description": "A.", "required": ["type"], "optional": []},
            {"name": "b", "description": "B.", "required": ["type"], "optional": []},
        ],
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    result = runner.invoke(app, ["commit", str(draft_path)])
    receipt = json.loads(result.stdout)
    assert receipt["schema"] == "lore.surface.commit/1"
    assert receipt["data"]["operation"] == "init"
    assert receipt["data"]["surfaces"] == ["a", "b"]
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_cli_surface.py -k "receipt_shape" -v`

Expected: PASS (receipts already match the shape from Tasks 9-10).

- [ ] **Step 3: Commit**

```bash
git add tests/test_cli_surface.py
git commit -m "test(cli): lock the commit receipt JSON shape"
```

---

## Phase D — MCP tools

### Task 12: `lore_surface_context` handler

**Files:**
- Modify: `lib/lore_mcp/server.py` — add `handle_surface_context`, schema entry, dispatch
- Modify: `tests/test_mcp_server.py` (or create) — handler tests

- [ ] **Step 1: Write failing tests**

Locate existing MCP server tests — `grep -rln "handle_search\|handle_read" tests/` to find the file. Add to that file (or create `tests/test_mcp_surface_tools.py` if no suitable file exists):

```python
import json
from pathlib import Path


def test_surface_context_fresh_wiki(tmp_path, monkeypatch):
    """Fresh wiki — no SURFACES.md, no notes — returns empty collections + templates."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    (tmp_path / "wiki" / "science").mkdir(parents=True)
    from lore_mcp.server import handle_surface_context
    ctx = handle_surface_context(wiki="science")
    assert ctx["schema"] == "lore.surface.context/1"
    assert ctx["wiki"] == "science"
    assert ctx["surfaces_md_exists"] is False
    assert ctx["current_surfaces"] == []
    assert ctx["note_samples"] == {}
    # Templates bundled
    assert "standard" in ctx["shipped_templates"]
    assert "schema_version: 2" in ctx["shipped_templates"]["standard"]


def test_surface_context_with_existing_surfaces_and_notes(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "science"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "SURFACES.md").write_text(
        "# Surfaces\nschema_version: 2\n\n## concept\nA concept.\n\n```yaml\nrequired: [type, created]\noptional: []\n```\n"
    )
    concepts_dir = wiki_dir / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "2026-04-01-alpha.md").write_text(
        "---\ntype: concept\ncreated: 2026-04-01\ndescription: Alpha\n---\nbody\n"
    )
    (concepts_dir / "2026-04-02-beta.md").write_text(
        "---\ntype: concept\ncreated: 2026-04-02\ndescription: Beta\n---\nbody\n"
    )
    from lore_mcp.server import handle_surface_context
    ctx = handle_surface_context(wiki="science")
    assert ctx["surfaces_md_exists"] is True
    assert len(ctx["current_surfaces"]) == 1
    assert ctx["current_surfaces"][0]["name"] == "concept"
    # Samples sorted newest first, limited to ≤3
    assert ctx["note_samples"]["concept"][0].endswith("2026-04-02-beta]]")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp_surface_tools.py -v` (or wherever you placed them).

Expected: `ImportError: cannot import name 'handle_surface_context'`.

- [ ] **Step 3: Implement the handler**

In `lib/lore_mcp/server.py`, add (near the other handlers):

```python
def handle_surface_context(wiki: str) -> dict[str, Any]:
    """Gather context pack for surface-authoring skills."""
    from importlib import resources
    from lore_core.surfaces import load_surfaces
    import yaml

    wiki_dir = _resolve_wiki(wiki)
    if wiki_dir is None:
        return {
            "schema": "lore.surface.context/1",
            "wiki": wiki,
            "error": f"wiki '{wiki}' not found under $LORE_ROOT/wiki/",
        }

    surfaces_path = wiki_dir / "SURFACES.md"
    exists = surfaces_path.exists()
    doc = load_surfaces(wiki_dir) if exists else None
    current = []
    note_samples: dict[str, list[str]] = {}

    if doc is not None:
        for s in doc.surfaces:
            current.append({
                "name": s.name,
                "description": s.description,
                "required": list(s.required),
                "optional": list(s.optional),
                "extract_when": s.extract_when,
                "plural": s.plural,
                "slug_format": s.slug_format,
                "extract_prompt": s.extract_prompt,
            })
            dirname = s.plural or (s.name if s.name.endswith("s") else f"{s.name}s")
            subdir = wiki_dir / dirname
            if not subdir.is_dir():
                continue
            # Sort notes by frontmatter `created` desc; take up to 3.
            samples: list[tuple[str, str]] = []  # (created, stem)
            for md in subdir.glob("*.md"):
                try:
                    txt = md.read_text()
                except OSError:
                    continue
                if not txt.startswith("---\n"):
                    continue
                end = txt.find("\n---\n", 4)
                if end == -1:
                    continue
                try:
                    fm = yaml.safe_load(txt[4:end]) or {}
                except yaml.YAMLError:
                    continue
                created = str(fm.get("created", ""))
                samples.append((created, md.stem))
            samples.sort(reverse=True)
            if samples:
                note_samples[s.name] = [f"[[{stem}]]" for _created, stem in samples[:3]]

    # Bundle templates (exclude 'custom' — placeholder, not inspiration)
    shipped_templates: dict[str, str] = {}
    for tmpl in ("standard", "science", "design"):
        try:
            shipped_templates[tmpl] = (
                resources.files("lore_core.surface_templates")
                .joinpath(f"{tmpl}.md")
                .read_text()
            )
        except (FileNotFoundError, ModuleNotFoundError):
            continue

    # CLAUDE.md attach block (if present). Only the managed "## Lore" section.
    claude_md_attach = ""
    claude_md = wiki_dir / "CLAUDE.md"
    if claude_md.exists():
        txt = claude_md.read_text()
        start = txt.find("## Lore")
        if start != -1:
            end = txt.find("\n## ", start + 1)
            claude_md_attach = txt[start:end] if end != -1 else txt[start:]

    return {
        "schema": "lore.surface.context/1",
        "wiki": wiki,
        "wiki_dir": str(wiki_dir),
        "surfaces_md_exists": exists,
        "current_surfaces": current,
        "claude_md_attach": claude_md_attach,
        "note_samples": note_samples,
        "shipped_templates": shipped_templates,
    }
```

- [ ] **Step 4: Add to schema + dispatch**

In `lib/lore_mcp/server.py`:

In `_tool_schema()` return list, add:

```python
        {
            "name": "lore_surface_context",
            "description": (
                "Gather context for surface-authoring skills: current SURFACES.md, "
                "CLAUDE.md attach block, sampled recent notes per surface, shipped templates."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"wiki": {"type": "string"}},
                "required": ["wiki"],
            },
        },
```

In `_dispatch()` match block, add:

```python
        case "lore_surface_context":
            return handle_surface_context(**args)
```

Also update the module docstring's tools list (lines 7-22) to include `lore_surface_context`.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_mcp_surface_tools.py -v && pytest -q`

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add lib/lore_mcp/server.py tests/test_mcp_surface_tools.py
git commit -m "feat(mcp): lore_surface_context — context pack for surface-authoring skills"
```

---

### Task 13: `lore_surface_validate` handler

**Files:**
- Modify: `lib/lore_mcp/server.py`
- Modify: `tests/test_mcp_surface_tools.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_mcp_surface_tools.py`:

```python
def test_surface_validate_happy_path_append(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "x"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "SURFACES.md").write_text(
        "# Surfaces\nschema_version: 2\n\n## concept\nA.\n\n```yaml\nrequired: [type]\noptional: []\n```\n"
    )
    from lore_mcp.server import handle_surface_validate
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "append",
        "surface": {"name": "paper", "description": "A paper.", "required": ["type"], "optional": []},
    }
    result = handle_surface_validate(wiki="x", draft=draft)
    assert result["schema"] == "lore.surface.validate/1"
    assert result["ok"] is True
    assert result["issues"] == []
    assert "## paper" in result["rendered_markdown"]
    assert "+## paper" in result["diff_preview"]


def test_surface_validate_surfaces_issues(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "x"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "SURFACES.md").write_text(
        "# Surfaces\nschema_version: 2\n\n## paper\nA.\n\n```yaml\nrequired: [type]\noptional: []\n```\n"
    )
    from lore_mcp.server import handle_surface_validate
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "append",
        "surface": {"name": "paper", "description": "dup", "required": ["type"], "optional": []},
    }
    result = handle_surface_validate(wiki="x", draft=draft)
    assert result["ok"] is False
    assert any(i["code"] == "duplicate_name" for i in result["issues"])


def test_surface_validate_init_diff_is_new_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    (tmp_path / "wiki" / "x").mkdir(parents=True)
    from lore_mcp.server import handle_surface_validate
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "x",
        "operation": "init",
        "schema_version": 2,
        "surfaces": [{"name": "a", "description": "A.", "required": ["type"], "optional": []}],
    }
    result = handle_surface_validate(wiki="x", draft=draft)
    assert result["ok"] is True
    # Diff against /dev/null — every line is an addition
    assert result["diff_preview"].count("\n+") >= 3
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_mcp_surface_tools.py -k "surface_validate" -v`

Expected: `ImportError: cannot import name 'handle_surface_validate'`.

- [ ] **Step 3: Implement the handler**

In `lib/lore_mcp/server.py`:

```python
def handle_surface_validate(wiki: str, draft: dict) -> dict[str, Any]:
    """Validate a draft-spec. Returns issues + rendered markdown + unified diff."""
    import difflib
    from lore_core.surfaces import (
        SurfaceDef,
        render_section,
        render_document,
        validate_draft,
    )

    wiki_dir = _resolve_wiki(wiki)
    if wiki_dir is None:
        return {
            "schema": "lore.surface.validate/1",
            "ok": False,
            "issues": [{
                "level": "error",
                "code": "unknown_wiki",
                "message": f"wiki '{wiki}' not found under $LORE_ROOT/wiki/",
            }],
            "rendered_markdown": "",
            "diff_preview": "",
        }

    issues = validate_draft(draft, wiki_dir=wiki_dir)
    ok = not any(i["level"] == "error" for i in issues)

    # Render whatever the draft describes — useful for preview even when invalid.
    rendered = ""
    op = draft.get("operation")
    surfaces_path = wiki_dir / "SURFACES.md"
    current_text = surfaces_path.read_text() if surfaces_path.exists() else ""
    new_text = current_text

    try:
        if op == "append" and isinstance(draft.get("surface"), dict):
            s = draft["surface"]
            sd = SurfaceDef(
                name=s.get("name", ""),
                description=s.get("description", ""),
                required=list(s.get("required") or []),
                optional=list(s.get("optional") or []),
                extract_when=s.get("extract_when", ""),
                plural=s.get("plural"),
                slug_format=s.get("slug_format"),
                extract_prompt=s.get("extract_prompt"),
            )
            rendered = render_section(sd)
            if current_text:
                new_text = current_text.rstrip("\n") + "\n\n" + rendered
            else:
                new_text = "# Surfaces\nschema_version: 2\n\n" + rendered
        elif op == "init" and isinstance(draft.get("surfaces"), list):
            sds = [
                SurfaceDef(
                    name=s.get("name", ""),
                    description=s.get("description", ""),
                    required=list(s.get("required") or []),
                    optional=list(s.get("optional") or []),
                    extract_when=s.get("extract_when", ""),
                    plural=s.get("plural"),
                    slug_format=s.get("slug_format"),
                    extract_prompt=s.get("extract_prompt"),
                )
                for s in draft["surfaces"]
            ]
            new_text = render_document(
                schema_version=draft.get("schema_version", 2),
                surfaces=sds,
                wiki=wiki,
            )
            rendered = new_text
    except Exception as e:
        issues.append({
            "level": "error",
            "code": "render_failed",
            "message": str(e),
        })
        ok = False

    diff_lines = list(difflib.unified_diff(
        current_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile="a/SURFACES.md",
        tofile="b/SURFACES.md",
    ))
    diff_preview = "".join(diff_lines)

    return {
        "schema": "lore.surface.validate/1",
        "wiki": wiki,
        "ok": ok,
        "issues": issues,
        "rendered_markdown": rendered,
        "diff_preview": diff_preview,
    }
```

- [ ] **Step 4: Register in schema + dispatch**

In `_tool_schema()`, add:

```python
        {
            "name": "lore_surface_validate",
            "description": (
                "Validate a surface draft-spec (append or init). Returns structured "
                "issue list + rendered markdown + unified diff preview against the "
                "current SURFACES.md. Never writes."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "wiki": {"type": "string"},
                    "draft": {"type": "object"},
                },
                "required": ["wiki", "draft"],
            },
        },
```

In `_dispatch()`:

```python
        case "lore_surface_validate":
            return handle_surface_validate(**args)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_mcp_surface_tools.py -q && pytest -q`

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add lib/lore_mcp/server.py tests/test_mcp_surface_tools.py
git commit -m "feat(mcp): lore_surface_validate — draft validation + diff preview"
```

---

### Task 14: Update server docstring + README MCP section

**Files:**
- Modify: `lib/lore_mcp/server.py` — docstring (already updated in Task 12 for context; add validate)
- Modify: `README.md` — list the two new MCP tools if the README has a MCP tools list

- [ ] **Step 1: Verify server docstring lists both tools**

Open `lib/lore_mcp/server.py`, check lines 7-22 already list:

```
    lore_surface_context    — gather context pack for surface-authoring skills
    lore_surface_validate   — validate draft-spec + preview diff (no writes)
```

If missing either, add them.

- [ ] **Step 2: Update README MCP section**

Run: `grep -n "MCP" README.md | head` to locate. If there's a "Tools provided by the MCP server" list, add the two new entries. If not, skip this step.

- [ ] **Step 3: Commit**

```bash
git add lib/lore_mcp/server.py README.md
git commit -m "docs: list lore_surface_context / lore_surface_validate in server + README"
```

---

## Phase E — Lint additions

### Task 15: `lore surface lint` validates the new keys

**Files:**
- Modify: `lib/lore_cli/surface_cmd.py` — extend `cmd_lint` to use `validate_draft` on existing SURFACES.md sections
- Modify: `tests/test_cli_surface.py` — new lint cases

- [ ] **Step 1: Write failing tests**

Add to `tests/test_cli_surface.py`:

```python
def test_surface_lint_catches_plural_collision(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "x"
    _make_surfaces_md(
        wiki_dir,
        "# Surfaces\nschema_version: 2\n\n"
        "## paper\nA.\n\n```yaml\nrequired: [type]\noptional: []\nplural: papers\n```\n\n"
        "## study\nB.\n\n```yaml\nrequired: [type]\noptional: []\nplural: papers\n```\n",
    )
    result = runner.invoke(app, ["lint", "--wiki", "x"])
    assert result.exit_code == 1
    assert "plural" in result.stderr.lower()


def test_surface_lint_catches_invalid_slug_format(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "x"
    _make_surfaces_md(
        wiki_dir,
        "# Surfaces\nschema_version: 2\n\n"
        "## paper\nA.\n\n```yaml\nrequired: [type]\noptional: []\nslug_format: \"{nonsense}\"\n```\n",
    )
    result = runner.invoke(app, ["lint", "--wiki", "x"])
    assert result.exit_code == 1
    assert "slug_format" in result.stderr.lower() or "placeholder" in result.stderr.lower()


def test_surface_lint_catches_invalid_plural_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "x"
    _make_surfaces_md(
        wiki_dir,
        "# Surfaces\nschema_version: 2\n\n"
        "## paper\nA.\n\n```yaml\nrequired: [type]\noptional: []\nplural: \"Bad Plural!\"\n```\n",
    )
    result = runner.invoke(app, ["lint", "--wiki", "x"])
    assert result.exit_code == 1
    assert "plural" in result.stderr.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_cli_surface.py -k "lint_catches" -v`

Expected: all three FAIL — current lint doesn't check new-key rules.

- [ ] **Step 3: Extend `cmd_lint`**

In `lib/lore_cli/surface_cmd.py`, replace the body of `cmd_lint` after `doc = load_surfaces(wiki_dir)` (keep the earlier missing-file path) with:

```python
    issues: list[str] = []
    doc = load_surfaces(wiki_dir)
    if doc is None:
        issues.append("file unparseable")
    else:
        from lore_core.surfaces import _surface_spec_issues
        seen_names: set[str] = set()
        seen_plurals: set[str] = set()
        for s in doc.surfaces:
            if s.name in seen_names:
                issues.append(f"duplicate surface name: {s.name}")
            seen_names.add(s.name)
            if not s.required:
                issues.append(f"surface '{s.name}' has no `required:` list (no YAML block?)")
            # Run the shared validator on each surface to cover new-key rules.
            spec = {
                "name": s.name,
                "description": s.description,
                "required": list(s.required),
                "optional": list(s.optional),
                "extract_when": s.extract_when,
                "plural": s.plural,
                "slug_format": s.slug_format,
                "extract_prompt": s.extract_prompt,
            }
            # Treat already-seen plurals (from earlier sections this loop)
            # as "existing" so we catch intra-file collisions.
            for sub in _surface_spec_issues(
                spec, existing_names=set(), existing_plurals=seen_plurals
            ):
                # Skip the "duplicate_name" emitted by the validator — we
                # already track names above with a friendlier message.
                if sub["code"] == "duplicate_name":
                    continue
                issues.append(f"surface '{s.name}': {sub['message']}")
            effective_plural = s.plural or (s.name if s.name.endswith("s") else f"{s.name}s")
            seen_plurals.add(effective_plural)
    if issues:
        for line in issues:
            err_console.print(f"[red]✗[/red] {line}")
        raise typer.Exit(1)
    err_console.print(f"[green]SURFACES.md OK ({len(doc.surfaces)} surfaces)[/green]")
```

Note: this imports `_surface_spec_issues` from `lore_core.surfaces`. That function is public-ish (used across modules); consider renaming to `surface_spec_issues` (drop underscore) if linting flags it.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_cli_surface.py -q && pytest -q`

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add lib/lore_cli/surface_cmd.py lib/lore_core/surfaces.py tests/test_cli_surface.py
git commit -m "feat(lint): surface lint catches plural collision / invalid plural / invalid slug_format"
```

---

## Phase F — CLI launchers

### Task 16: Rewrite `lore surface add` as a skill launcher

**Files:**
- Modify: `lib/lore_cli/surface_cmd.py` — replace `cmd_add` body with an exec shim
- Modify: `tests/test_cli_surface.py` — replace old append-behavior tests with launcher tests

- [ ] **Step 1: Stage the breaking-change tests**

**Remove** all tests that exercise the current interactive `lore surface add <name>` behavior:

```
test_surface_add_creates_bare_surfaces_md_when_missing
test_surface_add_creates_surfaces_md_when_missing_new_surface
test_surface_add_appends_section_to_existing_file
test_surface_add_rejects_duplicate_name
```

Append coverage for append behavior now lives under `test_surface_commit_append_*` (Task 9). The launcher tests below cover `add`.

Add new launcher tests:

```python
def test_surface_add_launcher_execs_claude_with_skill_and_wiki(tmp_path, monkeypatch):
    """`lore surface add --wiki X` exec's `claude "/lore:surface-new X"`."""
    import os
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    # Install a shim that records the command and exits 0.
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    record_file = tmp_path / "claude-invocation.txt"
    shim = shim_dir / "claude"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$@\" > {record_file}\n"
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ['PATH']}")
    result = runner.invoke(app, ["add", "--wiki", "science"])
    assert result.exit_code == 0, result.output + result.stderr
    assert record_file.read_text().strip() == "/lore:surface-new science"


def test_surface_add_launcher_missing_claude_prints_helpful_error(tmp_path, monkeypatch):
    """If `claude` is not on PATH, exit 1 with an install pointer."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path))  # no claude here
    result = runner.invoke(app, ["add", "--wiki", "science"])
    assert result.exit_code == 1
    assert "claude" in result.stderr.lower()
    assert "install" in result.stderr.lower() or "path" in result.stderr.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_cli_surface.py -k "launcher" -v`

Expected: FAIL — current `add` doesn't exec claude.

- [ ] **Step 3: Replace `cmd_add` + add launcher helper**

In `lib/lore_cli/surface_cmd.py`, replace the entire `cmd_add` function body with:

```python
@app.command("add")
def cmd_add(
    ctx: typer.Context,
    wiki: str | None = typer.Option(None, "--wiki", help="Wiki name. Overrides group-level --wiki."),
) -> None:
    """Drop into the /lore:surface-new skill to author a new surface interactively."""
    wiki = wiki or (ctx.obj or {}).get("wiki")
    wiki_dir = _resolve_wiki_dir(wiki)
    wiki_name = wiki_dir.name
    _launch_claude_skill(f"/lore:surface-new {wiki_name}")


def _launch_claude_skill(slash_command: str) -> None:
    """Launch the `claude` CLI with a slash command as the initial message.

    Uses subprocess (not os.execv) so the launcher is testable through
    Typer's CliRunner. Subscription cost / interactivity is identical.
    """
    import shutil
    import subprocess
    claude_bin = shutil.which("claude")
    if claude_bin is None:
        err_console.print(
            "[red]`claude` is not on PATH. Install Claude Code "
            "(https://claude.com/code) to use the interactive authoring "
            "flow, or write a draft and call `lore surface commit "
            "<draft.json>` directly.[/red]"
        )
        raise typer.Exit(1)
    try:
        result = subprocess.run([claude_bin, slash_command], check=False)
    except OSError as e:
        err_console.print(f"[red]failed to launch claude: {e}[/red]")
        raise typer.Exit(1)
    if result.returncode != 0:
        raise typer.Exit(result.returncode)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_cli_surface.py -q && pytest -q`

Expected: all green. Confirm the shim receives `/lore:surface-new science`.

- [ ] **Step 5: Commit**

```bash
git add lib/lore_cli/surface_cmd.py tests/test_cli_surface.py
git commit -m "feat(cli): lore surface add launches /lore:surface-new via claude (BREAKING)"
```

---

### Task 17: Rewrite `lore surface init` as a skill launcher

**Files:**
- Modify: `lib/lore_cli/surface_cmd.py` — replace `cmd_init` body
- Modify: `tests/test_cli_surface.py` — launcher tests for init; remove old init tests

- [ ] **Step 1: Stage breaking-change tests**

**Remove** tests for the current `lore surface init --template` interactive behavior:

```
test_surface_init_seeds_from_standard_template
test_surface_init_uses_template_option
test_surface_init_refuses_to_overwrite
test_surface_init_force_overwrites
test_surface_init_unknown_template_rejected
```

(They covered pre-skill init. The template seed path moves entirely to `lore new-wiki --surfaces` and, for standalone init of an existing wiki, to the skill.)

Add:

```python
def test_surface_init_launcher_execs_claude(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    record = tmp_path / "claude-args.txt"
    shim = shim_dir / "claude"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$@\" > {record}\n"
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ['PATH']}")
    result = runner.invoke(app, ["init", "--wiki", "science"])
    assert result.exit_code == 0, result.output + result.stderr
    assert record.read_text().strip() == "/lore:surface-init science"
```

- [ ] **Step 2: Replace `cmd_init`**

In `lib/lore_cli/surface_cmd.py`, replace the entire `cmd_init` function body with:

```python
@app.command("init")
def cmd_init(
    ctx: typer.Context,
    wiki: str | None = typer.Option(None, "--wiki", help="Wiki name. Overrides group-level --wiki."),
) -> None:
    """Drop into the /lore:surface-init skill to design the wiki's SURFACES.md set."""
    wiki = wiki or (ctx.obj or {}).get("wiki")
    wiki_dir = _resolve_wiki_dir(wiki)
    wiki_name = wiki_dir.name
    _launch_claude_skill(f"/lore:surface-init {wiki_name}")
```

- [ ] **Step 3: Also clean up now-dead helpers**

If `_load_template` and `TEMPLATE_NAMES` become unused in `surface_cmd.py` (they are — `new-wiki` has its own copies), leave them in place *only if* `handle_surface_context` in the MCP still imports them from this module. It doesn't (it uses `importlib.resources` directly). **Delete** `_load_template`, `TEMPLATE_NAMES`, and the now-unused `from importlib import resources` import from `surface_cmd.py`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_cli_surface.py -q && pytest -q`

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add lib/lore_cli/surface_cmd.py tests/test_cli_surface.py
git commit -m "feat(cli): lore surface init launches /lore:surface-init via claude (BREAKING)"
```

---

## Phase G — Skills

### Task 18: `/lore:surface-new` skill

**Files:**
- Create: `skills/surface-new/SKILL.md`

- [ ] **Step 1: Write the skill**

Create `skills/surface-new/SKILL.md`:

````markdown
---
name: lore:surface-new
description: Add a new surface to a wiki's SURFACES.md via an LLM-guided
  conversation. Proposes a full draft from one open question, allows
  per-field deepening, commits via `lore surface commit <draft.json>`.
  Run with "/lore:surface-new <wiki>".
user_invocable: true
---

# Surface Authoring — add one surface

Guide the user through adding a new surface to `$LORE_ROOT/wiki/<wiki>/SURFACES.md`. One open question, synthesis-first, optional per-field deepening, hybrid commit.

## Arguments

`/lore:surface-new <wiki>` — the positional is the wiki name (e.g. `science`).

If the wiki name is missing, ask the user once before starting.

## Step 1 — Gather context (silent)

Call the MCP tool `lore_surface_context(wiki=<wiki>)`. You will get:

- `current_surfaces` — already-declared surfaces
- `claude_md_attach` — the wiki's CLAUDE.md `## Lore` block (what the wiki is for)
- `note_samples` — wikilinks to ~3 recent notes per existing type
- `shipped_templates` — `standard`, `science`, `design` template text for inspiration

Read all of it. Do not show it to the user directly.

## Step 2 — Open the conversation

Ask **one** question:

> "Describe the new surface in your own words — what does it capture, and when should Curator extract one?"

(User-facing term is "Curator" — do not say "Curator B".)

If the user asks you to run a semantic scan of the wiki first, call `lore_search` with their description as the query, present the top 5 hits as a compact list, and ask if any cluster looks like it would fit this surface before continuing.

## Step 3 — Synthesize a full draft

From the user's answer + the context pack, produce a **complete** surface spec:

- `name` — lowercase ASCII identifier, `^[a-z][a-z0-9_]*$`
- `description` — one-sentence prose
- `required` — list; always starts with `type, created, description, tags` unless there's a reason to drop one
- `optional` — list (`draft` is usually present)
- `extract_when` — short prose hint for Curator
- `plural` — only if `<name>s` would be wrong (e.g. `study` → `studies`)
- `slug_format` — only if the default `{date}-{slug}` wouldn't suit (e.g. `{citekey}` for papers)
- `extract_prompt` — only if you need to tell Curator something the description doesn't

Before presenting: run a **semantic-overlap check** against `current_surfaces`. If the new surface sounds like an existing one, say so explicitly and propose extending the existing surface instead. Let the user decide.

Build a draft-spec JSON:

```json
{
  "schema": "lore.surface.draft/1",
  "wiki": "<wiki>",
  "operation": "append",
  "surface": { ... }
}
```

Call `lore_surface_validate(wiki=<wiki>, draft=<draft>)`. If it returns issues, revise the draft until clean — do **not** surface validation noise to the user; fix it silently and try again (max 2 retries; if still broken, report the issue honestly).

## Step 4 — Present

Show the user:

- The rendered `## <name>` section (from `rendered_markdown`)
- A compact summary of the diff (how SURFACES.md will change)
- Any overlap notes from Step 3

Ask:

> "Commit this, deepen a specific field, or save as draft?"

## Step 5 — Branch

**Commit:**
1. Write the draft to a temp file: `$TMPDIR/lore-surface-<timestamp>.json`.
2. Run `lore surface commit <path>` via the Bash tool.
3. Report the receipt JSON path + the new surface's wikilink.

**Deepen:**
1. Ask the user which field to tune. Accept free text.
2. For that field, ask a focused question (e.g. for `required`: "Any required fields beyond type/created/description/tags?").
3. Update the draft, re-validate, return to Step 4.

**Save as draft:**
1. Write to `$LORE_ROOT/drafts/surfaces/<wiki>-<name>.json`.
2. Print: *"Saved. Commit later with `lore surface commit <path>`."*
3. Stop.

## Error handling

- MCP server not reachable → say so honestly, stop. Do not fake a context pack.
- Validation keeps failing → surface the issue codes verbatim + ask the user how they want to adjust.
- Commit exits non-zero → show the receipt stderr and stop; do not retry automatically.

## What you do NOT do

- Do not edit SURFACES.md directly. The commit CLI is the only write path.
- Do not invent surface fields the user didn't ask for (e.g. don't add `citekey` to `required` unless the user described a paper-like thing).
- Do not mention "Curator A/B/C" — always just "Curator".
- Do not offer to rename or remove existing surfaces — that's a separate (future) flow.
````

- [ ] **Step 2: Commit**

```bash
git add skills/surface-new/SKILL.md
git commit -m "feat(skills): /lore:surface-new — LLM-assisted single-surface authoring"
```

---

### Task 19: `/lore:surface-init` skill

**Files:**
- Create: `skills/surface-init/SKILL.md`

- [ ] **Step 1: Write the skill**

Create `skills/surface-init/SKILL.md`:

````markdown
---
name: lore:surface-init
description: Design a wiki's full SURFACES.md set in one conversation. Holistic
  vocabulary design from one open question, optional per-surface deepening,
  writes via `lore surface commit <draft.json>`. Run with
  "/lore:surface-init <wiki>".
user_invocable: true
---

# Surface Authoring — design the full set

Guide the user through designing `$LORE_ROOT/wiki/<wiki>/SURFACES.md` from scratch. Produces a coherent vocabulary of 3-6 surfaces in one synthesis, with optional per-surface editing.

## Arguments

`/lore:surface-init <wiki>` — the positional is the wiki name.

## Step 1 — Gather context (silent)

Call `lore_surface_context(wiki=<wiki>)`.

- If `surfaces_md_exists` is `true`, warn the user: *"SURFACES.md already exists at `<path>`. Running `/lore:surface-init` will replace it (with `--force` on commit). Continue?"* — stop on `no`.

## Step 2 — Open the conversation

Ask **one** question:

> "What's this wiki for, and what kinds of things do you want to capture? A rough list or free-text description — either works."

## Step 3 — Synthesize the full set

Produce a **complete** SURFACES.md draft: 3-6 surfaces, internally consistent:

- No semantic overlap between surfaces (`decision` and `choice` don't both exist).
- Consistent naming register (all imperative-nouns, or all agent-role nouns — pick a lane).
- Consistent field schemas — `type, created, description, tags` appear in `required` for every surface unless there's a reason to drop.
- Always include a `session` surface (Curator writes session notes; the wiki needs a slot for them).
- Use `plural`, `slug_format`, `extract_prompt` only where they add real value — don't sprinkle them everywhere.

Consult `shipped_templates` for inspiration — do **not** pick one wholesale; build a set tailored to what the user described.

Build a draft-spec JSON:

```json
{
  "schema": "lore.surface.draft/1",
  "wiki": "<wiki>",
  "operation": "init",
  "schema_version": 2,
  "surfaces": [ ... ]
}
```

Call `lore_surface_validate(wiki=<wiki>, draft=<draft>)`. Revise silently on issues; report honestly if stuck.

## Step 4 — Present

Show the user:

- The rendered full SURFACES.md
- A one-line-per-surface summary ("`concept` — ideas that recur across sessions", etc.)

Ask:

> "Commit this, refine one surface, or save as draft?"

## Step 5 — Branch

**Commit:**
1. Write draft to `$TMPDIR/lore-surface-init-<timestamp>.json`.
2. Run `lore surface commit <path>` (add `--force` if SURFACES.md already exists and the user agreed in Step 1).
3. Report receipt.

**Refine one surface:**
1. Ask which surface.
2. Run a mini-loop like `/lore:surface-new` for that one surface only (open question → synthesize → validate → present just that section → accept or deepen).
3. Update the surface inside the init draft; preserve all others unchanged.
4. Return to Step 4.

**Save as draft:**
1. Write to `$LORE_ROOT/drafts/surfaces/<wiki>-init.json`.
2. Print the commit command.
3. Stop.

## Error handling, rules

Same as `/lore:surface-new`:

- Never edit SURFACES.md directly.
- Never say "Curator B"; say "Curator".
- If MCP is unreachable, stop honestly — do not fake.
- If validation keeps failing after 2 retries, show codes + ask the user.
````

- [ ] **Step 2: Commit**

```bash
git add skills/surface-init/SKILL.md
git commit -m "feat(skills): /lore:surface-init — LLM-assisted full-vocabulary authoring"
```

---

## Phase H — Docs + integration

### Task 20: README surface-authoring section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the surfaces section**

Open `README.md`. Locate the existing surfaces paragraph (near "SURFACES.md is human-editable markdown").

Replace with a short section that covers the three user paths:

```markdown
## Surface authoring

Surfaces (`SURFACES.md`) declare the note vocabulary of a wiki — what types exist, what fields they require, how Curator should extract them.

Three user paths:

- **Design a new wiki's vocabulary (interactive):** `lore surface init --wiki <name>` — drops into `/lore:surface-init` in Claude. Guided holistic design.
- **Add one new surface (interactive):** `lore surface add --wiki <name>` — drops into `/lore:surface-new`. Proposes a full draft from one open question.
- **Scripted / automation:** write a `draft.json` (schema: `lore.surface.draft/1`) by hand, then `lore surface commit <path>`. Or for a fresh wiki, `lore new-wiki <name> --surfaces <standard|science|design>` seeds from a shipped template.

Both interactive flows require `claude` on PATH (Claude Code). The CLI commit primitive does not.

See `docs/superpowers/specs/2026-04-20-surface-authoring-design.md` for details.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(readme): surface authoring section — three user paths"
```

---

### Task 21: End-to-end integration test

**Files:**
- Create: `tests/test_surface_authoring_e2e.py`

- [ ] **Step 1: Write the test**

Create `tests/test_surface_authoring_e2e.py`:

```python
"""End-to-end: draft JSON → MCP validate → CLI commit → SURFACES.md on disk.

Skips the LLM / skill conversation — exercises the deterministic bottom half
of the pipeline that everything else depends on.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lore_cli.surface_cmd import app
from lore_mcp.server import handle_surface_context, handle_surface_validate

runner = CliRunner(mix_stderr=False)


def test_flow_a_append_end_to_end(tmp_path, monkeypatch):
    """Simulate flow A: validate → commit → resulting SURFACES.md parses clean."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    wiki_dir = tmp_path / "wiki" / "science"
    wiki_dir.mkdir(parents=True)
    # Seed with an existing surface so the append is non-trivial.
    (wiki_dir / "SURFACES.md").write_text(
        "# Surfaces — science\nschema_version: 2\n\n"
        "## concept\nA concept.\n\n```yaml\nrequired: [type, created, description, tags]\noptional: [draft]\n```\n"
    )
    # 1. Context tool returns the wiki state
    ctx = handle_surface_context(wiki="science")
    assert [s["name"] for s in ctx["current_surfaces"]] == ["concept"]

    # 2. Build a draft
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "science",
        "operation": "append",
        "surface": {
            "name": "paper",
            "description": "Citekey-named publication note.",
            "required": ["type", "created", "description", "tags", "citekey"],
            "optional": ["draft"],
            "extract_when": "a paper is discussed with concrete findings",
            "plural": "papers",
            "slug_format": "{citekey}",
            "extract_prompt": "Prefer citekey over title for slug.",
        },
    }

    # 3. Validate tool says OK
    result = handle_surface_validate(wiki="science", draft=draft)
    assert result["ok"] is True, result["issues"]

    # 4. Commit via CLI
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    cli = runner.invoke(app, ["commit", str(draft_path)])
    assert cli.exit_code == 0, cli.output + cli.stderr

    # 5. Resulting SURFACES.md parses cleanly and contains both surfaces
    from lore_core.surfaces import load_surfaces
    doc = load_surfaces(wiki_dir)
    assert doc is not None
    assert [s.name for s in doc.surfaces] == ["concept", "paper"]
    paper = doc.surfaces[1]
    assert paper.plural == "papers"
    assert paper.slug_format == "{citekey}"
    assert "citekey" in paper.extract_prompt


def test_flow_b_init_end_to_end(tmp_path, monkeypatch):
    """Simulate flow B: validate → commit → fresh SURFACES.md parses clean."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    (tmp_path / "wiki" / "science").mkdir(parents=True)
    draft = {
        "schema": "lore.surface.draft/1",
        "wiki": "science",
        "operation": "init",
        "schema_version": 2,
        "surfaces": [
            {"name": "concept", "description": "Cross-cutting idea.",
             "required": ["type", "created", "description", "tags"], "optional": ["draft"]},
            {"name": "decision", "description": "Trade-off made.",
             "required": ["type", "created", "description", "tags"], "optional": ["superseded_by"]},
            {"name": "session", "description": "Curator session log.",
             "required": ["type", "created", "description"], "optional": ["scope", "tags"]},
            {"name": "paper", "description": "Publication.",
             "required": ["type", "created", "description", "tags", "citekey"], "optional": ["draft"],
             "plural": "papers", "slug_format": "{citekey}"},
        ],
    }
    val = handle_surface_validate(wiki="science", draft=draft)
    assert val["ok"] is True, val["issues"]

    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    cli = runner.invoke(app, ["commit", str(draft_path)])
    assert cli.exit_code == 0, cli.output + cli.stderr

    from lore_core.surfaces import load_surfaces
    doc = load_surfaces(tmp_path / "wiki" / "science")
    assert [s.name for s in doc.surfaces] == ["concept", "decision", "session", "paper"]
```

- [ ] **Step 2: Run**

Run: `pytest tests/test_surface_authoring_e2e.py -v`

Expected: all green.

- [ ] **Step 3: Full-suite sanity**

Run: `pytest -q`

Expected: green. Note the new passing count and include it in the commit message.

- [ ] **Step 4: Commit**

```bash
git add tests/test_surface_authoring_e2e.py
git commit -m "test(e2e): draft → validate → commit → parse round-trip for both flows"
```

---

## Post-plan checklist

After all 21 tasks land:

- [ ] `pytest -q` — green.
- [ ] `lore surface --help` shows `add`, `init`, `commit`, `lint` (no stray old options).
- [ ] `lore surface add --wiki x` actually execs claude with `/lore:surface-new x` (manual check).
- [ ] `lore surface commit <a hand-written draft.json>` works without Claude on PATH (scripted path preserved).
- [ ] `lore new-wiki <name> --surfaces standard` still creates a seeded wiki (automation path preserved).
- [ ] `skills/surface-new/SKILL.md` and `skills/surface-init/SKILL.md` exist with correct `name:` frontmatter (`lore:surface-new`, `lore:surface-init`).
- [ ] Memory entries still accurate — no stale references to removed `_DEFAULT_STANDARD`.

Follow-up specs to write (out of scope here):

- Surface rename / remove with existing-notes migration.
- Deep lint cross-checks (declared surfaces ↔ on-disk directories ↔ existing note `type:` values).
- Unified `--json` flag across all surface subcommands.
