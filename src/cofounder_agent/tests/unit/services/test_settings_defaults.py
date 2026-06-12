"""Unit tests for services/settings_defaults.py (#379).

Covers the static registry shape (every key is a non-empty string, no
duplicates, no secret-looking names slip in) and the seed_all_defaults
helper's behavioural contract:

* No-pool no-op
* Idempotence (running twice doesn't double-count)
* Operator-tuned values aren't clobbered (ON CONFLICT DO NOTHING)
* Returns the count of newly-inserted rows

The integration-ish "fresh-DB ends with ~450 rows" test lives in
``tests/integration_db/test_settings_defaults_integration.py`` and only
runs when a live Postgres is reachable.
"""
from __future__ import annotations

import asyncio
import re
from unittest.mock import AsyncMock, MagicMock



def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------

class TestRegistryShape:
    def test_module_imports(self):
        from services import settings_defaults
        assert hasattr(settings_defaults, "DEFAULTS")
        assert hasattr(settings_defaults, "seed_all_defaults")
        assert hasattr(settings_defaults, "keys")

    def test_defaults_is_dict_of_strings(self):
        from services.settings_defaults import DEFAULTS
        assert isinstance(DEFAULTS, dict)
        assert len(DEFAULTS) > 0
        for k, v in DEFAULTS.items():
            assert isinstance(k, str), f"key {k!r} is not a string"
            assert k, "empty key in DEFAULTS"
            assert isinstance(v, str), f"value for {k!r} is not str: {type(v).__name__}"

    def test_no_duplicate_keys(self):
        # Python dicts can't actually contain duplicates — but verify that
        # the post-import iteration order is stable and matches the
        # ``keys()`` helper.
        from services.settings_defaults import DEFAULTS, keys
        assert sorted(DEFAULTS.keys()) == keys()
        assert len(set(DEFAULTS.keys())) == len(DEFAULTS)

    def test_keys_helper_is_sorted_unique(self):
        from services.settings_defaults import keys
        ks = keys()
        assert ks == sorted(ks)
        assert len(ks) == len(set(ks))

    def test_registry_size_in_expected_range(self):
        """Sanity floor/ceiling — caught accidental wholesale deletes."""
        from services.settings_defaults import DEFAULTS
        # 218 today; allow a generous range so adding new keys doesn't
        # break the test on every PR.
        assert 150 <= len(DEFAULTS) <= 600, (
            f"Registry size {len(DEFAULTS)} outside expected range "
            f"(150-600). Did the AST extractor regression?"
        )


# ---------------------------------------------------------------------------
# Secret-name guard
# ---------------------------------------------------------------------------

# Patterns that should NEVER appear in DEFAULTS — these belong to the
# secrets path (set_secret) and the migration auto-encrypt trigger
# (migration 0130) would mangle a placeholder value if seeded.
SECRET_NAME_PATTERNS = [
    re.compile(r".*_api_key$"),
    re.compile(r".*_api_token$"),
    re.compile(r".*_password$"),
    re.compile(r".*_secret$"),
    re.compile(r".*_secret_key$"),
    re.compile(r".*_dsn$"),
    re.compile(r".*_bot_token$"),
    re.compile(r"^api_token$"),
    re.compile(r"^cli_oauth_client_secret$"),
    re.compile(r"^jwt_secret(_key)?$"),
    re.compile(r"^session_secret$"),
    re.compile(r"^encryption_master_key$"),
    re.compile(r"^database_url$"),
    re.compile(r"^litellm_master_key$"),
]


class TestNoSecretsInRegistry:
    def test_no_secret_keys_in_defaults(self):
        from services.settings_defaults import DEFAULTS
        offenders = [
            k for k in DEFAULTS
            if any(p.match(k) for p in SECRET_NAME_PATTERNS)
        ]
        assert offenders == [], (
            "Registry contains keys that look like secrets — these must "
            f"stay unset on fresh install: {offenders}"
        )

    def test_known_secrets_explicitly_absent(self):
        """A handful of known-secret keys we explicitly never want seeded."""
        from services.settings_defaults import DEFAULTS
        forbidden = {
            "anthropic_api_key",
            "openai_api_key",
            "gemini_api_key",
            "google_api_key",
            "huggingface_api_key",
            "serper_api_key",
            "pexels_api_key",
            "smtp_password",
            "smtp_user",
            "telegram_bot_token",
            "telegram_chat_id",
            "discord_bot_token",
            "operator_id",
            "owner_email",
            "database_url",
            "jwt_secret_key",
            "encryption_master_key",
            "cli_oauth_client_id",
            "cli_oauth_client_secret",
            "local_postgres_password",
            "grafana_password",
            "pgadmin_password",
            "langfuse_secret_key",
            "litellm_master_key",
            "github_token",
            "vercel_token",
            "host_home",  # operator-specific path
            "gitea_password",
        }
        leaked = forbidden & set(DEFAULTS)
        assert leaked == set(), f"Forbidden secret keys present in registry: {leaked}"


