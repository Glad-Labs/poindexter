"""Unit tests for ``services/jobs/sync_prompt_catalog_to_langfuse.py``.

The job keeps Langfuse a read-only mirror of the SKILL.md prompt catalog:
missing keys are created, changed defaults are pushed, hand-edited versions
are replaced + flagged, orphaned names (key gone from the catalog) emit the
``prompt_catalog_drift`` finding. SKILL.md is authoritative; the mirror
never feeds back into resolution (that path is gated off by
``langfuse_prompt_overrides_enabled``).

HTTP is patched at the module seams (``_list_langfuse_prompt_names`` /
``_get_production_version`` / ``_push_prompt_version``); ``emit_finding``
patched so we assert routing intent without touching audit_log.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from services.jobs.sync_prompt_catalog_to_langfuse import (
    SyncPromptCatalogToLangfuseJob,
    _is_sync_owned,
)

_MODULE = "services.jobs.sync_prompt_catalog_to_langfuse"


class _StubConfig:
    """SiteConfig stand-in. An explicit empty ``values`` dict means
    "unconfigured" — a real SiteConfig falls back to env vars (LANGFUSE_*),
    which made an earlier probe test environment-sensitive in CI."""

    def __init__(self, values: dict | None = None, secret: str = "sk-test"):
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


def _catalog(entries: dict[str, str]) -> dict[str, dict[str, str]]:
    return {k: {"template": v, "category": "content"} for k, v in entries.items()}


def _patches(
    catalog: dict[str, str],
    langfuse_names: set[str],
    production_versions: dict[str, dict] | None = None,
):
    """Patch the catalog + the three Langfuse HTTP seams.

    ``production_versions`` maps name -> {prompt, config} for names the
    GET-production seam should return; unknown names return None (404).
    """
    versions = production_versions or {}

    async def _get_version(client, host, name):
        del client, host
        return versions.get(name)

    return (
        patch(f"{_MODULE}._repo_prompt_catalog", return_value=_catalog(catalog)),
        patch(
            f"{_MODULE}._list_langfuse_prompt_names",
            new=AsyncMock(return_value=langfuse_names),
        ),
        patch(f"{_MODULE}._get_production_version", side_effect=_get_version),
        patch(f"{_MODULE}._push_prompt_version", new=AsyncMock()),
        patch(f"{_MODULE}.emit_finding"),
    )


def _synced(body: str) -> dict:
    return {"prompt": body, "config": {"source": "skill_sync"}}


def _legacy_import(body: str) -> dict:
    return {"prompt": body, "config": {"imported_by": "import_prompts_to_langfuse.py"}}


def _hand_edit(body: str) -> dict:
    return {"prompt": body, "config": {}}


class TestSyncPromptCatalogToLangfuseJob:
    async def test_creates_missing_keys(self):
        p_cat, p_list, p_get, p_push, p_emit = _patches(
            {"qa.review": "body A", "seo.generate_title": "body B"},
            set(),  # Langfuse empty
        )
        with p_cat, p_list, p_get, p_push as mock_push, p_emit as mock_emit:
            result = await SyncPromptCatalogToLangfuseJob().run(
                None, {"_site_config": _StubConfig()}
            )
        assert result.ok is True
        assert mock_push.await_count == 2
        pushed_names = {c.args[2] for c in mock_push.await_args_list}
        assert pushed_names == {"qa.review", "seo.generate_title"}
        assert result.changes_made == 2
        assert "2 created" in result.detail
        mock_emit.assert_not_called()

    async def test_updates_sync_owned_version_when_default_changed(self):
        p_cat, p_list, p_get, p_push, p_emit = _patches(
            {"qa.review": "NEW default"},
            {"qa.review"},
            {"qa.review": _synced("OLD default")},
        )
        with p_cat, p_list, p_get, p_push as mock_push, p_emit as mock_emit:
            result = await SyncPromptCatalogToLangfuseJob().run(
                None, {"_site_config": _StubConfig()}
            )
        assert result.ok is True
        mock_push.assert_awaited_once()
        assert "1 updated" in result.detail
        # sync-owned replacement is routine — no finding
        mock_emit.assert_not_called()

    async def test_legacy_import_versions_are_treated_as_sync_owned(self):
        # First-run upgrade path: the retired bulk-import script's versions
        # carry imported_by config — replace them without a hand-edit finding.
        p_cat, p_list, p_get, p_push, p_emit = _patches(
            {"qa.review": "NEW default"},
            {"qa.review"},
            {"qa.review": _legacy_import("stale 2026-05-14 snapshot")},
        )
        with p_cat, p_list, p_get, p_push as mock_push, p_emit as mock_emit:
            result = await SyncPromptCatalogToLangfuseJob().run(
                None, {"_site_config": _StubConfig()}
            )
        assert result.ok is True
        mock_push.assert_awaited_once()
        assert "1 updated" in result.detail
        mock_emit.assert_not_called()

    async def test_hand_edit_is_replaced_and_flagged(self):
        p_cat, p_list, p_get, p_push, p_emit = _patches(
            {"qa.review": "the default"},
            {"qa.review"},
            {"qa.review": _hand_edit("operator's UI edit")},
        )
        with p_cat, p_list, p_get, p_push as mock_push, p_emit as mock_emit:
            result = await SyncPromptCatalogToLangfuseJob().run(
                None, {"_site_config": _StubConfig()}
            )
        assert result.ok is True
        mock_push.assert_awaited_once()  # still replaced — mirror wins
        assert "1 hand-edit(s) replaced" in result.detail
        mock_emit.assert_called_once()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["kind"] == "prompt_catalog_drift"
        assert kwargs["severity"] == "warn"
        assert "qa.review" in kwargs["body"]
        assert kwargs["dedup_key"] == "prompt-mirror-hand-edit-replaced"

    async def test_up_to_date_mirror_is_a_noop(self):
        p_cat, p_list, p_get, p_push, p_emit = _patches(
            {"qa.review": "same body"},
            {"qa.review"},
            {"qa.review": _synced("same body")},
        )
        with p_cat, p_list, p_get, p_push as mock_push, p_emit as mock_emit:
            result = await SyncPromptCatalogToLangfuseJob().run(
                None, {"_site_config": _StubConfig()}
            )
        assert result.ok is True
        assert result.changes_made == 0
        assert "1 up-to-date" in result.detail
        mock_push.assert_not_awaited()
        mock_emit.assert_not_called()

    async def test_orphaned_langfuse_names_emit_finding(self):
        p_cat, p_list, p_get, p_push, p_emit = _patches(
            {"qa.review": "body"},
            {"qa.review", "blog_generation.expand_outline"},
            {"qa.review": _synced("body")},
        )
        with p_cat, p_list, p_get, p_push, p_emit as mock_emit:
            result = await SyncPromptCatalogToLangfuseJob().run(
                None, {"_site_config": _StubConfig()}
            )
        assert result.ok is True
        mock_emit.assert_called_once()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["kind"] == "prompt_catalog_drift"
        assert kwargs["dedup_key"] == "prompt_catalog_drift"
        assert kwargs["extra"]["orphaned"] == ["blog_generation.expand_outline"]
        assert "1 orphaned" in result.detail

    async def test_disabled_skips_everything(self):
        stub = _StubConfig()
        stub._values["langfuse_prompt_mirror_enabled"] = "false"
        p_cat, p_list, p_get, p_push, p_emit = _patches({"a": "x"}, set())
        with p_cat as mock_cat, p_list as mock_list, p_get, p_push, p_emit as mock_emit:
            result = await SyncPromptCatalogToLangfuseJob().run(
                None, {"_site_config": stub}
            )
        assert result.ok is True
        assert result.detail == "mirror disabled"
        mock_cat.assert_not_called()
        mock_list.assert_not_awaited()
        mock_emit.assert_not_called()

    async def test_langfuse_unconfigured_skips_quietly(self):
        sc = _StubConfig(values={}, secret="")
        p_cat, p_list, p_get, p_push, p_emit = _patches({"a": "x"}, set())
        with p_cat, p_list as mock_list, p_get, p_push, p_emit as mock_emit:
            result = await SyncPromptCatalogToLangfuseJob().run(
                None, {"_site_config": sc}
            )
        assert result.ok is True
        assert "not configured" in result.detail
        mock_list.assert_not_awaited()
        mock_emit.assert_not_called()

    async def test_langfuse_failure_returns_not_ok_without_crash(self):
        p_cat, _, p_get, p_push, p_emit = _patches({"a": "x"}, set())
        failing = patch(
            f"{_MODULE}._list_langfuse_prompt_names",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        )
        with p_cat, failing, p_get, p_push, p_emit as mock_emit:
            result = await SyncPromptCatalogToLangfuseJob().run(
                None, {"_site_config": _StubConfig()}
            )
        assert result.ok is False
        assert "langfuse sync failed" in result.detail
        mock_emit.assert_not_called()

    async def test_no_site_config_returns_not_ok(self):
        result = await SyncPromptCatalogToLangfuseJob().run(None, {})
        assert result.ok is False


class TestIsSyncOwned:
    def test_marker_matrix(self):
        assert _is_sync_owned({"source": "skill_sync"}) is True
        assert _is_sync_owned(
            {"imported_by": "import_prompts_to_langfuse.py"}
        ) is True
        assert _is_sync_owned({}) is False
        assert _is_sync_owned({"source": "someone_else"}) is False
