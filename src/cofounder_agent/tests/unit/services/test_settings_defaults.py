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


def test_ops_triage_defaults_to_small_model_not_heavy_writer():
    """Alert triage is a one-paragraph ops task; it must NOT default to the
    19 GB content writer. With no default the route fell through to
    ``pipeline_writer_model``, so a triage reloaded the writer into VRAM
    mid-media-render and CUDA-OOM'd the SDXL server (2026-06-21). A fresh
    install now gets a small model (matching ``cost_tier.free.model``)."""
    from services.settings_defaults import DEFAULTS

    assert DEFAULTS["ops_triage_writer_model"] == "ollama/llama3.2:3b"
    assert DEFAULTS["ops_triage_writer_model"] != DEFAULTS["pipeline_writer_model"]


def test_rag_rerank_device_default_is_cpu():
    """The cross-encoder reranker must default to CPU so it stops stacking
    on the resident ~18 GB writer in VRAM (single-GPU stability core). The
    device is DB-tunable via rag_rerank_device."""
    from services.settings_defaults import DEFAULTS, METADATA

    assert DEFAULTS["rag_rerank_device"] == "cpu"
    assert METADATA["rag_rerank_device"]["value_type"] == "string"


def test_vram_budget_defaults_present():
    """The VRAM budget guard reads four DB-tunable knobs: total VRAM, the
    desktop reserve carved out so the WDDM compositor never starves, the KV
    cache dtype that sets bytes/element, and the on/off switch."""
    from services.settings_defaults import DEFAULTS, METADATA

    assert DEFAULTS["gpu_vram_total_gb"] == "32"
    assert DEFAULTS["gpu_desktop_reserve_gb"] == "3"
    assert DEFAULTS["ollama_kv_cache_type"] == "q8_0"
    assert DEFAULTS["vram_budget_guard_enabled"] == "true"
    assert METADATA["gpu_vram_total_gb"]["value_type"] == "float"
    assert METADATA["gpu_desktop_reserve_gb"]["value_type"] == "float"
    assert METADATA["ollama_kv_cache_type"]["value_type"] == "string"
    assert METADATA["vram_budget_guard_enabled"]["value_type"] == "boolean"


def test_piece4_video_hero_defaults_present():
    """Video-quality Piece 4: the swappable generative-video model seam and the
    per-video hero-shot budget cap (spec §3.3)."""
    from services.settings_defaults import DEFAULTS, METADATA

    assert DEFAULTS["generative_video_model"] == "Wan-AI/Wan2.2-TI2V-5B"
    assert DEFAULTS["video_hero_shots_max"] == "3"
    assert METADATA["generative_video_model"]["value_type"] == "model"
    assert METADATA["video_hero_shots_max"]["value_type"] == "integer"