# ---------------------------------------------------------------------------
# seed_all_defaults() behaviour
# ---------------------------------------------------------------------------

def _make_pool(insert_status: str = "INSERT 0 1"):
    """Build an asyncpg-shape pool whose execute() returns ``insert_status``."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=insert_status)

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


class TestSeedAllDefaults:
    def test_no_pool_returns_zero(self):
        from services.settings_defaults import seed_all_defaults
        assert _run(seed_all_defaults(None)) == 0

    def test_inserts_count_matches_status_strings(self):
        """When every INSERT returns 'INSERT 0 1', the count == len(DEFAULTS)."""
        from services.settings_defaults import DEFAULTS, seed_all_defaults

        pool, conn = _make_pool("INSERT 0 1")
        n = _run(seed_all_defaults(pool))
        assert n == len(DEFAULTS)
        assert conn.execute.await_count == len(DEFAULTS)

    def test_idempotent_on_full_conflict(self):
        """When every INSERT returns 'INSERT 0 0' (ON CONFLICT fired), count is 0."""
        from services.settings_defaults import DEFAULTS, seed_all_defaults

        pool, conn = _make_pool("INSERT 0 0")
        n = _run(seed_all_defaults(pool))
        assert n == 0
        # Still issued the SQL — that's by design (lets Postgres own the
        # conflict resolution rather than a pre-fetch + diff dance).
        assert conn.execute.await_count == len(DEFAULTS)

    def test_uses_on_conflict_do_nothing(self):
        """Verify the SQL does NOT overwrite operator-tuned values."""
        from services.settings_defaults import seed_all_defaults

        pool, conn = _make_pool("INSERT 0 0")
        _run(seed_all_defaults(pool))

        # Inspect every SQL statement we executed
        for call in conn.execute.await_args_list:
            sql = call.args[0]
            assert "ON CONFLICT (key) DO NOTHING" in sql, (
                f"Seeder must use ON CONFLICT DO NOTHING — found:\n{sql}"
            )
            # And must NOT use UPSERT-style DO UPDATE — that would clobber
            # operator-tuned rows.
            assert "DO UPDATE" not in sql, (
                f"Seeder uses DO UPDATE — would clobber operator values:\n{sql}"
            )

    def test_seeds_with_is_secret_false(self):
        """Verify is_secret column is FALSE in the INSERT — never accidentally TRUE."""
        from services.settings_defaults import seed_all_defaults

        pool, conn = _make_pool("INSERT 0 1")
        _run(seed_all_defaults(pool))

        # Look at the SQL the first call made
        first_call = conn.execute.await_args_list[0]
        sql = first_call.args[0]
        # Tolerate either case, with surrounding whitespace
        assert re.search(r"\bFALSE\b", sql, re.IGNORECASE), (
            "Seeder must mark seeded rows is_secret=FALSE so the encrypt "
            f"trigger doesn't mangle the placeholder value:\n{sql}"
        )


# ---------------------------------------------------------------------------
# Group-classification stays sensible
# ---------------------------------------------------------------------------

class TestGroupingMakesSense:
    def test_qa_keys_are_grouped_together(self):
        """All qa_* keys should appear in the same logical block of the file.

        Catches the regression where a new section is added that splits qa_*
        across two non-adjacent blocks (annoying for grep / review).
        """
        from pathlib import Path

        import services.settings_defaults as mod
        text = Path(mod.__file__).read_text(encoding="utf-8")

        # Grab the line index of every qa_ key occurrence
        qa_lines = []
        for i, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("'qa_") or stripped.startswith('"qa_'):
                qa_lines.append(i)

        if len(qa_lines) < 2:
            return  # Nothing to check
        span = qa_lines[-1] - qa_lines[0]
        # All qa_ keys should fit inside a contiguous-ish block
        # (some interleaving with comments is fine)
        assert span < 200, (
            f"qa_ keys span {span} lines — likely split across non-adjacent "
            "sections of the registry file (regression in GROUPS classifier)."
        )


# ---------------------------------------------------------------------------
# No dangling (uninstalled) model defaults
# ---------------------------------------------------------------------------

# The local Ollama ``gemma3`` family (``gemma3:27b`` / ``gemma3:27b-it-qat``)
# was upgraded to ``gemma4`` around 2026-06-08/09 and uninstalled. Any default
# still pointing at it would fail or silently CPU-offload budget / fallback /
# writer / structured-extraction calls on a FRESH install or a settings reset.
# See the ops memory note ``ollama-model-settings-dangle-after-upgrade``.
_UNINSTALLED_MODEL_SUBSTR = "gemma3"


class TestNoDanglingModelDefaults:
    """Guard both surfaces that re-seed ``*_model`` app_settings on a clean
    boot: the Python ``DEFAULTS`` registry (the settings-reset path) and the
    squashed ``0000_baseline.seeds.sql`` (the fresh-install path that actually
    wins, because migrations run before ``seed_all_defaults`` and both use
    ``ON CONFLICT DO NOTHING``).
    """

    def test_registry_defaults_have_no_uninstalled_model(self):
        from services.settings_defaults import DEFAULTS

        offenders = {
            k: v for k, v in DEFAULTS.items()
            if _UNINSTALLED_MODEL_SUBSTR in v
        }
        assert offenders == {}, (
            "settings_defaults.DEFAULTS still seed the uninstalled "
            f"'{_UNINSTALLED_MODEL_SUBSTR}' model family: {offenders}. Repoint "
            "to an installed successor (gemma-4-31B-it-qat:latest / gemma4:31b "
            "/ glm-4.7-5090:latest)."
        )

    def test_baseline_seed_values_have_no_uninstalled_model(self):
        import re
        from pathlib import Path

        from services import settings_defaults

        seeds = (
            Path(settings_defaults.__file__).parent
            / "migrations"
            / "0000_baseline.seeds.sql"
        )
        text = seeds.read_text(encoding="utf-8")

        # Pull (key, value) from each app_settings INSERT. The key and value
        # are the first two single-quoted fields and never contain embedded
        # quotes; descriptions (which may legitimately reference model
        # history) come later and are deliberately ignored.
        row_re = re.compile(
            r"INSERT INTO app_settings \([^)]*\) VALUES \('([^']*)',\s*'([^']*)'"
        )
        offenders = {
            key: value
            for key, value in row_re.findall(text)
            if _UNINSTALLED_MODEL_SUBSTR in value
        }
        assert offenders == {}, (
            "0000_baseline.seeds.sql seeds the uninstalled "
            f"'{_UNINSTALLED_MODEL_SUBSTR}' model family on fresh installs "
            f"(baseline wins over settings_defaults): {offenders}. Repoint "
            "each seeded value to an installed successor."
        )


# ---------------------------------------------------------------------------
# Retired settings must stay retired
# ---------------------------------------------------------------------------

class TestRetiredSettings:
    """Pin settings that were deliberately removed so they can't creep back.

    ``findings.topic_gap.min_severity`` was seeded as ``'info'`` in the
    baseline, but that value was always wrong: ``findings_alert_router``
    has a SQL floor that drops ``info`` findings BEFORE per-kind
    ``min_severity`` policy is consulted, so the row was inert and
    misleading. ``glad-labs-stack#1471`` fixed the emit site
    (``analyze_topic_gaps.py``) to emit at ``severity="warn"``;
    migration ``20260612_060000_drop_topic_gap_min_severity`` deletes the
    stale row from prod. The key must not re-appear in the seeder.

    If you need to add a CORRECT ``findings.topic_gap.min_severity``
    value (e.g. ``"warning"`` or ``"critical"``), delete this test and
    explain why in the PR — the current correct behaviour is to rely on
    ``_delivery_for``'s default floor (``"warning"``), which is right
    for a Discord-routed finding.
    """

    def test_topic_gap_min_severity_absent_from_defaults(self):
        from services.settings_defaults import DEFAULTS

        assert "findings.topic_gap.min_severity" not in DEFAULTS, (
            "findings.topic_gap.min_severity must NOT be seeded — the default "
            "'info' was inert (SQL floor drops info before per-kind policy runs) "
            "and the 'warn' emit in analyze_topic_gaps.py makes it redundant. "
            "See glad-labs-stack#1471 and migration "
            "20260612_060000_drop_topic_gap_min_severity."
        )
