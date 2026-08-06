"""`lore status` — unified health dashboard (issue #193).

Six sections render from live state (capture, wikis, flags, retention,
news, alerts). Golden --plain section tests + exit-code-mirrors-alerts +
--offline note are the heart of the suite. Every row the panel prints
has a writer; issue #377 removed the ones that lost theirs.

Output is plain text (built line-by-line and printed) — no ANSI — so
these golden asserts are robust to the CI color environment (no TTY under
CliRunner). Times are deterministic via the _LORE_STATUS_NOW env pin.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lore_cli.status_cmd import app
from typer.testing import CliRunner

runner = CliRunner()

_NOW = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Vault seeding
# ---------------------------------------------------------------------------


def _seed_vault(tmp_path: Path) -> tuple[Path, Path]:
    """Returns (lore_root, attached_project_dir)."""
    from lore_core.state.attachments import Attachment, AttachmentsFile

    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    (lore_root / "wiki" / "private" / "sessions").mkdir(parents=True)

    project = tmp_path / "project"
    project.mkdir()
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(
        Attachment(
            path=project,
            wiki="private",
            scope="proj:test",
            attached_at=datetime.now(UTC),
            source="manual",
        )
    )
    af.save()
    return lore_root, project


def _seed_hook_event(lore_root: Path, *, ago: timedelta, outcome: str = "captured") -> None:
    from lore_core.spine import SpineWriter

    SpineWriter(lore_root).emit(
        source="hook",
        event="session-end",
        data={"outcome": outcome, "ts": _iso(_NOW - ago)},
    )


def _seed_janitor(lore_root: Path, *, ago: timedelta, failed: int = 0) -> None:
    (lore_root / ".lore" / "janitor-status.json").write_text(
        json.dumps(
            {
                "last_run_at": _iso(_NOW - ago),
                "hot_bytes": 2048,
                "cold_bytes": 0,
                "deleted": 0,
                "failed": failed,
            }
        )
    )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t.co", "-c", "user.name=t", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _make_git_wiki(
    lore_root: Path, name: str, *, remote: bool, dirty: bool, ahead: int = 0
) -> None:
    wiki = lore_root / "wiki" / name
    wiki.mkdir(parents=True, exist_ok=True)
    _git(wiki, "init", "-q")
    _git(wiki, "checkout", "-q", "-b", "main")
    (wiki / "a.md").write_text("seed\n")
    _git(wiki, "add", "-A")
    _git(wiki, "commit", "-qm", "init")
    if remote:
        bare = lore_root.parent / f"{name}-origin.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
        _git(wiki, "remote", "add", "origin", str(bare))
        _git(wiki, "push", "-q", "-u", "origin", "main")
    for i in range(ahead):
        (wiki / f"ahead{i}.md").write_text("x\n")
        _git(wiki, "add", "-A")
        _git(wiki, "commit", "-qm", f"ahead{i}")
    if dirty:
        (wiki / "a.md").write_text("uncommitted change\n")


def _invoke(lore_root: Path, cwd: Path | None, *extra: str, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    monkeypatch.setenv("_LORE_STATUS_NOW", _iso(_NOW))
    args = list(extra)
    if cwd is not None:
        args += ["--cwd", str(cwd)]
    return runner.invoke(app, args, catch_exceptions=False)


# ---------------------------------------------------------------------------
# Tracer bullet — the six-section golden dashboard on a healthy vault
# ---------------------------------------------------------------------------


def test_dashboard_renders_six_sections(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_hook_event(lore_root, ago=timedelta(hours=2))

    result = _invoke(lore_root, project, "--plain", monkeypatch=monkeypatch)
    out = result.output

    for header in ("capture", "wikis", "flags", "retention", "news", "alerts"):
        assert f"\n{header}\n" in out or out.startswith(header + "\n") or f"\n{header} " in out, (
            f"missing section header {header!r} in:\n{out}"
        )
    # Capture liveness lines — every row here has a writer.
    for label in ("Hook", "Session"):
        assert label in out, f"missing capture line {label!r} in:\n{out}"
    # Flags counts line — empty vault, nothing written yet.
    assert "written 0 · pending 0 · accepted 0 · declined 0" in out
    # No ANSI escapes leaked.
    assert "\x1b[" not in out


def test_dashboard_healthy_exits_zero(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_hook_event(lore_root, ago=timedelta(hours=2))

    result = _invoke(lore_root, project, monkeypatch=monkeypatch)
    assert result.exit_code == 0
    assert "alerts" in result.output


# ---------------------------------------------------------------------------
# Flags section (#360) — per-wiki flag counters
# ---------------------------------------------------------------------------


def _emit_flag(lore_root: Path, *, event: str, wiki: str = "private", **data) -> None:
    from lore_core import flag
    from lore_core.spine import SpineWriter

    SpineWriter(lore_root).emit(source=flag.SPINE_SOURCE, event=event, wiki=wiki, data=data)


def test_flags_section_shows_written_accepted_declined(tmp_path: Path, monkeypatch) -> None:
    from lore_core import flag

    lore_root, project = _seed_vault(tmp_path)
    _seed_hook_event(lore_root, ago=timedelta(hours=2))
    _emit_flag(lore_root, event=flag.EV_WRITE, outcome="written", flag_id="a")
    _emit_flag(lore_root, event=flag.EV_WRITE, outcome="written", flag_id="b")
    _emit_flag(lore_root, event=flag.EV_REVIEW, verdict="accept", flag_id="a")
    _emit_flag(lore_root, event=flag.EV_REVIEW, verdict="decline", flag_id="b")

    out = _invoke(lore_root, project, monkeypatch=monkeypatch).output
    assert "flags" in out
    assert "written 2 · pending 0 · accepted 1 · declined 1" in out


def test_flags_section_shows_withheld_apart_from_written(tmp_path: Path, monkeypatch) -> None:
    from lore_core import flag

    lore_root, project = _seed_vault(tmp_path)
    _seed_hook_event(lore_root, ago=timedelta(hours=2))
    _emit_flag(lore_root, event=flag.EV_WRITE, outcome="written", flag_id="a")
    _emit_flag(lore_root, event=flag.EV_WRITE, outcome="withheld", flag_id="b", category="email")

    out = _invoke(lore_root, project, monkeypatch=monkeypatch).output
    assert "written 1" in out
    assert "1 withheld" in out


def test_flags_pending_reflects_note_scan(tmp_path: Path, monkeypatch) -> None:
    from lore_core import flag

    lore_root, project = _seed_vault(tmp_path)
    _seed_hook_event(lore_root, ago=timedelta(hours=2))
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    (lore_root / "wiki" / "private" / "concepts").mkdir(parents=True)
    written = flag.write(
        "One fact.",
        wiki="private",
        target="concepts/topic.md",
        transcript="tr-1",
        author="claude",
        now="2026-08-05",
    )
    flag.write(
        "Another fact.",
        wiki="private",
        target="concepts/topic.md",
        transcript="tr-2",
        author="claude",
        now="2026-08-05",
    )
    flag.accept(lore_root / "wiki" / "private", written.flag_id)

    out = _invoke(lore_root, project, monkeypatch=monkeypatch).output
    assert "pending 1" in out


# ---------------------------------------------------------------------------
# Exit code mirrors alerts (AC5)
# ---------------------------------------------------------------------------


def test_exit_nonzero_when_alerts_exist(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_hook_event(lore_root, ago=timedelta(hours=1))
    (lore_root / ".lore" / "spine-failed.marker").touch()

    result = _invoke(lore_root, project, monkeypatch=monkeypatch)
    assert result.exit_code != 0
    assert "spine write" in result.output.lower()


# ---------------------------------------------------------------------------
# Wiki connection health (AC2)
# ---------------------------------------------------------------------------


def test_wiki_health_clean_dirty_remote(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_hook_event(lore_root, ago=timedelta(hours=2))
    # Replace the plain "private" dir with a healthy git wiki + a broken one.
    _make_git_wiki(lore_root, "team", remote=True, dirty=False)
    _make_git_wiki(lore_root, "solo", remote=False, dirty=True)

    out = _invoke(lore_root, project, monkeypatch=monkeypatch).output
    assert "team" in out
    assert "solo" in out
    assert "clean" in out
    assert "dirty" in out
    assert "ahead 0" in out
    assert "behind 0" in out
    # A local-bare remote is reachable without --offline.
    assert "reachable" in out


def test_wiki_health_offline_note(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_hook_event(lore_root, ago=timedelta(hours=2))
    _make_git_wiki(lore_root, "team", remote=True, dirty=False)

    out = _invoke(lore_root, project, "--offline", monkeypatch=monkeypatch).output
    assert "(offline)" in out
    # --offline must not have run a network probe → no reachability verdict.
    assert "reachable" not in out
    assert "unreachable" not in out


def test_wiki_health_ahead_count(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_hook_event(lore_root, ago=timedelta(hours=2))
    _make_git_wiki(lore_root, "team", remote=True, dirty=False, ahead=2)

    out = _invoke(lore_root, project, "--offline", monkeypatch=monkeypatch).output
    assert "ahead 2" in out


# ---------------------------------------------------------------------------
# Retention section (AC1 retention, consumes #190)
# ---------------------------------------------------------------------------


def test_retention_shows_usage_and_last_run(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_hook_event(lore_root, ago=timedelta(hours=2))
    _seed_janitor(lore_root, ago=timedelta(minutes=5))

    out = _invoke(lore_root, project, monkeypatch=monkeypatch).output
    assert "retention" in out
    assert "MB" in out
    assert "5m ago" in out


def test_retention_janitor_never_run(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_hook_event(lore_root, ago=timedelta(hours=2))

    out = _invoke(lore_root, project, monkeypatch=monkeypatch).output
    assert "never run" in out


def test_retention_failed_deletions_alert(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_hook_event(lore_root, ago=timedelta(hours=2))
    _seed_janitor(lore_root, ago=timedelta(minutes=5), failed=2)

    result = _invoke(lore_root, project, monkeypatch=monkeypatch)
    assert "failed" in result.output.lower()
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# News section — absorbs `lore news`, cursor advance preserved (AC4)
# ---------------------------------------------------------------------------


def test_news_section_shows_event_and_advances_cursor(tmp_path: Path, monkeypatch) -> None:
    from lore_core.drain import DrainStore

    lore_root, project = _seed_vault(tmp_path)
    _seed_hook_event(lore_root, ago=timedelta(hours=2))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-1")
    DrainStore(lore_root, "sess-1").emit("transcript-synced", wiki="private", wikilink="[[hello]]")

    out = _invoke(lore_root, project, monkeypatch=monkeypatch).output
    assert "news" in out
    assert "transcript synced" in out
    # Cursor advanced so the event is surfaced once (news semantics).
    cur = DrainStore(lore_root, "sess-1").read_cursor()
    assert cur is not None


def test_news_nothing_new(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_hook_event(lore_root, ago=timedelta(hours=2))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-empty")

    out = _invoke(lore_root, project, monkeypatch=monkeypatch).output
    assert "nothing new" in out


# ---------------------------------------------------------------------------
# Preserved v1 semantics — alerts still earned
# ---------------------------------------------------------------------------


def test_simple_tier_fallback_alert(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_hook_event(lore_root, ago=timedelta(hours=1))
    (lore_root / ".lore" / "warnings.log").write_text("simple-tier-fallback\n")

    out = _invoke(lore_root, project, monkeypatch=monkeypatch).output
    assert "simple-tier" in out.lower() or "simple tier" in out.lower()


# ---------------------------------------------------------------------------
# Unattached cwd — preserved guidance copy
# ---------------------------------------------------------------------------


def test_unattached_cwd(tmp_path: Path, monkeypatch) -> None:
    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    (lore_root / "wiki" / "private").mkdir(parents=True)
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()

    result = _invoke(lore_root, unrelated, monkeypatch=monkeypatch)
    assert "not attached here" in result.output
    assert "/lore:attach" in result.output
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# --json mode
# ---------------------------------------------------------------------------


def test_json_mode(tmp_path: Path, monkeypatch) -> None:
    lore_root, project = _seed_vault(tmp_path)
    _seed_hook_event(lore_root, ago=timedelta(hours=2))

    out = _invoke(lore_root, project, "--json", monkeypatch=monkeypatch).output
    data = json.loads(out)
    assert data["scope_name"] == "private/proj:test"
    assert "curators" not in data
    assert "flushes" not in data
    assert "retention" in data
    assert "flags" in data
    assert data["flags"]["private"]["written"] == 0


def test_help_mentions_status() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "status" in result.output.lower() or "health" in result.output.lower()
