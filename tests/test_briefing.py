"""Tests for `lore_core.briefing` and the `lore briefing` CLI."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest
from lore_cli import briefing_cmd
from lore_core.briefing import gather, mark_incorporated, render_briefing
from lore_core.linkage import Linkage
from lore_core.note_document import Chapter, TopicBlock, append_chapter, close_note, create_note


@pytest.fixture
def briefing_vault(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "ccat"
    (wiki / "sessions").mkdir(parents=True)

    def write_session(name: str, lead: str, decision: str = "") -> None:
        """Write a real new-shape note: disclaimer + one chapter + one block."""
        path = wiki / "sessions" / f"{name}.md"
        create_note(
            path,
            title=name[11:],
            description=f"session {name}",
            scope="lore:test",
            created=name[:10],
        )
        append_chapter(
            path,
            Chapter(blocks=[TopicBlock(lead=lead, body=decision, anchor_turn=12)]),
            slice_from_turn=1,
            slice_to_turn=12,
        )
        close_note(path)

    write_session("2026-04-15-fix-a", "Did the A thing.")
    write_session("2026-04-16-fix-b", "Did the B thing.", "Chose option Z because Y.")
    write_session("2026-04-17-fix-c", "Did the C thing.")

    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    return vault_root, wiki


def test_gather_returns_all_sessions_when_ledger_missing(briefing_vault):
    result = gather(wiki="ccat")
    assert "error" not in result
    assert result["wiki"] == "ccat"
    assert len(result["new_sessions"]) == 3
    assert result["ledger"]["last_briefing"] is None
    assert result["ledger"]["incorporated_count"] == 0


def test_gather_filters_by_ledger(briefing_vault):
    _, wiki = briefing_vault
    (wiki / ".briefing-ledger.json").write_text(
        json.dumps({"last_briefing": "2026-04-16", "incorporated": ["2026-04-15-fix-a.md"]})
    )
    result = gather(wiki="ccat")
    assert len(result["new_sessions"]) == 2
    slugs = [s["slug"] for s in result["new_sessions"]]
    assert "fix-a" not in slugs
    assert "fix-b" in slugs
    assert "fix-c" in slugs


def test_gather_filters_by_since_date(briefing_vault):
    result = gather(wiki="ccat", since="2026-04-17")
    assert len(result["new_sessions"]) == 1
    assert result["new_sessions"][0]["slug"] == "fix-c"


def test_gather_includes_full_body(briefing_vault):
    """Chapter-aware gather: the whole note body (disclaimer + chapters +
    topic blocks) is handed over — there is no H2 structure to split on
    in the new note shape, so full text is the only faithful extraction."""
    result = gather(wiki="ccat")
    s = next(s for s in result["new_sessions"] if s["slug"] == "fix-b")
    assert "Lab-notebook session note" in s["body"]  # disclaimer travels too
    assert "lore:chapter 1" in s["body"]
    assert "Did the B thing." in s["body"]
    assert "Chose option Z because Y." in s["body"]


def test_gather_no_body_when_disabled(briefing_vault):
    result = gather(wiki="ccat", include_body_sections=False)
    s = result["new_sessions"][0]
    assert "body" not in s


def test_gather_unknown_wiki(briefing_vault):
    result = gather(wiki="nonexistent")
    assert "error" in result


def test_mark_incorporated_writes_ledger(briefing_vault):
    _, wiki = briefing_vault
    result = mark_incorporated(
        wiki="ccat",
        session_paths=["2026-04-15-fix-a.md", "2026-04-16-fix-b.md"],
    )
    assert result["incorporated_count"] == 2
    assert result["last_briefing"] is not None
    ledger = json.loads((wiki / ".briefing-ledger.json").read_text())
    assert "2026-04-15-fix-a.md" in ledger["incorporated"]
    assert "2026-04-16-fix-b.md" in ledger["incorporated"]


def test_mark_incorporated_idempotent(briefing_vault):
    _, _ = briefing_vault
    mark_incorporated(wiki="ccat", session_paths=["2026-04-15-fix-a.md"])
    result = mark_incorporated(wiki="ccat", session_paths=["2026-04-15-fix-a.md"])
    # No new additions on second call
    assert result["added"] == []
    assert result["incorporated_count"] == 1


def test_cli_gather_emits_envelope(briefing_vault, capsys):
    rc = briefing_cmd.main(["gather", "--wiki", "ccat"])
    assert rc == 0
    out = capsys.readouterr().out
    envelope = json.loads(out)
    assert envelope["schema"] == "lore.briefing.gather/1"
    assert envelope["data"]["wiki"] == "ccat"


def test_cli_publish_markdown_sink(briefing_vault, tmp_path, capsys, monkeypatch):
    """Publish a briefing via the markdown sink to a target file."""
    out_path = tmp_path / "out.md"
    monkeypatch.setattr(
        "sys.stdin",
        type(
            "S",
            (),
            {
                "read": staticmethod(lambda: "## Briefing\n\nbody\n"),
                "isatty": staticmethod(lambda: False),
            },
        )(),
    )
    rc = briefing_cmd.main(
        ["publish", "--sink", "markdown", "--out", str(out_path), "--json"]
    )
    assert rc == 0
    assert out_path.exists()
    assert "Briefing" in out_path.read_text()


def test_cli_mark_writes_and_emits(briefing_vault, capsys):
    rc = briefing_cmd.main(
        ["mark", "--wiki", "ccat", "--session", "2026-04-15-fix-a.md"]
    )
    assert rc == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["schema"] == "lore.briefing.mark/1"
    assert envelope["data"]["incorporated_count"] == 1


# ---------------------------------------------------------------------------
# Deterministic formatter
# ---------------------------------------------------------------------------


def test_render_briefing_empty_returns_empty_string():
    assert render_briefing({"new_sessions": []}) == ""


def test_render_briefing_uses_summary_frontmatter():
    result = {
        "wiki": "ccat",
        "today": "2026-04-29",
        "ledger": {"last_briefing": "2026-04-28", "incorporated_count": 0},
        "new_sessions": [
            {
                "path": "sessions/2026-04-29-thing.md",
                "date": "2026-04-29",
                "slug": "thing",
                "frontmatter": {"summary": "shipped the thing"},
                "body": "**Shipped the thing.**\n\n@12",
            }
        ],
    }
    out = render_briefing(result)
    assert "# Briefing — 2026-04-29 · ccat" in out
    assert "1 session since 2026-04-28." in out
    assert "## 2026-04-29" in out
    assert "- **thing** — shipped the thing" in out


def test_render_briefing_falls_back_to_description():
    """No `summary` frontmatter: falls straight to `description` — the new
    note shape has no H2 sections to mine a bullet from, so there is no
    intermediate tier between summary and description."""
    result = {
        "wiki": "ccat",
        "today": "2026-04-29",
        "ledger": {"last_briefing": None, "incorporated_count": 0},
        "new_sessions": [
            {
                "path": "p",
                "date": "2026-04-29",
                "slug": "thing",
                "frontmatter": {"description": "fallback line"},
                "body": "**Did the thing.**\n\n@12",
            }
        ],
    }
    out = render_briefing(result)
    assert "- **thing** — fallback line" in out
    assert "since the start" in out


def test_render_briefing_groups_by_date_descending():
    result = {
        "wiki": "ccat",
        "today": "2026-04-29",
        "ledger": {"last_briefing": None, "incorporated_count": 0},
        "new_sessions": [
            {
                "path": "p1",
                "date": "2026-04-15",
                "slug": "older",
                "frontmatter": {"summary": "older work"},
                "body": "",
            },
            {
                "path": "p2",
                "date": "2026-04-29",
                "slug": "newer",
                "frontmatter": {"summary": "newer work"},
                "body": "",
            },
        ],
    }
    out = render_briefing(result)
    assert "2 sessions since the start." in out
    # Newer date comes first.
    assert out.index("## 2026-04-29") < out.index("## 2026-04-15")


# ---------------------------------------------------------------------------
# One-shot `lore briefing --wiki ...`
# ---------------------------------------------------------------------------


def _write_briefing_yaml(
    wiki: Path,
    sink: str = "markdown",
    target_path: str | None = None,
) -> None:
    body = f"sink: {sink}\n"
    if target_path is not None and sink == "markdown":
        body += f"markdown:\n  path: {target_path}\n"
    (wiki / ".lore-briefing.yml").write_text(body)


def test_oneshot_publishes_and_marks(briefing_vault, tmp_path, capsys):
    _, wiki = briefing_vault
    out_path = tmp_path / "out.md"
    _write_briefing_yaml(wiki, target_path=str(out_path))
    rc = briefing_cmd.main(["--wiki", "ccat"])
    assert rc == 0
    assert out_path.exists()
    text = out_path.read_text()
    assert "# Briefing" in text
    assert "fix-a" in text
    # Ledger updated for all 3 sessions.
    ledger = json.loads((wiki / ".briefing-ledger.json").read_text())
    assert len(ledger["incorporated"]) == 3
    assert ledger["last_briefing"] is not None


def test_oneshot_no_new_sessions_exits_zero(briefing_vault, capsys):
    _, wiki = briefing_vault
    _write_briefing_yaml(wiki, target_path="/tmp/never.md")
    # Pre-populate ledger with everything.
    (wiki / ".briefing-ledger.json").write_text(
        json.dumps(
            {
                "last_briefing": "2026-04-28",
                "incorporated": [
                    "2026-04-15-fix-a.md",
                    "2026-04-16-fix-b.md",
                    "2026-04-17-fix-c.md",
                ],
            }
        )
    )
    rc = briefing_cmd.main(["--wiki", "ccat"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "no new sessions" in err.lower()


def test_oneshot_dry_run_skips_publish_and_mark(briefing_vault, tmp_path, capsys):
    _, wiki = briefing_vault
    out_path = tmp_path / "out.md"
    _write_briefing_yaml(wiki, target_path=str(out_path))
    rc = briefing_cmd.main(["--wiki", "ccat", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "# Briefing" in out
    assert not out_path.exists()
    assert not (wiki / ".briefing-ledger.json").exists()


def test_oneshot_no_mark_publishes_without_ledger_write(
    briefing_vault, tmp_path, capsys
):
    _, wiki = briefing_vault
    out_path = tmp_path / "out.md"
    _write_briefing_yaml(wiki, target_path=str(out_path))
    rc = briefing_cmd.main(["--wiki", "ccat", "--no-mark"])
    assert rc == 0
    assert out_path.exists()
    assert not (wiki / ".briefing-ledger.json").exists()


def test_oneshot_sink_override(briefing_vault, tmp_path):
    _, wiki = briefing_vault
    out_path = tmp_path / "override.md"
    # Yaml says markdown without target; override URI supplies the target.
    _write_briefing_yaml(wiki, sink="markdown")
    rc = briefing_cmd.main(
        ["--wiki", "ccat", "--sink", f"markdown:{out_path}"]
    )
    assert rc == 0
    assert out_path.exists()


def test_oneshot_missing_yaml_errors(briefing_vault, capsys):
    rc = briefing_cmd.main(["--wiki", "ccat"])
    assert rc == 1
    err = capsys.readouterr().err
    assert ".lore-briefing.yml" in err


def test_oneshot_no_args_shows_help(briefing_vault, capsys):
    rc = briefing_cmd.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "briefing" in out.lower()


# ---------------------------------------------------------------------------
# Sharded session layout (team-mode): sessions/[<handle>/]YYYY/MM/DD-HHMM-slug.md
# ---------------------------------------------------------------------------


@pytest.fixture
def sharded_briefing_vault(tmp_path, monkeypatch):
    """Vault with team-mode sharded session paths under a handle."""
    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "ccat"
    sessions = wiki / "sessions" / "buchbend" / "2026" / "04"
    sessions.mkdir(parents=True)

    def write(name: str, summary: str) -> None:
        path = sessions / f"{name}.md"
        create_note(
            path,
            title=summary,
            description=summary,
            scope="lore:test",
            extra_frontmatter={"summary": summary},
        )
        append_chapter(
            path,
            Chapter(blocks=[TopicBlock(lead="Did things.", anchor_turn=5)]),
            slice_from_turn=1,
            slice_to_turn=5,
        )

    write("15-0900-fix-a", "fixed A")
    write("16-1200-fix-b", "fixed B")
    write("17-1535-fix-c", "fixed C")

    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    return vault_root, wiki


def test_gather_finds_sharded_sessions(sharded_briefing_vault):
    result = gather(wiki="ccat")
    assert "error" not in result
    assert len(result["new_sessions"]) == 3
    dates = sorted(s["date"] for s in result["new_sessions"])
    assert dates == ["2026-04-15", "2026-04-16", "2026-04-17"]
    slugs = sorted(s["slug"] for s in result["new_sessions"])
    assert slugs == ["fix-a", "fix-b", "fix-c"]


def test_gather_sharded_filters_by_since(sharded_briefing_vault):
    result = gather(wiki="ccat", since="2026-04-17")
    assert len(result["new_sessions"]) == 1
    assert result["new_sessions"][0]["slug"] == "fix-c"


def test_gather_sharded_filters_by_ledger(sharded_briefing_vault):
    _, wiki = sharded_briefing_vault
    (wiki / ".briefing-ledger.json").write_text(
        json.dumps(
            {
                "last_briefing": "2026-04-16",
                "incorporated": ["15-0900-fix-a.md"],
            }
        )
    )
    result = gather(wiki="ccat")
    slugs = sorted(s["slug"] for s in result["new_sessions"])
    assert slugs == ["fix-b", "fix-c"]


def test_gather_sharded_without_handle(tmp_path, monkeypatch):
    """sessions/YYYY/MM/DD-HHMM-slug.md (no handle) should also work."""
    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "demo"
    sessions = wiki / "sessions" / "2026" / "04"
    sessions.mkdir(parents=True)
    path = sessions / "29-1100-thing.md"
    create_note(
        path,
        title="thing",
        description="did the thing",
        scope="lore:test",
        extra_frontmatter={"summary": "did the thing"},
    )
    append_chapter(
        path,
        Chapter(blocks=[TopicBlock(lead="Did the thing.", anchor_turn=3)]),
        slice_from_turn=1,
        slice_to_turn=3,
    )
    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    result = gather(wiki="demo")
    assert "error" not in result
    assert len(result["new_sessions"]) == 1
    s = result["new_sessions"][0]
    assert s["date"] == "2026-04-29"
    assert s["slug"] == "thing"


# ---------------------------------------------------------------------------
# LLM-composed briefing prose
# ---------------------------------------------------------------------------


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, *, text: str = "", raises: Exception | None = None) -> None:
        self._text = text
        self._raises = raises
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return _FakeResp(self._text)


class _FakeClient:
    def __init__(self, *, text: str = "", raises: Exception | None = None) -> None:
        self.messages = _FakeMessages(text=text, raises=raises)


def test_compose_briefing_prose_returns_llm_text():
    from lore_core.briefing import compose_briefing_prose

    fake = _FakeClient(text="## Briefing: 2026-04-29 (ccat)\n\n### What happened\n- shipped\n")
    out = compose_briefing_prose(
        gather_result={
            "wiki": "ccat",
            "today": "2026-04-29",
            "ledger": {"last_briefing": None},
            "new_sessions": [
                {
                    "path": "p",
                    "date": "2026-04-29",
                    "slug": "thing",
                    "frontmatter": {"summary": "shipped the thing"},
                    "body": "**Shipped the thing.**\n\nWired up the last piece.\n\n@12",
                }
            ],
        },
        llm_client=fake,
        model_resolver=lambda t: "claude-sonnet-4-6",
    )
    assert "Briefing" in out
    assert fake.messages.calls[0]["model"] == "claude-sonnet-4-6"
    prompt = fake.messages.calls[0]["messages"][0]["content"]
    assert "shipped the thing" in prompt


def test_compose_briefing_prompt_includes_linkage():
    """Author + epic ride into the prompt so the LLM can key digests on them."""
    from lore_core.briefing import compose_briefing_prose

    fake = _FakeClient(text="## Briefing: 2026-04-29 (ccat)\n")
    compose_briefing_prose(
        gather_result={
            "wiki": "ccat",
            "today": "2026-04-29",
            "ledger": {"last_briefing": None},
            "new_sessions": [
                {
                    "path": "p",
                    "date": "2026-04-29",
                    "slug": "thing",
                    "frontmatter": {"summary": "shipped thing"},
                    "linkage": {"author": "Alice", "epics": [162]},
                    "body": "",
                }
            ],
        },
        llm_client=fake,
        model_resolver=lambda t: "claude-sonnet-4-6",
    )
    prompt = fake.messages.calls[0]["messages"][0]["content"]
    assert "Alice" in prompt
    assert "epic #162" in prompt


def test_compose_briefing_prose_empty_input_short_circuits():
    from lore_core.briefing import compose_briefing_prose

    fake = _FakeClient(text="should-not-appear")
    out = compose_briefing_prose(
        gather_result={"new_sessions": []},
        llm_client=fake,
        model_resolver=lambda t: "claude-sonnet-4-6",
    )
    assert out == ""
    assert fake.messages.calls == []


def test_oneshot_uses_llm_prose_when_client_available(
    briefing_vault, tmp_path, monkeypatch
):
    """LLM client returns prose; CLI publishes it instead of the deterministic render."""
    _, wiki = briefing_vault
    out_path = tmp_path / "out.md"
    _write_briefing_yaml(wiki, target_path=str(out_path))

    fake = _FakeClient(
        text="## Briefing: 2026-04-29 (ccat)\n\n### What happened\n- shipped X\n"
    )

    def fake_make_client(**_kw):
        return fake

    monkeypatch.setattr(
        "lore_curator.llm_client.make_llm_client", fake_make_client
    )

    rc = briefing_cmd.main(["--wiki", "ccat"])
    assert rc == 0
    text = out_path.read_text()
    assert "### What happened" in text
    assert "shipped X" in text
    # Deterministic markers should NOT appear when LLM succeeds.
    assert "# Briefing — 2026-04-29 · ccat" not in text


def test_oneshot_falls_back_when_llm_client_unavailable(
    briefing_vault, tmp_path, monkeypatch, capsys
):
    """No LLM client → deterministic render is published."""
    _, wiki = briefing_vault
    out_path = tmp_path / "out.md"
    _write_briefing_yaml(wiki, target_path=str(out_path))

    monkeypatch.setattr(
        "lore_curator.llm_client.make_llm_client", lambda **_kw: None
    )

    rc = briefing_cmd.main(["--wiki", "ccat"])
    assert rc == 0
    text = out_path.read_text()
    assert "# Briefing — " in text  # deterministic header
    err = capsys.readouterr().err
    assert "deterministic fallback" in err


def test_oneshot_falls_back_when_llm_call_raises(
    briefing_vault, tmp_path, monkeypatch, capsys
):
    """LLM client raises → swallow + deterministic fallback."""
    _, wiki = briefing_vault
    out_path = tmp_path / "out.md"
    _write_briefing_yaml(wiki, target_path=str(out_path))

    fake = _FakeClient(raises=RuntimeError("boom"))

    monkeypatch.setattr(
        "lore_curator.llm_client.make_llm_client", lambda **_kw: fake
    )

    rc = briefing_cmd.main(["--wiki", "ccat"])
    assert rc == 0
    text = out_path.read_text()
    assert "# Briefing — " in text
    err = capsys.readouterr().err
    assert "boom" in err or "LLM call failed" in err


def test_oneshot_no_llm_flag_skips_compose(
    briefing_vault, tmp_path, monkeypatch, capsys
):
    """--no-llm: do not even attempt LLM, publish deterministic immediately."""
    _, wiki = briefing_vault
    out_path = tmp_path / "out.md"
    _write_briefing_yaml(wiki, target_path=str(out_path))

    fake = _FakeClient(text="should-not-appear")
    called = {"n": 0}

    def fake_make(**_kw):
        called["n"] += 1
        return fake

    monkeypatch.setattr("lore_curator.llm_client.make_llm_client", fake_make)

    rc = briefing_cmd.main(["--wiki", "ccat", "--no-llm"])
    assert rc == 0
    assert called["n"] == 0  # never tried to make a client
    text = out_path.read_text()
    assert "# Briefing — " in text
    assert "should-not-appear" not in text
    err = capsys.readouterr().err
    assert "--no-llm" in err


def test_gather_sharded_dd_only_no_time(tmp_path, monkeypatch):
    """sessions/YYYY/MM/DD-slug.md (no HHMM) — older sharded form."""
    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "demo"
    sessions = wiki / "sessions" / "2026" / "04"
    sessions.mkdir(parents=True)
    (sessions / "29-thing.md").write_text(
        dedent(
            """\
            ---
            schema_version: 2
            type: session
            summary: "did the thing"
            ---
            """
        )
    )
    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    result = gather(wiki="demo")
    assert len(result["new_sessions"]) == 1
    s = result["new_sessions"][0]
    assert s["date"] == "2026-04-29"
    assert s["slug"] == "thing"


# ---------------------------------------------------------------------------
# Linkage-frontmatter join: digest keyed by author/scope/epic, drill-down
# refs surfaced for downstream compose/render.
# ---------------------------------------------------------------------------


@pytest.fixture
def linkage_briefing_vault(tmp_path, monkeypatch):
    """Shared vault, two authors, real `linkage` frontmatter per note."""
    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "ccat"
    (wiki / "sessions").mkdir(parents=True)

    def write(name: str, *, author: str, epics: list[int], issues: list[int]) -> None:
        path = wiki / "sessions" / f"{name}.md"
        create_note(
            path,
            title=name[11:],
            description=f"session {name}",
            scope="lore:test",
            created=name[:10],
            linkage=Linkage(
                repo="acme/app",
                branch="main",
                issues=issues,
                epics=epics,
                author=author,
            ),
        )
        append_chapter(
            path,
            Chapter(blocks=[TopicBlock(lead="Did the thing.", anchor_turn=5)]),
            slice_from_turn=1,
            slice_to_turn=5,
        )
        close_note(path)

    write("2026-04-15-fix-a", author="Alice", epics=[162], issues=[175])
    write("2026-04-16-fix-b", author="Bob", epics=[161], issues=[180])

    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    return vault_root, wiki


def test_gather_includes_linkage_frontmatter(linkage_briefing_vault):
    result = gather(wiki="ccat")
    by_slug = {s["slug"]: s for s in result["new_sessions"]}
    assert by_slug["fix-a"]["linkage"]["author"] == "Alice"
    assert by_slug["fix-a"]["linkage"]["epics"] == [162]
    assert by_slug["fix-a"]["linkage"]["issues"] == [175]


def test_gather_shared_vault_multiple_authors(linkage_briefing_vault):
    """Two authors' notes coexist in one wiki; gather surfaces both."""
    result = gather(wiki="ccat")
    authors = sorted(s["linkage"]["author"] for s in result["new_sessions"])
    assert authors == ["Alice", "Bob"]


