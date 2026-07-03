"""Sink config resolution: env > yaml > error (issue #15 + #4).

Covers:
* matrix sink: yaml-only / env-only / env-overrides-yaml / nested vs
  flat / missing-fields error
* markdown sink: URI target wins over yaml; yaml fallback works
* dispatch(): mismatched sink (yaml says X, URI says Y) raises
  SinkConfigMismatchError
* cmd_publish --wiki: loads yaml and threads it through to the sink
"""

from __future__ import annotations

import warnings
from textwrap import dedent

import pytest

from lore_core.briefing import (
    SinkConfigMismatchError,
    UnknownSinkError,
    dispatch,
    register,
)
from lore_core.briefing.sinks import matrix as matrix_sink
from lore_core.briefing.sinks import markdown as markdown_sink


# ---------------------------------------------------------------------------
# matrix sink: _resolve_room_config
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_matrix_env(monkeypatch):
    """Strip matrix env vars so each test sets them explicitly."""
    for v in ("LORE_MATRIX_HOMESERVER", "LORE_MATRIX_USER_ID", "LORE_MATRIX_ROOM_ID"):
        monkeypatch.delenv(v, raising=False)
    matrix_sink._FLAT_DEPRECATION_WARNED = False
    markdown_sink._FLAT_DEPRECATION_WARNED = False
    yield


def test_matrix_yaml_nested_resolves() -> None:
    config = {
        "sink": "matrix",
        "matrix": {
            "homeserver": "https://m.example.org",
            "user_id": "@bot:m.example.org",
            "room_id": "!abc:m.example.org",
        },
    }
    assert matrix_sink._resolve_room_config(config) == (
        "https://m.example.org",
        "@bot:m.example.org",
        "!abc:m.example.org",
    )


