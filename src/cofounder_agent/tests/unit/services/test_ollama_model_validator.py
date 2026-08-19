"""Unit tests for StartupManager._validate_ollama_model_settings().

Covers the five scenarios called out in glad-labs-stack#1284:
  1. All models installed  -> no notification sent
  2. One model missing     -> notify_operator called, message names the model
  3. Suspect template      -> notify_operator called, message names the model
  4. Ollama unreachable    -> warning logged, notify_operator called, no hard-fail
  5. Validation disabled   -> no checks run at all

All Ollama HTTP calls are mocked via unittest.mock so the tests are
fully offline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.startup_manager import StartupManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOOD_TEMPLATE = "{{ if .System }}<|im_start|>system\n{{ .System }}<|im_end|>\n{{ end }}"
# `<|im_turn|>` belongs to no published family -- a mangled ChatML variant.
# NB this used to be the Gemma 4 `<|turn>…<turn|>` shape, which is legitimate;
# see _GEMMA4_TEMPLATE below.
_SUSPECT_TEMPLATE = "{{ .Input }}<|im_turn|>assistant{{ .Response }}"
_ESTABLISHED_TEMPLATE = "<start_of_turn>user\n{{ .Input }}<end_of_turn>"
# Gemma 4's real paired-delimiter DSL, trimmed from the live
# gemma-4-31B-it-qat:latest template.
_GEMMA4_TEMPLATE = (
    "{{- '<|turn>system\n' -}}{{- '<turn|>\n' -}}"
    "{%- for message in loop_messages -%}{{- '<|turn>' + role + '\n' }}"
    "{{- '<turn|>\n' -}}{%- endfor -%}{{- '<|turn>model\n' -}}"
)


def _make_site_config(overrides: dict[str, str] | None = None) -> MagicMock:
    """Build a minimal SiteConfig mock."""
    defaults = {
        "ollama_model_validation_enabled": "true",
        "ollama_base_url": "http://localhost:11434",
    }
    if overrides:
        defaults.update(overrides)

    sc = MagicMock()
    sc.get = lambda key, default="": defaults.get(key, default)
    return sc


def _make_pool_rows(rows: list[dict]):
    """Return (pool, conn) with rows as Record-like objects."""
    records = []
    for row in rows:
        rec = MagicMock()
        rec.__getitem__ = lambda self, k, _row=row: _row[k]
        records.append(rec)

    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=records)

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


def _make_http_response(json_data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data)
    resp.raise_for_status = MagicMock()  # no-op
    return resp


def _make_manager(site_config=None) -> StartupManager:
    sc = site_config if site_config is not None else _make_site_config()
    return StartupManager(site_config=sc)


async def _run_validator(
    model_rows: list[dict],
    tags_data: dict,
    show_data: dict | None = None,
    site_config_overrides: dict | None = None,
    ollama_raises: Exception | None = None,
) -> AsyncMock:
    """Run the validator and return the notify_operator mock."""
    pool, _ = _make_pool_rows(model_rows)
    sc = _make_site_config(site_config_overrides)
    manager = _make_manager(sc)

    notify_mock = AsyncMock()

    tags_resp = _make_http_response(tags_data)
    show_resp = _make_http_response(show_data or {})

    async def fake_get(url, **kw):
        if ollama_raises is not None:
            raise ollama_raises
        return tags_resp

    async def fake_post(url, **kw):
        return show_resp

    mock_client_instance = AsyncMock()
    mock_client_instance.get = fake_get
    mock_client_instance.post = fake_post

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    mock_httpx_cls = MagicMock(return_value=mock_cm)

    import httpx as _real_httpx
    original_async_client = _real_httpx.AsyncClient
    try:
        _real_httpx.AsyncClient = mock_httpx_cls  # type: ignore[assignment]
        with (
            patch("services.integrations.operator_notify.http_client", None),
            patch(
                "services.integrations.operator_notify.notify_operator",
                new=notify_mock,
            ),
        ):
            await manager._validate_ollama_model_settings(pool)
    finally:
        _real_httpx.AsyncClient = original_async_client  # type: ignore[assignment]

    return notify_mock


# ---------------------------------------------------------------------------
# Test 1: All installed -- no notify
# ---------------------------------------------------------------------------


class TestAllModelsOk:
    @pytest.mark.asyncio
    async def test_all_installed_no_notify(self):
        notify = await _run_validator(
            model_rows=[
                {"key": "pipeline_writer_model", "value": "ollama/gemma3:27b"},
                {"key": "cost_tier.standard.model", "value": "ollama/gemma3:27b"},
            ],
            tags_data={"models": [{"name": "gemma3:27b"}]},
            show_data={"template": _ESTABLISHED_TEMPLATE},
        )
        notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_ollama_models_skipped(self):
        """openai/ prefixed models are skipped entirely -- no missing warning."""
        notify = await _run_validator(
            model_rows=[
                {"key": "some_model", "value": "openai/gpt-4o"},
            ],
            tags_data={"models": []},  # empty -- would fail if openai model checked
        )
        notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_model_rows_no_notify(self):
        """No configured model keys means nothing to validate."""
        notify = await _run_validator(
            model_rows=[],
            tags_data={"models": [{"name": "gemma3:27b"}]},
        )
        notify.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: Missing model -- notify called with model name
# ---------------------------------------------------------------------------


class TestMissingModel:
    @pytest.mark.asyncio
    async def test_missing_model_triggers_notify(self):
        notify = await _run_validator(
            model_rows=[
                {"key": "cost_tier.standard.model", "value": "ollama/missing-model:latest"},
            ],
            tags_data={"models": [{"name": "other-model:latest"}]},
        )
        notify.assert_called_once()
        call_args = notify.call_args[0][0]  # positional first arg is the message
        assert "missing-model:latest" in call_args

    @pytest.mark.asyncio
    async def test_missing_model_message_contains_model_name(self):
        notify = await _run_validator(
            model_rows=[
                {"key": "pipeline_writer_model", "value": "ollama/gemma-4-31B-it-qat:latest"},
            ],
            tags_data={"models": []},
        )
        notify.assert_called_once()
        msg = notify.call_args[0][0]
        assert "gemma-4-31B-it-qat:latest" in msg

    @pytest.mark.asyncio
    async def test_ollama_prefix_stripped_before_lookup(self):
        """'ollama/gemma3:27b' should match the installed model 'gemma3:27b'."""
        notify = await _run_validator(
            model_rows=[
                {"key": "pipeline_writer_model", "value": "ollama/gemma3:27b"},
            ],
            tags_data={"models": [{"name": "gemma3:27b"}]},
            show_data={"template": _ESTABLISHED_TEMPLATE},
        )
        notify.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: Suspect template -- notify called
# ---------------------------------------------------------------------------


class TestSuspectTemplate:
    @pytest.mark.asyncio
    async def test_suspect_template_triggers_notify(self):
        notify = await _run_validator(
            model_rows=[
                {"key": "cost_tier.standard.model", "value": "ollama/bad-template:latest"},
            ],
            tags_data={"models": [{"name": "bad-template:latest"}]},
            show_data={"template": _SUSPECT_TEMPLATE},
        )
        notify.assert_called_once()
        msg = notify.call_args[0][0]
        assert "bad-template" in msg

    @pytest.mark.asyncio
    async def test_good_template_no_notify(self):
        notify = await _run_validator(
            model_rows=[
                {"key": "cost_tier.standard.model", "value": "ollama/good-model:latest"},
            ],
            tags_data={"models": [{"name": "good-model:latest"}]},
            show_data={"template": _ESTABLISHED_TEMPLATE},
        )
        notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_suspect_tokens_with_established_delimiter_ok(self):
        """A template with <|im_turn|> AND <start_of_turn> is not flagged."""
        combined = "<start_of_turn>user\n{{ .Input }}<|im_turn|>end"
        notify = await _run_validator(
            model_rows=[
                {"key": "cost_tier.standard.model", "value": "ollama/combo-model:latest"},
            ],
            tags_data={"models": [{"name": "combo-model:latest"}]},
            show_data={"template": combined},
        )
        notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_im_start_delimiter_not_flagged(self):
        """<|im_start|> is established; <|im_turn|> alongside it is OK."""
        template_with_im_start = "<|im_start|>user\n{{ .Input }}<|im_turn|>foo"
        notify = await _run_validator(
            model_rows=[
                {"key": "cost_tier.standard.model", "value": "ollama/im-start-model:latest"},
            ],
            tags_data={"models": [{"name": "im-start-model:latest"}]},
            show_data={"template": template_with_im_start},
        )
        notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_gemma4_turn_template_not_flagged(self):
        """Regression: Gemma 4's <|turn>/<turn|> DSL is legitimate, not suspect.

        gemma-4-31B-it-qat declares `stop "<|turn>"` / `stop "<turn|>"` in its
        own Ollama parameters (family=gemma4). Before this was recognised, every
        boot warned about a model backing 20 *_model settings -- a pure false
        positive that trains operators to ignore startup warnings.
        """
        notify = await _run_validator(
            model_rows=[
                {
                    "key": "pipeline_local_writer_model",
                    "value": "ollama/gemma-4-31B-it-qat:latest",
                },
            ],
            tags_data={"models": [{"name": "gemma-4-31B-it-qat:latest"}]},
            show_data={"template": _GEMMA4_TEMPLATE},
        )
        notify.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: Ollama unreachable -- warning logged, notify called, no hard-fail
# ---------------------------------------------------------------------------


class TestOllamaUnreachable:
    @pytest.mark.asyncio
    async def test_connection_error_does_not_raise(self):
        """A ConnectionError from Ollama must NOT propagate -- only warns + notifies."""
        import httpx

        notify = await _run_validator(
            model_rows=[
                {"key": "pipeline_writer_model", "value": "ollama/gemma3:27b"},
            ],
            tags_data={},  # not reached
            ollama_raises=httpx.ConnectError("Connection refused"),
        )
        notify.assert_called_once()
        msg = notify.call_args[0][0]
        assert "unreachable" in msg.lower() or "cannot validate" in msg.lower()

    @pytest.mark.asyncio
    async def test_timeout_error_does_not_raise(self):
        import httpx

        notify = await _run_validator(
            model_rows=[{"key": "pipeline_writer_model", "value": "ollama/gemma3:27b"}],
            tags_data={},
            ollama_raises=httpx.TimeoutException("timed out"),
        )
        notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_generic_exception_does_not_raise(self):
        notify = await _run_validator(
            model_rows=[{"key": "pipeline_writer_model", "value": "ollama/gemma3:27b"}],
            tags_data={},
            ollama_raises=OSError("network failure"),
        )
        notify.assert_called_once()


# ---------------------------------------------------------------------------
# Test 5: Validation disabled -- no checks run at all
# ---------------------------------------------------------------------------


class TestValidationDisabled:
    @pytest.mark.asyncio
    async def test_disabled_setting_skips_all_checks(self):
        pool, conn = _make_pool_rows([])
        sc = _make_site_config({"ollama_model_validation_enabled": "false"})
        manager = _make_manager(sc)

        notify_mock = AsyncMock()
        with patch(
            "services.integrations.operator_notify.notify_operator",
            new=notify_mock,
        ):
            await manager._validate_ollama_model_settings(pool)

        # DB was never queried (validation exited early)
        conn.fetch.assert_not_called()
        notify_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_disabled_via_zero_value(self):
        pool, conn = _make_pool_rows([])
        sc = _make_site_config({"ollama_model_validation_enabled": "0"})
        manager = _make_manager(sc)

        notify_mock = AsyncMock()
        with patch(
            "services.integrations.operator_notify.notify_operator",
            new=notify_mock,
        ):
            await manager._validate_ollama_model_settings(pool)

        conn.fetch.assert_not_called()
        notify_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_site_config_skips_silently(self):
        """Without a SiteConfig, the validator returns early without error."""
        pool, conn = _make_pool_rows([])
        manager = StartupManager(site_config=None)

        notify_mock = AsyncMock()
        with patch(
            "services.integrations.operator_notify.notify_operator",
            new=notify_mock,
        ):
            await manager._validate_ollama_model_settings(pool)

        conn.fetch.assert_not_called()
        notify_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Non-Ollama filtering (Glad-Labs/poindexter#941)
#
# Measured on the live DB 2026-07-29, the unfiltered validator reported 16
# missing models of which 15 were false positives — burying the single real
# finding. These pin each root cause so the noise can't creep back.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOllamaValueClassification:
    """Unit-level checks on the value/key classifier itself."""

    def test_ollama_prefixed_values_are_checked(self):
        from utils.startup_manager import _is_ollama_model_value

        assert _is_ollama_model_value(
            "pipeline_writer_model", "ollama/gemma-4-31B-it-qat:latest",
            skip_keys=frozenset(),
        )

    def test_other_provider_namespaces_are_skipped(self):
        """A namespaced value declares its own backend. The old code carried an
        allowlist of cloud prefixes, which could only ever recognise providers
        someone had already been bitten by — HuggingFace orgs sailed through
        and were reported as missing Ollama models."""
        from utils.startup_manager import _is_ollama_model_value

        for value in (
            "anthropic/claude-sonnet-5",             # another LLM provider
            "Systran/faster-whisper-medium",         # speaches / HF repo
            "Wan-AI/Wan2.2-TI2V-5B",                 # wan-server / HF repo
            "speaches-ai/Kokoro-82M-v1.0-ONNX",      # speaches / HF repo
            "cross-encoder/ms-marco-MiniLM-L-6-v2",  # sentence-transformers
        ):
            assert not _is_ollama_model_value(
                "some_model", value, skip_keys=frozenset()
            ), value

    def test_sentinel_values_are_skipped(self):
        """`auto` selects a model at runtime — there is nothing to look up."""
        from utils.startup_manager import _is_ollama_model_value

        assert not _is_ollama_model_value(
            "default_ollama_model", "auto", skip_keys=frozenset()
        )

    def test_known_non_ollama_bare_keys_are_skipped(self):
        """Bare values can't be classified by inspection, so the key decides.
        gpu_model is the clearest case: it holds a hardware description, and
        was being reported as a missing LLM."""
        from utils.startup_manager import _NON_OLLAMA_MODEL_KEYS, _is_ollama_model_value

        assert "gpu_model" in _NON_OLLAMA_MODEL_KEYS
        assert not _is_ollama_model_value(
            "gpu_model", "NVIDIA RTX 5090 (32GB VRAM)",
            skip_keys=_NON_OLLAMA_MODEL_KEYS,
        )
        assert not _is_ollama_model_value(
            "image_model", "z_image_turbo", skip_keys=_NON_OLLAMA_MODEL_KEYS,
        )

    def test_unknown_bare_keys_are_still_checked(self):
        """Default-on for unrecognised bare keys: a NEW Ollama model setting
        must be validated without anyone remembering to register it. Silence
        on a real missing model is worse than one false positive."""
        from utils.startup_manager import _NON_OLLAMA_MODEL_KEYS, _is_ollama_model_value

        assert _is_ollama_model_value(
            "some_new_writer_model", "llama3.2:3b",
            skip_keys=_NON_OLLAMA_MODEL_KEYS,
        )

    def test_operator_skip_list_extends_the_builtin(self):
        from utils.startup_manager import _NON_OLLAMA_MODEL_KEYS, _is_ollama_model_value

        extended = _NON_OLLAMA_MODEL_KEYS | {"my_custom_backend_model"}
        assert not _is_ollama_model_value(
            "my_custom_backend_model", "something", skip_keys=extended,
        )

    def test_checkpoint_filenames_are_skipped(self):
        """A weights-file suffix names a checkpoint on a sidecar's disk, never
        an Ollama tag. The ComfyUI keys were the live false positives: three
        MISSING warnings per boot against /api/tags for files that live in
        ComfyUI's models directory."""
        from utils.startup_manager import _is_ollama_model_value

        for key, value in (
            ("video_comfyui_high_model", "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"),
            ("video_comfyui_low_model", "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"),
            ("image_fanout_qwen_model", "qwen_image_2512_fp8_e4m3fn.safetensors"),
            ("some_future_comfyui_model", "SDXL-BASE.CKPT"),  # case-insensitive
        ):
            assert not _is_ollama_model_value(
                key, value, skip_keys=frozenset()
            ), f"{key}={value} should be classified as a checkpoint file"

    def test_checkpoint_rule_does_not_widen_to_tag_lookalikes(self):
        """An Ollama tag that merely CONTAINS a dot (`llama3.2:3b`,
        `qwen2.5-coder:14b`) must still be validated — only a terminal
        weights-file suffix opts out."""
        from utils.startup_manager import _is_ollama_model_value

        for value in ("llama3.2:3b", "qwen2.5-coder:14b", "nomic-embed-text"):
            assert _is_ollama_model_value(
                "some_writer_model", value, skip_keys=frozenset()
            ), f"{value} should still be validated"


