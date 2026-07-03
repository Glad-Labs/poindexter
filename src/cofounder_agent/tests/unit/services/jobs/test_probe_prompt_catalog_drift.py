"""Unit tests for ``services/jobs/probe_prompt_catalog_drift.py``.

The probe diffs the SKILL.md prompt catalog (via ``UnifiedPromptManager``)
against the Langfuse prompt-name list and emits ONE advisory
``prompt_catalog_drift`` finding (severity ``warn``) for orphaned Langfuse
prompts — names with zero repo references, whose UI edits silently do
nothing. Repo-only keys are the healthy state (Langfuse holds intentional
overrides only) and never alert.

Catalog + Langfuse fetch patched at the module seam; ``emit_finding``
patched so we assert routing intent without touching audit_log.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from services.jobs.probe_prompt_catalog_drift import ProbePromptCatalogDriftJob

_MODULE = "services.jobs.probe_prompt_catalog_drift"


class _StubConfig:
    """SiteConfig stand-in with a Langfuse-configured surface.

    The real ``SiteConfig.get_secret`` needs a pool (or env var); this stub
    keeps the configured-path tests hermetic.
    """

    def __init__(self, values: dict | None = None, secret: str = "sk-test"):
        # `is None` check, not `or` — an explicit empty dict means
        # "unconfigured", which must NOT fall back to the configured defaults.
        self._values = values if values is not None else {
            "langfuse_host": "http://langfuse-web:3000/",
            "langfuse_public_key": "pk-test",
        }
        self._secret = secret

    def get(self, key, default=""):
        return self._values.get(key, default)

    def get_bool(self, key, default=False):
        raw = self._values.get(key)
        if raw is None:
            return default
        return str(raw).strip().lower() in ("1", "true", "yes", "on")

    async def get_secret(self, key, default=""):
        del key  # single-secret stub
        return self._secret or default


def _patches(repo_keys: set[str], langfuse_names: set[str]):
    return (
        patch(f"{_MODULE}._repo_prompt_keys", return_value=repo_keys),
        patch(
            f"{_MODULE}._fetch_langfuse_prompt_names",
            new=AsyncMock(return_value=langfuse_names),
        ),
        patch(f"{_MODULE}.emit_finding"),
    )


class TestProbePromptCatalogDriftJob:
    async def test_emits_finding_for_orphaned_langfuse_prompts(self):
        p_repo, p_lf, p_emit = _patches(
            {"qa.review", "seo.generate_title"},
            {"qa.review", "blog_generation.expand_outline"},
        )
        with p_repo, p_lf, p_emit as mock_emit:
            result = await ProbePromptCatalogDriftJob().run(
                None, {"_site_config": _StubConfig()}
            )
        assert result.ok is True
        mock_emit.assert_called_once()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["kind"] == "prompt_catalog_drift"
        assert kwargs["severity"] == "warn"
        assert "blog_generation.expand_outline" in kwargs["body"]
        assert kwargs["extra"]["orphaned"] == ["blog_generation.expand_outline"]
        # repo-only keys are context, not drift
        assert kwargs["extra"]["repo_only_count"] == 1
        assert kwargs["extra"]["in_both_count"] == 1

    async def test_repo_only_keys_do_not_alert(self):
        # 29-keys-absent-from-Langfuse is the documented healthy state.
        p_repo, p_lf, p_emit = _patches(
            {"qa.review", "seo.generate_title", "topic.ranking"},
            {"qa.review"},
        )
        with p_repo, p_lf, p_emit as mock_emit:
            result = await ProbePromptCatalogDriftJob().run(
                None, {"_site_config": _StubConfig()}
            )
        assert result.ok is True
        mock_emit.assert_not_called()
        assert "2 repo-only" in result.detail

    async def test_disabled_skips_everything(self):
        stub = _StubConfig()
        stub._values["prompt_catalog_drift_probe_enabled"] = "false"
        p_repo, p_lf, p_emit = _patches({"a"}, {"b"})
        with p_repo as mock_repo, p_lf as mock_lf, p_emit as mock_emit:
            result = await ProbePromptCatalogDriftJob().run(
                None, {"_site_config": stub}
            )
        assert result.ok is True
        assert result.detail == "probe disabled"
        mock_repo.assert_not_called()
        mock_lf.assert_not_awaited()
        mock_emit.assert_not_called()

    async def test_langfuse_unconfigured_skips_quietly(self):
        # OSS default — no Langfuse, no second catalog, no noise. Stub with
        # empty values, NOT a bare SiteConfig(): SiteConfig falls back to env
        # vars (LANGFUSE_* via get_secret's os.getenv), so a real instance
        # makes this test environment-sensitive — it failed on the CI runner,
        # which exports Langfuse creds.
        sc = _StubConfig(values={}, secret="")
        p_repo, p_lf, p_emit = _patches({"a"}, set())
        with p_repo, p_lf as mock_lf, p_emit as mock_emit:
            result = await ProbePromptCatalogDriftJob().run(
                None, {"_site_config": sc}
            )
        assert result.ok is True
        assert "not configured" in result.detail
        mock_lf.assert_not_awaited()
        mock_emit.assert_not_called()

    async def test_langfuse_fetch_failure_returns_not_ok_without_crash(self):
        p_repo, _, p_emit = _patches({"a"}, set())
        failing = patch(
            f"{_MODULE}._fetch_langfuse_prompt_names",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        )
        with p_repo, failing, p_emit as mock_emit:
            result = await ProbePromptCatalogDriftJob().run(
                None, {"_site_config": _StubConfig()}
            )
        assert result.ok is False
        assert "langfuse list failed" in result.detail
        mock_emit.assert_not_called()

    async def test_dedup_key_is_stable(self):
        p_repo, p_lf, p_emit = _patches(set(), {"dead.key"})
        with p_repo, p_lf, p_emit as mock_emit:
            await ProbePromptCatalogDriftJob().run(
                None, {"_site_config": _StubConfig()}
            )
        # Stable dedup_key + 1440-min cooldown = at most one page per
        # drift-day, not one per probe cycle.
        assert mock_emit.call_args.kwargs["dedup_key"] == "prompt_catalog_drift"

    async def test_no_site_config_returns_not_ok(self):
        result = await ProbePromptCatalogDriftJob().run(None, {})
        assert result.ok is False
