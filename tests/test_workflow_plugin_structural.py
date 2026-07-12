"""Structural gates for the `lore-workflow` plugin (PRD 0003 / epic #161).

Ported from `ccat-agent-workflow`'s `tests/validate.py`, scoped to
`lore-workflow/` instead of a whole-repo layout, and updated for this repo's
rewires: skills delegate tiers via `lore tier resolve` (not a bundled
MODEL-TIERS.md), and the shared boilerplate that used to repeat across five
skills now lives in one `TIER-DELEGATION.md`.

Each check appends to a shared failure list rather than asserting inline, so
a single test run reports every problem at once — mirroring the source
validator's design.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = REPO_ROOT / "lore-workflow"
PLUGIN_MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
SKILLS_ROOT = PLUGIN_ROOT / "skills"
SKILLS_GLOB = "skills/*/SKILL.md"
TIER_DELEGATION_DOC = PLUGIN_ROOT / "TIER-DELEGATION.md"
MODEL_TIERS_DOC = REPO_ROOT / "docs" / "model-tiers.md"

REQUIRED_PLUGIN_FIELDS = ("name", "version", "description", "author")
REQUIRED_SKILL_FRONTMATTER = ("name", "description")
KEBAB_CASE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Every ported skill must carry the plugin's slash-command prefix.
EXPECTED_SKILL_NAMES = {
    "ccat-workflow-init",
    "debug",
    "document-epic",
    "domain-modeling",
    "grilling",
    "implement-issue",
    "orchestrate-epic",
    "orient",
    "seed-epic",
    "tdd",
    "to-epic",
}

GRILLING_SKILL_FILES = {
    "grilling": ("SKILL.md",),
    "domain-modeling": ("SKILL.md", "CONTEXT-FORMAT.md", "ADR-FORMAT.md"),
}

DOCUMENT_EPIC_SKILL = SKILLS_ROOT / "document-epic" / "SKILL.md"

# No skill under lore-workflow may name a concrete model — tiers are resolved
# via `lore tier resolve`, never hardcoded.
BANNED_MODEL_NAME_SUBSTRINGS = (
    "opus",
    "sonnet",
    "haiku",
    "fable",
    "mythos",
    "claude-",
    "gpt-",
    "gemini",
)

SEMANTIC_TIERS = ("frontier", "strong", "mid", "cheap")

# Scripts that used to ship beside a skill; superseded by `lore` CLI/MCP
# surface. A reference to any of these under skills/ is stale.
BANNED_SCRIPT_REFERENCES = (
    "roadmap_validator.py",
    "prd_docs.py",
    "diataxis.py",
    "code_map.py",
    "ccat_workflow_init.py",
)

SKILL_LINE_BUDGET = 150
SKILL_LINE_BUDGET_WAIVERS: dict[str, int] = {
    "orchestrate-epic": 300,
    "to-epic": 220,
}

_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_TIER_REF_HYPHEN = re.compile(r"\b([a-z]+)-tier(?![a-z])")
_TIER_REF_ASSIGN = re.compile(r"\btier:\s*`?([a-z]+)`?")


def _parse_frontmatter(text: str) -> dict[str, str] | None:
    if not text.startswith("---"):
        return None
    match = re.match(r"^---\s*\n(.*?)\n---\s*(\n|$)", text, re.DOTALL)
    if not match:
        return None
    data: dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.rstrip()
        if not line or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip().strip("'\"")
    return data


def _skill_files() -> list[Path]:
    return sorted(PLUGIN_ROOT.glob(SKILLS_GLOB))


def _skill_dirs() -> list[Path]:
    return sorted(p for p in SKILLS_ROOT.iterdir() if p.is_dir())


def _all_skill_files() -> list[Path]:
    return sorted(p for p in SKILLS_ROOT.rglob("*") if p.is_file())


def test_plugin_manifest_required_fields() -> None:
    manifest = json.loads(PLUGIN_MANIFEST.read_text())
    missing = [f for f in REQUIRED_PLUGIN_FIELDS if not manifest.get(f)]
    assert not missing, f"plugin.json missing required field(s): {missing}"
    assert KEBAB_CASE.match(manifest["name"]), (
        f"plugin.json name not kebab-case: {manifest['name']}"
    )


def test_every_expected_skill_ships() -> None:
    present = {p.parent.name for p in _skill_files()}
    assert present == EXPECTED_SKILL_NAMES, (
        f"skill set mismatch — missing: {EXPECTED_SKILL_NAMES - present}, "
        f"unexpected: {present - EXPECTED_SKILL_NAMES}"
    )


@pytest.mark.parametrize("skill_path", _skill_files(), ids=lambda p: p.parent.name)
def test_skill_frontmatter_valid(skill_path: Path) -> None:
    frontmatter = _parse_frontmatter(skill_path.read_text(encoding="utf-8"))
    assert frontmatter is not None, f"{skill_path}: missing or unparseable YAML frontmatter"
    for field in REQUIRED_SKILL_FRONTMATTER:
        assert frontmatter.get(field), f"{skill_path}: frontmatter missing '{field}'"


@pytest.mark.parametrize("skill_path", _skill_files(), ids=lambda p: p.parent.name)
def test_skill_name_carries_plugin_prefix(skill_path: Path) -> None:
    frontmatter = _parse_frontmatter(skill_path.read_text(encoding="utf-8"))
    name = frontmatter["name"] if frontmatter else ""
    assert name == f"lore-workflow:{skill_path.parent.name}", (
        f"{skill_path}: name '{name}' must be 'lore-workflow:{skill_path.parent.name}'"
    )


def test_no_legacy_skill_install_path() -> None:
    offenders = []
    for path in _all_skill_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if ".claude/skills" in text:
            offenders.append(path.relative_to(REPO_ROOT))
    assert not offenders, f"references to legacy ~/.claude/skills path: {offenders}"


def test_skill_relative_references_resolve() -> None:
    failures = []
    for path in sorted(SKILLS_ROOT.rglob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        in_fence = False
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for target in _MARKDOWN_LINK.findall(line):
                target = target.strip()
                if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                if "<" in target or ">" in target:
                    continue
                target_path = target.split("#", 1)[0].strip()
                if not target_path:
                    continue
                resolved = (path.parent / target_path).resolve()
                if not resolved.exists():
                    failures.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno}: '{target}' -> {resolved}"
                    )
    assert not failures, "broken relative references:\n  " + "\n  ".join(failures)


def test_document_epic_skill_contract() -> None:
    text = DOCUMENT_EPIC_SKILL.read_text().lower()
    for quadrant in ("tutorial", "how-to", "reference", "explanation"):
        assert quadrant in text, f"document-epic must mention Diátaxis quadrant '{quadrant}'"
    for protected in ("docs/prd", "docs/adr"):
        assert protected in text, f"document-epic must state the never-edit rule for '{protected}'"
    assert "docstring" in text, "document-epic reference handling must mention docstrings"
    assert "autosummary" in text or "toctree" in text, (
        "document-epic reference handling must mention autosummary/toctree wiring"
    )


def test_grilling_subsystem_cohesion() -> None:
    for skill, files in GRILLING_SKILL_FILES.items():
        for filename in files:
            path = SKILLS_ROOT / skill / filename
            assert path.exists(), f"missing required file: {path.relative_to(REPO_ROOT)}"
    grilling_text = (SKILLS_ROOT / "grilling" / "SKILL.md").read_text()
    frontmatter = _parse_frontmatter(grilling_text)
    description = frontmatter["description"].lower()
    for phrase in ("grill me", "grill with docs", "grill"):
        assert phrase in description, f"grilling description must carry trigger phrase '{phrase}'"
    assert "domain-modeling" in grilling_text, (
        "grilling must reference 'domain-modeling' for its doc-context mode"
    )


def test_no_hardcoded_model_names() -> None:
    failures = []
    for path in _all_skill_files():
        lowered = path.read_text(encoding="utf-8", errors="replace").lower()
        for banned in BANNED_MODEL_NAME_SUBSTRINGS:
            if banned in lowered:
                failures.append(f"{path.relative_to(REPO_ROOT)}: hardcoded model name '{banned}'")
    assert not failures, (
        "skills must name a semantic tier (see TIER-DELEGATION.md / docs/model-tiers.md), "
        "not a concrete model:\n  " + "\n  ".join(failures)
    )


def test_no_stale_script_references() -> None:
    failures = []
    for path in _all_skill_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for banned in BANNED_SCRIPT_REFERENCES:
            if banned in text:
                failures.append(f"{path.relative_to(REPO_ROOT)}: stale reference to '{banned}'")
    assert not failures, (
        "skills must call the lore CLI/MCP surface, not a skill-local script:\n  "
        + "\n  ".join(failures)
    )


def test_skill_tiers_are_defined() -> None:
    failures = []
    for path in _all_skill_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        referenced = set(_TIER_REF_HYPHEN.findall(text)) | set(_TIER_REF_ASSIGN.findall(text))
        for tier in sorted(referenced - set(SEMANTIC_TIERS)):
            failures.append(f"{path.relative_to(REPO_ROOT)}: undefined tier '{tier}'")
    assert not failures, (
        f"tiers referenced under skills/ must be one of {SEMANTIC_TIERS}:\n  "
        + "\n  ".join(failures)
    )


def test_tier_delegation_doc_states_the_rules() -> None:
    assert TIER_DELEGATION_DOC.exists(), "missing lore-workflow/TIER-DELEGATION.md"
    text = TIER_DELEGATION_DOC.read_text().lower()
    assert "lore tier resolve" in text
    assert "implicit" in text and "inherit" in text, (
        "TIER-DELEGATION.md must state the no-implicit-inherit rule"
    )
    for tier in SEMANTIC_TIERS:
        assert tier in text, f"TIER-DELEGATION.md must name tier '{tier}'"


def test_model_tiers_doc_covers_ordinal_fallback_cheap_rules() -> None:
    """The shared lore-level tier doc (docs/model-tiers.md) is what
    TIER-DELEGATION.md points readers at for the full contract; assert it
    still states the rules the source repo's MODEL-TIERS.md carried."""
    text = MODEL_TIERS_DOC.read_text().lower()
    assert "ordinal" in text and "collapse" in text
    assert "fallback" in text
    assert "bulk-mechanical" in text or "bulk mechanical" in text


def test_readme_skill_table_matches_shipped_skills() -> None:
    readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
    row = re.compile(r"^\|\s*`([a-z0-9]+(?:-[a-z0-9]+)*)`\s*\|", re.M)
    documented = set(row.findall(readme))
    actual = {p.name for p in _skill_dirs()}
    assert documented == actual, (
        f"README skill table out of sync — missing: {actual - documented}, "
        f"extra: {documented - actual}"
    )


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=lambda p: p.name)
def test_skill_line_budget(skill_dir: Path) -> None:
    cap = SKILL_LINE_BUDGET_WAIVERS.get(skill_dir.name, SKILL_LINE_BUDGET)
    n_lines = len((skill_dir / "SKILL.md").read_text(encoding="utf-8").splitlines())
    assert n_lines <= cap, (
        f"{skill_dir.name}/SKILL.md: {n_lines} lines exceeds its {cap}-line budget"
    )