def test_qa_vision_num_predict_has_headroom_for_thinking_plus_json():
    """qwen3-vl's <think> trace shares the num_predict budget with the JSON
    verdict; the old hardcoded 400 truncated the JSON and the vision rail
    returned None on good images (#563). The default must leave real headroom
    and be int-parseable + DB-tunable."""
    from services.settings_defaults import DEFAULTS, METADATA

    assert int(DEFAULTS["qa_vision_num_predict"]) >= 768  # > the broken 400
    assert METADATA["qa_vision_num_predict"]["value_type"] == "int"


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

    def test_video_director_model_is_writer_grade(self):
        # Director + self-critique run on the writer model, not the standard
        # tier (video-quality spec §3.1) — shared video_director_model key.
        from services.settings_defaults import DEFAULTS, METADATA
        assert DEFAULTS["video_director_model"] == DEFAULTS["pipeline_writer_model"]
        assert METADATA["video_director_model"]["value_type"] == "model"

    def test_video_shot_qa_keys_seeded(self):
        # Per-shot vision-QA render-check loop tunables (video-quality spec §3.2).
        from services.settings_defaults import DEFAULTS, METADATA
        assert DEFAULTS["video_shot_qa_enabled"] == "true"
        assert DEFAULTS["video_shot_qa_threshold"] == "60"
        assert DEFAULTS["video_shot_qa_max_retries"] == "2"
        assert METADATA["video_shot_qa_enabled"]["value_type"] == "boolean"
        assert METADATA["video_shot_qa_threshold"]["value_type"] == "integer"

    def test_preview_gate_keys_seeded_off(self):
        # preview_gate (component-scoped regen) ships DISABLED — develop behind
        # the flag; the default flip to 'on' is gated on end-to-end verification
        # (plan Task 12). The two caps bound the per-component regen loop (the
        # HITL runaway guard; the surface refuses past them).
        from services.settings_defaults import DEFAULTS, METADATA
        assert DEFAULTS["pipeline_gate_preview_gate"] == "off"
        assert DEFAULTS["regen_images_max_attempts"] == "3"
        assert DEFAULTS["regen_text_max_attempts"] == "2"
        assert METADATA["pipeline_gate_preview_gate"]["value_type"] == "string"
        assert METADATA["regen_images_max_attempts"]["value_type"] == "integer"
        assert METADATA["regen_text_max_attempts"]["value_type"] == "integer"

    def test_caption_provider_keys_seeded(self):
        # media.transcribe_narration selects its ASR provider via
        # get_caption_provider → video_caption_engine (default 'speaches', the
        # already-running faster-whisper sidecar). The prior hardcoded
        # whisper_local default shelled a whisper-cli binary that was never
        # installed in the worker image, so captions silently never burned in.
        from services.settings_defaults import DEFAULTS, METADATA
        assert DEFAULTS["video_caption_engine"] == "speaches"
        assert DEFAULTS["plugin.caption_provider.speaches.enabled"] == "true"
        assert (
            DEFAULTS["plugin.caption_provider.speaches.base_url"]
            == "http://speaches:8000/v1"
        )
        assert (
            DEFAULTS["plugin.caption_provider.speaches.model"]
            == "Systran/faster-whisper-medium"
        )
        assert METADATA["video_caption_engine"]["value_type"] == "string"
        assert (
            METADATA["plugin.caption_provider.speaches.enabled"]["value_type"]
            == "boolean"
        )
        assert (
            METADATA["plugin.caption_provider.speaches.base_url"]["value_type"]
            == "url"
        )
        # initial_prompt biases the ASR toward proper nouns (brand-name fix).
        # Defaults to '' (the NOT-NULL "unset" sentinel) so the public OSS
        # default ships no operator vocabulary and behaves as before; the
        # operator sets brand terms in their own DB.
        assert DEFAULTS["plugin.caption_provider.speaches.initial_prompt"] == ""
        assert (
            METADATA["plugin.caption_provider.speaches.initial_prompt"]["value_type"]
            == "string"
        )

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
        """When every execute() returns 'INSERT 0 1', inserted count == len(DEFAULTS).

        The total execute() call count is len(DEFAULTS) INSERTs + len(METADATA)
        UPDATE metadata passes (poindexter#756).
        """
        from services.settings_defaults import DEFAULTS, METADATA, seed_all_defaults

        pool, conn = _make_pool("INSERT 0 1")
        n = _run(seed_all_defaults(pool))
        assert n == len(DEFAULTS)
        assert conn.execute.await_count == len(DEFAULTS) + len(METADATA)

    def test_idempotent_on_full_conflict(self):
        """When every INSERT returns 'INSERT 0 0' (ON CONFLICT fired), count is 0.

        The UPDATE metadata pass still runs (len(METADATA) calls) even when all
        INSERTs conflict — metadata is applied to both new and existing rows.
        """
        from services.settings_defaults import DEFAULTS, METADATA, seed_all_defaults

        pool, conn = _make_pool("INSERT 0 0")
        n = _run(seed_all_defaults(pool))
        assert n == 0
        # Still issued all SQL — that's by design.
        assert conn.execute.await_count == len(DEFAULTS) + len(METADATA)

    def test_uses_on_conflict_do_nothing(self):
        """Verify INSERT calls do NOT overwrite operator-tuned values.

        The seeder also issues UPDATE calls for METADATA — those are intentional
        and don't have ON CONFLICT DO NOTHING (they're not INSERTs).  Only the
        INSERT calls are checked here.
        """
        from services.settings_defaults import seed_all_defaults

        pool, conn = _make_pool("INSERT 0 0")
        _run(seed_all_defaults(pool))

        # Inspect only INSERT SQL calls (not UPDATE metadata calls)
        for call in conn.execute.await_args_list:
            sql = call.args[0]
            if "INSERT INTO app_settings" not in sql:
                continue  # skip METADATA UPDATE calls
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
        """All qa_* keys in DEFAULTS should appear in the same logical block.

        Catches the regression where a new section splits qa_* across
        non-adjacent blocks in the DEFAULTS dict (annoying for grep/review).
        Only DEFAULTS is checked — the METADATA dict groups keys by concern
        (security, QA, RAG, …) not by name prefix, so qa_ entries scattered
        across METADATA are expected.
        """
        from pathlib import Path

        import services.settings_defaults as mod
        text = Path(mod.__file__).read_text(encoding="utf-8")

        # Restrict to lines inside the DEFAULTS dict only (stop at METADATA).
        lines = text.splitlines()
        defaults_lines = []
        in_defaults = False
        for line in lines:
            if line.startswith("DEFAULTS"):
                in_defaults = True
            if in_defaults and line.startswith("METADATA"):
                break
            if in_defaults:
                defaults_lines.append(line)

        qa_lines = []
        for i, line in enumerate(defaults_lines, start=1):
            stripped = line.lstrip()
            if stripped.startswith("'qa_") or stripped.startswith('"qa_'):
                qa_lines.append(i)

        if len(qa_lines) < 2:
            return  # Nothing to check
        span = qa_lines[-1] - qa_lines[0]
        # All qa_ keys in DEFAULTS should fit inside a contiguous-ish block
        # (some interleaving with comments is fine). Two legitimate clusters
        # exist: the qa_*_model config keys that live with the other model keys
        # (qa_fallback_writer_model / qa_rewrite_model / qa_*_vision_*), and the
        # qa scoring + gate-behavior cluster (qa_accuracy_* … qa_rewrite_max_attempts
        # / qa_flag_instead_of_reject). The slack accommodates that ~210-line gap;
        # a genuinely new far-flung qa_ section would still overshoot it.
        assert span < 230, (
            f"qa_ keys span {span} lines in DEFAULTS — likely split across "
            "non-adjacent sections (regression in GROUPS classifier)."
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


# ---------------------------------------------------------------------------
# Deprecated settings (lifecycle columns, not hard-delete)
# ---------------------------------------------------------------------------

class TestDeprecatedSettings:
    """Keys kept as tombstones via the lifecycle columns (deprecated +
    superseded_by) instead of hard-deleted. ``seed_settings_metadata`` applies
    the flag every boot (fresh + prod via ``UPDATE … IS DISTINCT FROM``) and
    ``SiteConfig.get()`` warns once-per-boot on read, pointing callers at the
    replacement key.
    """

    def test_nvidia_exporter_url_deprecated(self):
        """nvidia_exporter_url went dead when PR #1827 moved gpu_scheduler onto
        Prometheus (gpu_metrics_prometheus_url); nothing reads it anymore."""
        from services.settings_defaults import DEFAULTS, METADATA

        meta = METADATA.get("nvidia_exporter_url")
        assert meta is not None, "nvidia_exporter_url must be marked in METADATA"
        assert meta.get("deprecated") is True
        assert meta.get("superseded_by") == "gpu_metrics_prometheus_url"
        # The replacement must be a live, seeded key (breadcrumb can't dangle).
        assert "gpu_metrics_prometheus_url" in DEFAULTS

    def test_every_deprecated_key_supersedes_a_live_key(self):
        """Invariant guarding all future deprecations: a deprecated key's
        superseded_by must point at a key that still exists in DEFAULTS."""
        from services.settings_defaults import DEFAULTS, METADATA

        for key, meta in METADATA.items():
            if not meta.get("deprecated"):
                continue
            target = meta.get("superseded_by")
            assert target, f"{key} is deprecated but has no superseded_by"
            assert target in DEFAULTS, (
                f"{key} superseded_by={target!r}, which is not a live "
                "DEFAULTS key"
            )


# ---------------------------------------------------------------------------
# DataFabric URLs must seed internal DNS, never localhost
# ---------------------------------------------------------------------------

class TestDataFabricUrlsAreInternalDns:
    """DataFabric clients run inside the worker/brain containers, so the
    seeded ``data_fabric_*_url`` defaults must use compose-service DNS — a
    ``localhost`` value resolves to the container itself (the in-container
    footgun PR #1827 fixed for the GPU URL). Companion to the DEFAULT_URL
    constant test in tests/unit/services/data_fabric/test_data_fabric.py;
    this guards the *seed* surface that populates fresh installs.
    """

    EXPECTED = {
        "data_fabric_prometheus_url": "http://prometheus:9090",
        "data_fabric_loki_url": "http://loki:3100",
        "data_fabric_tempo_url": "http://tempo:3200",
        "data_fabric_pyroscope_url": "http://pyroscope:4040",
    }

    def test_data_fabric_defaults_use_internal_dns(self):
        from services.settings_defaults import DEFAULTS

        for key, expected in self.EXPECTED.items():
            actual = DEFAULTS.get(key)
            assert actual == expected, (
                f"{key} default = {actual!r}; expected {expected!r}. A "
                "localhost default resolves to the container itself."
            )


# ---------------------------------------------------------------------------
# Hardcoded-value externalisation audit keys
# ---------------------------------------------------------------------------

class TestConfigExternalisationAuditKeys:
    """Keys added by the hardcoded-value externalisation audit. Each replaced
    a literal that used to bypass app_settings; the seeded default must match
    the prior in-code constant so the cutover is behaviour-preserving.
    """

    # key -> seeded default (must equal the old in-code literal)
    EXPECTED = {
        # Web fact-check rail (modules/content/multi_model_qa.py)
        "qa_web_factcheck_match_ratio": "0.6",
        "qa_web_factcheck_num_results": "3",
        "qa_web_factcheck_snippet_chars": "500",
        "qa_web_factcheck_min_term_len": "2",
        "qa_web_factcheck_max_claims": "3",
        # Image-style rotation window (services/image_style_rotation.py)
        "image_style_history_size": "10",
        "image_style_history_ttl_seconds": "3600",
        # Local GPU render timeouts (image / audio providers)
        "image_render_timeout_seconds": "240",
        "audio_render_timeout_seconds": "180",
        # Worker heartbeat cadence (services/worker_service.py)
        "worker_heartbeat_interval_seconds": "30",
        # URL scraper limits (services/url_scraper.py)
        "url_scraper_timeout_seconds": "15",
        "url_scraper_max_content_chars": "50000",
        # Tap ingest chunk size (services/taps/_chunking.py)
        "tap_chunk_max_chars": "6000",
    }

    def test_audit_keys_present_with_expected_defaults(self):
        from services.settings_defaults import DEFAULTS

        for key, val in self.EXPECTED.items():
            assert key in DEFAULTS, f"{key} missing from DEFAULTS (audit regression)"
            assert DEFAULTS[key] == val, (
                f"{key} default drifted: {DEFAULTS[key]!r} != {val!r}"
            )

    def test_audit_numeric_defaults_parse(self):
        """Every audit key is consumed via get_int/get_float — the seeded
        string default must parse as the right numeric type."""
        from services.settings_defaults import DEFAULTS

        float_keys = {"qa_web_factcheck_match_ratio"}
        for key in self.EXPECTED:
            if key in float_keys:
                float(DEFAULTS[key])
            else:
                int(DEFAULTS[key])  # raises if the default isn't an int string
