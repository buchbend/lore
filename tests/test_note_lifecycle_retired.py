"""The note lifecycle and the hygiene curator are retired (issue #596)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

REPO = Path(__file__).resolve().parent.parent

# A concept note that holds every retired field and no `last_reviewed`.
OLD = (
    "---\nschema_version: 2\ntype: concept\ncreated: 2020-01-01\n"
    "description: old note\ntags: []\ndraft: true\n"
    'superseded_by: "[[newer]]"\n---\nbody\n'
)
AGED = OLD.replace("draft: true\n", "last_reviewed: 2020-01-01\n")


def _lint(tmp_path: Path, monkeypatch, body: str):
    from lore_core.lint import _parse_note, _regenerate_wiki, check_frontmatter

    wiki = tmp_path / "wiki" / "demo"
    (wiki / "concepts").mkdir(parents=True)
    note_path = wiki / "concepts" / "old.md"
    note_path.write_text(body)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    note = _parse_note(note_path, wiki, "demo")
    _regenerate_wiki(wiki, [note], [])
    return wiki, note, check_frontmatter


def test_catalog_entry_holds_no_lifecycle_key(tmp_path, monkeypatch):
    wiki, _, _ = _lint(tmp_path, monkeypatch, OLD)
    text = (wiki / "_catalog.json").read_text()
    entries = [e for sec in json.loads(text)["sections"].values() for e in sec]
    assert entries
    for key in ("status", "lifecycle", "superseded_by"):
        assert all(key not in e for e in entries)


def test_index_and_collections_hold_no_badge(tmp_path, monkeypatch):
    wiki, _, _ = _lint(tmp_path, monkeypatch, OLD)
    for name in ("_index.txt", "_concepts.txt"):
        text = (wiki / name).read_text()
        assert "old" in text
        assert "DRAFT" not in text and "SUPERSEDED" not in text


def test_lint_has_no_age_rule_and_needs_no_last_reviewed(tmp_path, monkeypatch):
    import lore_core.lint as lint
    from lore_core.schema import REQUIRED_FIELDS

    assert not hasattr(lint, "check_staleness")
    assert all("last_reviewed" not in fields for fields in REQUIRED_FIELDS.values())
    _lint(tmp_path, monkeypatch, AGED)
    report = lint.run_lint(wiki_filter="demo", check_only=True)
    text = json.dumps(report, default=str)
    assert '"stale"' not in text and "last_reviewed" not in text


def test_no_lifecycle_function():
    import lore_core.schema as schema

    assert not hasattr(schema, "compute_lifecycle")


def test_curate_command_and_minimal_status_option_are_unknown():
    from lore_cli.__main__ import app

    result = CliRunner().invoke(app, ["wiki", "curate"])
    assert result.exit_code == 2 and "No such command" in result.output
    result = CliRunner().invoke(app, ["migrate", "--minimal-status"])
    assert result.exit_code == 2 and "No such option" in result.output


def test_repo_ships_no_hygiene_curator():
    assert not (REPO / "skills" / "curator").exists()
    assert not (REPO / "lib" / "lore_curator" / "hygiene.py").exists()
    assert not (REPO / "examples" / "wiki-curator-on-push.yml").exists()
    assert not (REPO / "lib" / "lore_core" / "run_log.py").exists()


def test_capture_state_reads_no_curator_run():
    from lore_core.capture_state import CaptureState

    assert not any(f.startswith("last_run_") for f in CaptureState.__dataclass_fields__)


def test_wiki_template_names_no_retired_field():
    text = (REPO / "lib" / "lore_core" / "templates" / "wiki-CLAUDE.md").read_text()
    for word in ("draft:", "superseded_by", "last_reviewed", "180", "curator"):
        assert word not in text