def test_gather_filters_by_epic(linkage_briefing_vault):
    result = gather(wiki="ccat", epic=162)
    assert len(result["new_sessions"]) == 1
    assert result["new_sessions"][0]["slug"] == "fix-a"


def test_gather_epic_filter_no_match_returns_empty(linkage_briefing_vault):
    result = gather(wiki="ccat", epic=999)
    assert result["new_sessions"] == []


def test_mcp_handle_briefing_gather_forwards_epic(linkage_briefing_vault):
    from lore_mcp.server import handle_briefing_gather

    result = handle_briefing_gather(wiki="ccat", epic=162)
    assert len(result["new_sessions"]) == 1
    assert result["new_sessions"][0]["slug"] == "fix-a"


def test_render_briefing_links_to_source_note():
    """Digest bullets carry a wikilink back to the source session note."""
    result = {
        "wiki": "ccat",
        "today": "2026-04-29",
        "ledger": {"last_briefing": None, "incorporated_count": 0},
        "new_sessions": [
            {
                "path": "sessions/2026-04-29-thing.md",
                "date": "2026-04-29",
                "slug": "thing",
                "frontmatter": {"summary": "shipped the thing"},
                "linkage": {},
                "body": "",
            }
        ],
    }
    out = render_briefing(result)
    # Pre-existing bullet text stays intact (backward compatible).
    assert "- **thing** — shipped the thing" in out
    assert "[[2026-04-29-thing]]" in out


def test_render_briefing_shows_drill_down_refs():
    """Author + epic/issue refs ride along so a reader can drill down."""
    result = {
        "wiki": "ccat",
        "today": "2026-04-29",
        "ledger": {"last_briefing": None, "incorporated_count": 0},
        "new_sessions": [
            {
                "path": "sessions/2026-04-29-thing.md",
                "date": "2026-04-29",
                "slug": "thing",
                "frontmatter": {"summary": "shipped the thing"},
                "linkage": {"author": "Alice", "epics": [162], "issues": [175]},
                "body": "",
            }
        ],
    }
    out = render_briefing(result)
    assert "Alice" in out
    assert "epic #162" in out
    assert "#175" in out