def test_matrix_yaml_flat_resolves_with_deprecation_warning() -> None:
    config = {
        "sink": "matrix",
        "homeserver": "https://m.example.org",
        "user_id": "@bot:m.example.org",
        "room_id": "!abc:m.example.org",
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert matrix_sink._resolve_room_config(config)[0] == "https://m.example.org"
    assert any(
        issubclass(w.category, DeprecationWarning) and "flat top-level" in str(w.message)
        for w in caught
    )


def test_matrix_env_only(monkeypatch) -> None:
    monkeypatch.setenv("LORE_MATRIX_HOMESERVER", "https://m.example.org")
    monkeypatch.setenv("LORE_MATRIX_USER_ID", "@bot:m.example.org")
    monkeypatch.setenv("LORE_MATRIX_ROOM_ID", "!abc:m.example.org")
    assert matrix_sink._resolve_room_config(None) == (
        "https://m.example.org",
        "@bot:m.example.org",
        "!abc:m.example.org",
    )


def test_matrix_env_overrides_yaml(monkeypatch) -> None:
    """Env var wins when both are set — debug overrides keep working."""
    monkeypatch.setenv("LORE_MATRIX_HOMESERVER", "https://override.example.org")
    config = {
        "matrix": {
            "homeserver": "https://yaml.example.org",
            "user_id": "@bot:m.example.org",
            "room_id": "!abc:m.example.org",
        },
    }
    homeserver, _, _ = matrix_sink._resolve_room_config(config)
    assert homeserver == "https://override.example.org"


def test_matrix_missing_fields_errors() -> None:
    config = {"matrix": {"homeserver": "https://m.example.org"}}
    with pytest.raises(RuntimeError, match="user_id, room_id"):
        matrix_sink._resolve_room_config(config)


def test_matrix_no_config_no_env_errors() -> None:
    with pytest.raises(RuntimeError, match="homeserver, user_id, room_id"):
        matrix_sink._resolve_room_config(None)


def test_matrix_nested_takes_precedence_over_flat() -> None:
    """When both nested and flat are present, nested wins (no warning)."""
    config = {
        "matrix": {
            "homeserver": "https://nested.example.org",
            "user_id": "@bot:m.example.org",
            "room_id": "!abc:m.example.org",
        },
        "homeserver": "https://flat.example.org",
        "user_id": "@flat:m.example.org",
        "room_id": "!flat:m.example.org",
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        homeserver, _, _ = matrix_sink._resolve_room_config(config)
    assert homeserver == "https://nested.example.org"
    assert not any(
        issubclass(w.category, DeprecationWarning) for w in caught
    ), "flat-key warning should not fire when nested resolves"


# ---------------------------------------------------------------------------
# markdown sink: _resolve_path
# ---------------------------------------------------------------------------


def test_markdown_uri_target_wins_over_yaml() -> None:
    config = {"markdown": {"path": "/tmp/from-yaml.md"}}
    assert markdown_sink._resolve_path("/tmp/from-uri.md", config) == "/tmp/from-uri.md"


def test_markdown_yaml_nested_fallback() -> None:
    config = {"markdown": {"path": "/tmp/from-yaml.md"}}
    assert markdown_sink._resolve_path("", config) == "/tmp/from-yaml.md"


def test_markdown_yaml_flat_fallback_warns() -> None:
    config = {"path": "/tmp/from-yaml.md"}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = markdown_sink._resolve_path("", config)
    assert result == "/tmp/from-yaml.md"
    assert any(
        issubclass(w.category, DeprecationWarning) and "flat top-level" in str(w.message)
        for w in caught
    )


def test_markdown_no_target_no_yaml_errors(tmp_path) -> None:
    with pytest.raises(ValueError, match="markdown sink requires"):
        markdown_sink._send("", "body", None)


def test_markdown_writes_via_yaml_path(tmp_path) -> None:
    out = tmp_path / "via-yaml.md"
    config = {"sink": "markdown", "markdown": {"path": str(out)}}
    markdown_sink._send("", "## body\n", config)
    assert "## body" in out.read_text()


# ---------------------------------------------------------------------------
# dispatch(): sink mismatch
# ---------------------------------------------------------------------------


def test_dispatch_refuses_sink_mismatch(tmp_path) -> None:
    """yaml says sink: matrix, URI says markdown — refuse."""
    config = {"sink": "matrix"}
    with pytest.raises(SinkConfigMismatchError, match="sink mismatch"):
        dispatch(f"markdown:{tmp_path / 'x.md'}", "body", config)


def test_dispatch_allows_no_sink_key_in_config(tmp_path) -> None:
    """yaml without 'sink:' is fine — only mismatch errors are gated."""
    out = tmp_path / "x.md"
    config = {"markdown": {"path": str(out)}}
    dispatch("markdown", "body", config)
    assert "body" in out.read_text()


def test_dispatch_unknown_sink_still_raises() -> None:
    with pytest.raises(UnknownSinkError):
        dispatch("nonexistent-scheme", "body", None)


# ---------------------------------------------------------------------------
# CLI: lore briefing publish --wiki <name>
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_with_briefing_yaml(tmp_path, monkeypatch):
    """Vault with one wiki + .lore-briefing.yml + a session note."""
    vault = tmp_path / "vault"
    wiki_dir = vault / "wiki" / "demo"
    (wiki_dir / "sessions").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(vault))
    return wiki_dir


def test_cli_publish_loads_yaml_and_passes_to_markdown_sink(
    vault_with_briefing_yaml, tmp_path, monkeypatch,
) -> None:
    out_path = tmp_path / "out.md"
    (vault_with_briefing_yaml / ".lore-briefing.yml").write_text(
        dedent(
            f"""\
            sink: markdown
            markdown:
              path: {out_path}
            """
        )
    )
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

    from lore_cli import briefing_cmd

    rc = briefing_cmd.main(
        ["publish", "--sink", "markdown", "--wiki", "demo"]
    )
    assert rc == 0
    assert out_path.exists()
    assert "Briefing" in out_path.read_text()


def test_cli_publish_refuses_yaml_sink_mismatch(
    vault_with_briefing_yaml, tmp_path, monkeypatch, capsys,
) -> None:
    (vault_with_briefing_yaml / ".lore-briefing.yml").write_text(
        "sink: matrix\nmatrix:\n  homeserver: https://m.example.org\n"
    )
    monkeypatch.setattr(
        "sys.stdin",
        type(
            "S",
            (),
            {
                "read": staticmethod(lambda: "## body\n"),
                "isatty": staticmethod(lambda: False),
            },
        )(),
    )

    from lore_cli import briefing_cmd

    rc = briefing_cmd.main(
        ["publish", "--sink", "markdown",
         "--out", str(tmp_path / "x.md"), "--wiki", "demo"]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "sink mismatch" in err


def test_cli_publish_missing_yaml_file(
    vault_with_briefing_yaml, tmp_path, monkeypatch, capsys,
) -> None:
    """--wiki set but no .lore-briefing.yml present → exit 1 with clear msg."""
    monkeypatch.setattr(
        "sys.stdin",
        type(
            "S",
            (),
            {
                "read": staticmethod(lambda: "## body\n"),
                "isatty": staticmethod(lambda: False),
            },
        )(),
    )

    from lore_cli import briefing_cmd

    rc = briefing_cmd.main(
        ["publish", "--sink", "markdown",
         "--out", str(tmp_path / "x.md"), "--wiki", "demo"]
    )
    assert rc == 1
    assert "no .lore-briefing.yml" in capsys.readouterr().err