@pytest.mark.unit
class TestOllamaNameVariants:
    def test_untagged_name_matches_latest(self):
        """Ollama's /api/tags always reports an explicit tag, so a bare
        `nomic-embed-text` never string-matched the installed
        `nomic-embed-text:latest` — reported missing while sitting right
        there."""
        from utils.startup_manager import _ollama_name_variants

        assert "nomic-embed-text:latest" in _ollama_name_variants("nomic-embed-text")

    def test_latest_tag_matches_untagged(self):
        from utils.startup_manager import _ollama_name_variants

        assert "nomic-embed-text" in _ollama_name_variants("nomic-embed-text:latest")

    def test_explicit_non_latest_tag_is_not_widened(self):
        """`phi4:14b` must NOT be treated as equivalent to bare `phi4` — a
        different tag is a different model, and widening would hide a genuine
        wrong-tag misconfiguration."""
        from utils.startup_manager import _ollama_name_variants

        assert _ollama_name_variants("phi4:14b") == {"phi4:14b"}


@pytest.mark.unit
class TestValidatorNoiseSuppression:
    @pytest.mark.asyncio
    async def test_non_ollama_values_are_not_reported_missing(self):
        """The regression this all exists for: a boot where every non-Ollama
        backend is configured must produce NO warning."""
        notify = await _run_validator(
            model_rows=[
                {"key": "gpu_model", "value": "NVIDIA RTX 5090 (32GB VRAM)"},
                {"key": "image_model", "value": "z_image_turbo"},
                {"key": "generative_video_model", "value": "Wan-AI/Wan2.2-TI2V-5B"},
                {"key": "podcast_tts_model", "value": "speaches-ai/Kokoro-82M-v1.0-ONNX"},
                {"key": "rag_rerank_model", "value": "cross-encoder/ms-marco-MiniLM-L-6-v2"},
                {"key": "voice_agent_stt_model", "value": "Systran/faster-whisper-medium"},
                {"key": "default_ollama_model", "value": "auto"},
                {"key": "pipeline_writer_model", "value": "anthropic/claude-sonnet-5"},
            ],
            tags_data={"models": [{"name": "llama3.2:3b"}]},
        )
        notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_untagged_installed_model_is_not_reported_missing(self):
        notify = await _run_validator(
            model_rows=[{"key": "embed_model", "value": "nomic-embed-text"}],
            tags_data={"models": [{"name": "nomic-embed-text:latest"}]},
            show_data={"template": _GOOD_TEMPLATE},
        )
        notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_real_missing_ollama_model_still_reports(self):
        """The point of the filtering is that THIS survives — it was the one
        true finding lost among 15 false positives."""
        notify = await _run_validator(
            model_rows=[
                {"key": "gpu_model", "value": "NVIDIA RTX 5090 (32GB VRAM)"},
                {"key": "voice_agent_llm_model", "value": "ollama/gemma-4-E2B-Q2:latest"},
            ],
            tags_data={"models": [{"name": "llama3.2:3b"}]},
        )
        notify.assert_called_once()
        msg = notify.call_args[0][0]
        assert "gemma-4-E2B-Q2:latest" in msg
        assert "NVIDIA" not in msg, "hardware string must not appear as a model"

    @pytest.mark.asyncio
    async def test_one_model_across_many_keys_reports_once(self):
        """Report per MODEL, not per key. The writer model is referenced by
        ~20 keys, which printed the same suspect-template line 20 times."""
        notify = await _run_validator(
            model_rows=[
                {"key": f"pipeline_step{i}_model", "value": "ollama/ghost:latest"}
                for i in range(5)
            ],
            tags_data={"models": [{"name": "llama3.2:3b"}]},
        )
        notify.assert_called_once()
        msg = notify.call_args[0][0]
        assert msg.count("ghost:latest") == 1, msg
