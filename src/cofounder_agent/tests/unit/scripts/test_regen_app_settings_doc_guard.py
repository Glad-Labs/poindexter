"""Guards on scripts/regen-app-settings-doc.py's choice of source database.

``docs/reference/app-settings.md`` ships to the **public OSS mirror**, so the
only correct source is a throwaway DB seeded by the baseline migration — what
``.github/workflows/regen-app-settings-doc.yml`` does. Rendering it from a live
operator install pulls in that operator's runtime rows (1420 vs 679 on the
install this was found on) and leans entirely on the redaction tiers to keep
private values out.

Two things conspired to make that the DEFAULT outcome locally, which is why
both are pinned here:

1. ``brain.bootstrap.resolve_database_url`` ranks ``bootstrap.toml`` ABOVE
   ``DATABASE_URL``. That order is right for runtime entry points — an env var
   should not silently redirect production — but it means every real install
   has a bootstrap.toml that beats an explicitly exported ``DATABASE_URL``. A
   local run against a deliberately-created throwaway DSN still rendered all
   1420 prod rows, with no indication the env var had been ignored.
2. Nothing downstream would have caught it. The redaction tiers are
   defense-in-depth against *known* key-name and value shapes; they cannot know
   an operator-only key added last week holds a private hostname. Not reading
   prod at all is the actual control.

The freshness check keys on an exact invariant rather than a heuristic: the
baseline seeds exactly two ``is_secret`` rows and both are empty placeholders,
so any populated secret proves this is somebody's live install.

The script filename is hyphenated (not an importable module name), so it is
loaded by path via importlib — same as ``test_recovery_agent.py``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_script():
    here = Path(__file__).resolve()
    # Anchor on the FILE, not a `scripts/` directory — this test itself lives in
    # tests/unit/scripts/, which would otherwise match first.
    root = next(
        (p for p in here.parents if (p / "scripts" / "regen-app-settings-doc.py").is_file()),
        None,
    )
    assert root is not None, "could not locate scripts/regen-app-settings-doc.py from test"
    path = root / "scripts" / "regen-app-settings-doc.py"
    spec = importlib.util.spec_from_file_location("regen_app_settings_doc", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


regen = _load_script()


class _FakeConn:
    """Minimal asyncpg-conn stand-in returning a fixed populated-secret count."""

    def __init__(self, populated_secrets: int) -> None:
        self._n = populated_secrets
        self.queries: list[str] = []

    async def fetchval(self, query: str, *args):
        self.queries.append(query)
        return self._n


# --- the freshness guard ---------------------------------------------------


@pytest.mark.asyncio
async def test_fresh_baseline_db_passes():
    # Baseline seeds two is_secret placeholders, both EMPTY, so a fresh DB
    # reports zero populated secrets and the render proceeds.
    await regen._assert_fresh_baseline_db(_FakeConn(0))


@pytest.mark.asyncio
async def test_operator_db_is_refused():
    with pytest.raises(SystemExit) as exc:
        await regen._assert_fresh_baseline_db(_FakeConn(63))
    msg = str(exc.value)
    assert "refusing to regenerate" in msg
    assert "63 populated secret" in msg
    # The refusal must teach the correct procedure, not just say no.
    assert "migrations_smoke.py" in msg
    assert "pgvector/pgvector:pg16" in msg


@pytest.mark.asyncio
async def test_guard_counts_only_populated_secrets(monkeypatch):
    # The predicate must be "is_secret AND non-empty", not a bare is_secret
    # count — the baseline's two EMPTY placeholders would trip a bare count and
    # make CI's own fresh DB unrenderable.
    conn = _FakeConn(0)
    await regen._assert_fresh_baseline_db(conn)
    sql = " ".join(conn.queries).lower()
    assert "is_secret = true" in sql
    assert "value, '') <> ''" in sql


@pytest.mark.asyncio
async def test_escape_hatch_allows_operator_db(monkeypatch, capsys):
    monkeypatch.setenv(regen._ALLOW_OPERATOR_DB_ENV, "1")
    await regen._assert_fresh_baseline_db(_FakeConn(63))  # does not raise
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "Do NOT commit" in err


@pytest.mark.asyncio
async def test_escape_hatch_ignores_junk_values(monkeypatch):
    # Only explicit truthy opt-ins count; a stray value must not disarm it.
    monkeypatch.setenv(regen._ALLOW_OPERATOR_DB_ENV, "maybe")
    with pytest.raises(SystemExit):
        await regen._assert_fresh_baseline_db(_FakeConn(1))


# --- DSN precedence --------------------------------------------------------


def test_database_url_beats_bootstrap_toml(monkeypatch):
    # The inversion that makes the documented procedure work at all. Without
    # it, `export DATABASE_URL=...` is silently ignored on any real install.
    seen: dict = {}

    def fake_resolve(*, explicit=None):
        seen["explicit"] = explicit
        return explicit or "postgresql://bootstrap-toml/prod"

    monkeypatch.setattr(regen, "resolve_database_url", fake_resolve)
    monkeypatch.setenv("DATABASE_URL", "postgresql://throwaway/poindexter_test")
    assert regen._doc_database_url() == "postgresql://throwaway/poindexter_test"
    assert seen["explicit"] == "postgresql://throwaway/poindexter_test"


def test_falls_back_to_normal_resolution_without_database_url(monkeypatch):
    # With no env var the usual resolution still applies, so CI images and
    # bootstrap-only installs are unaffected.
    monkeypatch.setattr(
        regen, "resolve_database_url", lambda *, explicit=None: explicit or "from-bootstrap"
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert regen._doc_database_url() == "from-bootstrap"


def test_blank_database_url_is_not_treated_as_explicit(monkeypatch):
    # An exported-but-empty DATABASE_URL must not shadow bootstrap.toml with
    # None — that would resolve to no DSN at all.
    monkeypatch.setattr(
        regen, "resolve_database_url", lambda *, explicit=None: explicit or "from-bootstrap"
    )
    monkeypatch.setenv("DATABASE_URL", "   ")
    assert regen._doc_database_url() == "from-bootstrap"
