"""The note lifecycle and the hygiene curator are retired (issue #596)."""

from __future__ import annotations

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


def test_check_ignores_the_retired_fields(tmp_path, monkeypatch):
    from lore_core.wiki_check import check_wiki

    wiki = tmp_path / "wiki" / "demo"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / "old.md").write_text(OLD)
    (wiki / "concepts" / "aged.md").write_text(AGED)
    monkeypatch.setattr("lore_core.wiki_check.shutil.which", lambda _: None)
    findings, _ = check_wiki(wiki)
    assert findings == []


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
