"""Tests for lore_core.secrets_env — dotenv-style secrets file loader."""
from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest

from lore_core import secrets_env


@pytest.fixture(autouse=True)
def _reset_cache_and_env():
    """Each test starts with a fresh cache; restore any env keys we mutate.

    ``load_into_environ`` writes directly to ``os.environ`` (its whole
    point), so monkeypatch can't undo it. Snapshot the relevant keys
    here so test pollution doesn't leak into the rest of the suite.
    """
    secrets_env.reset_cache()
    snapshot = {
        k: os.environ.get(k)
        for k in (
            "LORE_TEST_NEW",
            "LORE_TEST_X",
            "LORE_TEST_EMPTY",
            "A",
            "B",
            "C",
        )
    }
    yield
    secrets_env.reset_cache()
    for k, v in snapshot.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ---------------------------------------------------------------------------
# parse()
# ---------------------------------------------------------------------------

def test_parse_basic_key_value():
    parsed = secrets_env.parse("FOO=bar\nBAZ=qux")
    assert parsed == {"FOO": "bar", "BAZ": "qux"}


def test_parse_strips_surrounding_whitespace():
    parsed = secrets_env.parse("  FOO  =  bar baz  \n")
    # Key trimmed, leading/trailing spaces around value trimmed,
    # interior spaces preserved.
    assert parsed == {"FOO": "bar baz"}


def test_parse_strips_matched_quotes():
    parsed = secrets_env.parse('FOO="bar baz"\nQUX=\'a=b\'')
    assert parsed == {"FOO": "bar baz", "QUX": "a=b"}


def test_parse_keeps_unmatched_quotes():
    parsed = secrets_env.parse("FOO=\"bar")
    assert parsed == {"FOO": '"bar'}


def test_parse_skips_blank_and_comment_lines():
    text = """
    # comment line
    FOO=1

    # another comment
    BAR=2
    """
    parsed = secrets_env.parse(text)
    assert parsed == {"FOO": "1", "BAR": "2"}


def test_parse_warns_on_malformed_lines():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        parsed = secrets_env.parse("VALID=1\njust a line\nALSO=2")
    assert parsed == {"VALID": "1", "ALSO": "2"}
    assert any("malformed" in str(w.message) for w in caught)


def test_parse_warns_on_invalid_keys():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        parsed = secrets_env.parse("1FOO=x\n=y\nGOOD=z")
    assert parsed == {"GOOD": "z"}
    assert any("invalid key" in str(w.message) for w in caught)


def test_parse_value_with_equals_signs():
    # base_url style values often contain '=' in query strings
    parsed = secrets_env.parse("URL=https://x/y?a=1&b=2")
    assert parsed == {"URL": "https://x/y?a=1&b=2"}


# ---------------------------------------------------------------------------
# load_file()
# ---------------------------------------------------------------------------

def test_load_file_missing_returns_empty(tmp_path):
    assert secrets_env.load_file(tmp_path / "nope.env") == {}


def test_load_file_caches_result(tmp_path):
    p = tmp_path / "secrets.env"
    p.write_text("FOO=1")
    first = secrets_env.load_file(p)
    p.write_text("FOO=changed")
    # Cached: second call ignores the changed file content.
    second = secrets_env.load_file(p)
    assert first == second == {"FOO": "1"}


def test_load_file_warns_on_permissive_mode(tmp_path):
    p = tmp_path / "secrets.env"
    p.write_text("FOO=1")
    p.chmod(0o644)  # group/other readable
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        secrets_env.load_file(p)
    assert any("chmod 600" in str(w.message) for w in caught)


def test_load_file_quiet_at_0600(tmp_path):
    p = tmp_path / "secrets.env"
    p.write_text("FOO=1")
    p.chmod(0o600)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        secrets_env.load_file(p)
    assert not any("chmod" in str(w.message) for w in caught)


# ---------------------------------------------------------------------------
# secrets_path()
# ---------------------------------------------------------------------------

def test_secrets_path_explicit_root(tmp_path):
    assert secrets_env.secrets_path(tmp_path) == tmp_path / ".lore" / "secrets.env"


def test_secrets_path_falls_back_to_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    assert secrets_env.secrets_path(None) == tmp_path / ".lore" / "secrets.env"


def test_secrets_path_returns_none_without_root(monkeypatch):
    monkeypatch.delenv("LORE_ROOT", raising=False)
    assert secrets_env.secrets_path(None) is None


# ---------------------------------------------------------------------------
# load_into_environ()
# ---------------------------------------------------------------------------

def test_load_into_environ_injects_missing_keys(tmp_path, monkeypatch):
    (tmp_path / ".lore").mkdir()
    p = tmp_path / ".lore" / "secrets.env"
    p.write_text("LORE_TEST_NEW=hello")
    p.chmod(0o600)
    monkeypatch.delenv("LORE_TEST_NEW", raising=False)
    injected = secrets_env.load_into_environ(tmp_path)
    assert injected == {"LORE_TEST_NEW": "hello"}
    assert os.environ["LORE_TEST_NEW"] == "hello"


def test_load_into_environ_does_not_override_set_vars(tmp_path, monkeypatch):
    (tmp_path / ".lore").mkdir()
    p = tmp_path / ".lore" / "secrets.env"
    p.write_text("LORE_TEST_X=from_file")
    p.chmod(0o600)
    monkeypatch.setenv("LORE_TEST_X", "from_shell")
    injected = secrets_env.load_into_environ(tmp_path)
    assert injected == {}
    assert os.environ["LORE_TEST_X"] == "from_shell"


def test_load_into_environ_treats_empty_as_unset(tmp_path, monkeypatch):
    """A key set to empty string in os.environ is treated as unset."""
    (tmp_path / ".lore").mkdir()
    p = tmp_path / ".lore" / "secrets.env"
    p.write_text("LORE_TEST_EMPTY=value")
    p.chmod(0o600)
    monkeypatch.setenv("LORE_TEST_EMPTY", "")
    injected = secrets_env.load_into_environ(tmp_path)
    assert injected == {"LORE_TEST_EMPTY": "value"}


def test_load_into_environ_no_root_is_noop(monkeypatch):
    monkeypatch.delenv("LORE_ROOT", raising=False)
    assert secrets_env.load_into_environ(None) == {}


def test_load_into_environ_keys_filter(tmp_path, monkeypatch):
    (tmp_path / ".lore").mkdir()
    p = tmp_path / ".lore" / "secrets.env"
    p.write_text("A=1\nB=2\nC=3")
    p.chmod(0o600)
    monkeypatch.delenv("A", raising=False)
    monkeypatch.delenv("B", raising=False)
    monkeypatch.delenv("C", raising=False)
    injected = secrets_env.load_into_environ(tmp_path, keys=["A", "C"])
    assert injected == {"A": "1", "C": "3"}
    assert "B" not in os.environ
