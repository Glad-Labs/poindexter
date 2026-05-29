"""
Unit tests for services/decorators.py

Tests the log_query_performance decorator. The tests verify
that the decorator:
- Correctly pass-through return values from the wrapped function
- Re-raises exceptions without swallowing them
- Respects the ENABLE_QUERY_MONITORING env var toggle
- Handles both list and dict return values for result_count inference

All tests are pure async — no DB or network calls.
"""

import os

import pytest

# Ensure monitoring is enabled for all tests (override env before import)
os.environ.setdefault("ENABLE_QUERY_MONITORING", "true")

from services.decorators import log_query_performance

# ---------------------------------------------------------------------------
# log_query_performance
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLogQueryPerformance:
    @pytest.mark.asyncio
    async def test_returns_value_from_wrapped_function(self):
        @log_query_performance(operation="test_op", category="test")
        async def fetch_list():
            return [1, 2, 3]

        result = await fetch_list()
        assert result == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_works_with_dict_return_value(self):
        @log_query_performance(operation="test_dict_op", category="test")
        async def fetch_dict():
            return {"results": ["a", "b"], "total": 2}

        result = await fetch_dict()
        assert result["total"] == 2
        assert result["results"] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_works_with_none_return_value(self):
        @log_query_performance(operation="test_none_op", category="test")
        async def fetch_none():
            return None

        result = await fetch_none()
        assert result is None

    @pytest.mark.asyncio
    async def test_re_raises_exception(self):
        @log_query_performance(operation="failing_op", category="test")
        async def failing_query():
            raise ValueError("db error")

        with pytest.raises(ValueError, match="db error"):
            await failing_query()

    @pytest.mark.asyncio
    async def test_passes_kwargs_to_wrapped_function(self):
        @log_query_performance(operation="with_kwargs", category="test")
        async def query_with_params(limit: int = 10, offset: int = 0):
            return {"limit": limit, "offset": offset}

        result = await query_with_params(limit=5, offset=20)
        assert result == {"limit": 5, "offset": 20}

    @pytest.mark.asyncio
    async def test_passes_args_to_wrapped_function(self):
        @log_query_performance(operation="with_args", category="test")
        async def query_with_args(task_id: str):
            return {"id": task_id}

        result = await query_with_args("abc-123")
        assert result == {"id": "abc-123"}

    @pytest.mark.asyncio
    async def test_custom_slow_threshold_does_not_break(self):
        @log_query_performance(operation="fast_op", category="test", slow_threshold_ms=1000)
        async def fast_query():
            return "ok"

        result = await fast_query()
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_monitoring_disabled_still_returns_value(self, monkeypatch):
        # Monitoring is gated on _enable_query_monitoring() which reads
        # from site_config. Patch the helper directly — previously this
        # patched a module-level constant that was captured at import
        # time (the bug fixed in this file).
        monkeypatch.setattr("services.decorators._enable_query_monitoring", lambda: False)

        @log_query_performance(operation="disabled_op", category="test")
        async def fast_query():
            return "bypassed"

        result = await fast_query()
        assert result == "bypassed"

    @pytest.mark.asyncio
    async def test_filters_sensitive_kwargs(self):
        """Sensitive kwargs like 'password' must not surface in logs — no exception."""

        @log_query_performance(operation="sensitive_op", category="test")
        async def secure_query(username: str, password: str):
            return username

        result = await secure_query(username="admin", password="secret")
        assert result == "admin"

    @pytest.mark.asyncio
    async def test_dict_return_with_total_key(self):
        """Decorator should infer result_count from dict['total']."""

        @log_query_performance(operation="total_key_op", category="test")
        async def count_query():
            return {"total": 42, "data": []}

        result = await count_query()
        assert result["total"] == 42


# ---------------------------------------------------------------------------
# Logging-branch coverage — the existing class only asserts on return values,
# so the slow/error/info/debug log paths in lines 177-196 of services/decorators.py
# had zero coverage before this class. Each test patches the module logger and
# verifies the exact branch fires.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLogQueryPerformanceLogging:
    @pytest.mark.asyncio
    async def test_slow_query_emits_warning(self, monkeypatch):
        """A query exceeding the slow_threshold_ms must hit logger.warning
        with the SLOW QUERY marker — never log.info or log.error."""
        from unittest.mock import MagicMock

        mock_logger = MagicMock()
        monkeypatch.setattr("services.decorators.logger", mock_logger)

        # slow_threshold_ms=0 guarantees every call is "slow"
        @log_query_performance(operation="forced_slow", category="test", slow_threshold_ms=0)
        async def any_query():
            return "ok"

        await any_query()

        assert mock_logger.warning.called, "expected logger.warning on slow path"
        warn_msg = mock_logger.warning.call_args.args[0]
        assert "SLOW QUERY" in warn_msg
        assert "forced_slow" in warn_msg
        mock_logger.error.assert_not_called()
        mock_logger.info.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_path_logs_with_exc_info(self, monkeypatch):
        """When the wrapped fn raises, logger.error must be called with
        exc_info set to the captured exception (not True, not None)."""
        from unittest.mock import MagicMock

        mock_logger = MagicMock()
        monkeypatch.setattr("services.decorators.logger", mock_logger)

        sentinel = RuntimeError("boom")

        @log_query_performance(operation="explodes", category="test")
        async def explodes():
            raise sentinel

        with pytest.raises(RuntimeError):
            await explodes()

        assert mock_logger.error.called
        kwargs = mock_logger.error.call_args.kwargs
        assert kwargs.get("exc_info") is sentinel, (
            "decorator must pass the captured exception explicitly (LOG014 contract)"
        )
        # error context must include error=True flag
        assert kwargs["extra"]["error"] is True
        assert kwargs["extra"]["operation"] == "explodes"

    @pytest.mark.asyncio
    async def test_log_all_queries_emits_info_for_fast_query(self, monkeypatch):
        """When log_all_queries=True and the query is fast, logger.info
        must fire — not debug."""
        from unittest.mock import MagicMock

        mock_logger = MagicMock()
        monkeypatch.setattr("services.decorators.logger", mock_logger)
        monkeypatch.setattr("services.decorators._log_all_queries", lambda: True)

        @log_query_performance(operation="chatty_op", category="test")
        async def fast():
            return "ok"

        await fast()

        assert mock_logger.info.called
        assert not mock_logger.warning.called
        assert not mock_logger.error.called

    @pytest.mark.asyncio
    async def test_fast_query_default_logs_debug_not_info(self, monkeypatch):
        """The default (log_all_queries=False, fast query) hits logger.debug
        — the quiet branch on line 194. Regression guard for that path."""
        from unittest.mock import MagicMock

        mock_logger = MagicMock()
        monkeypatch.setattr("services.decorators.logger", mock_logger)
        monkeypatch.setattr("services.decorators._log_all_queries", lambda: False)

        @log_query_performance(operation="quiet_op", category="test")
        async def fast():
            return "ok"

        await fast()

        assert mock_logger.debug.called
        assert not mock_logger.info.called
        assert not mock_logger.warning.called

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sensitive_key", ["token", "secret", "api_key"])
    async def test_filters_each_sensitive_kwarg(self, monkeypatch, sensitive_key):
        """Each entry in the sensitive-kwargs blocklist must be stripped
        from logger context. Only `password` was tested before this."""
        from unittest.mock import MagicMock

        mock_logger = MagicMock()
        monkeypatch.setattr("services.decorators.logger", mock_logger)
        # Force the info branch so kwargs make it into `extra`
        monkeypatch.setattr("services.decorators._log_all_queries", lambda: True)

        @log_query_performance(operation="sensitive", category="test")
        async def safe_call(**kwargs):
            return "ok"

        await safe_call(visible="yes", **{sensitive_key: "leak-me-not"})

        extra = mock_logger.info.call_args.kwargs["extra"]
        params = extra.get("params", {})
        assert sensitive_key not in params, f"{sensitive_key} leaked into log context"
        assert params.get("visible") == "yes", "non-sensitive kwargs must survive"

    @pytest.mark.asyncio
    async def test_empty_list_result_count_is_zero(self, monkeypatch):
        """An empty list return value yields result_count=0, not absent.
        Distinguishes "no rows" from "couldn't compute"."""
        from unittest.mock import MagicMock

        mock_logger = MagicMock()
        monkeypatch.setattr("services.decorators.logger", mock_logger)
        monkeypatch.setattr("services.decorators._log_all_queries", lambda: True)

        @log_query_performance(operation="empty_list", category="test")
        async def no_rows():
            return []

        await no_rows()

        extra = mock_logger.info.call_args.kwargs["extra"]
        assert extra.get("result_count") == 0

    @pytest.mark.asyncio
    async def test_dict_with_non_list_results_skips_result_count(self, monkeypatch):
        """If `results` is present but not a list, result_count must NOT
        be set (defensive — no len() on a non-sized object)."""
        from unittest.mock import MagicMock

        mock_logger = MagicMock()
        monkeypatch.setattr("services.decorators.logger", mock_logger)
        monkeypatch.setattr("services.decorators._log_all_queries", lambda: True)

        @log_query_performance(operation="weird_dict", category="test")
        async def weird():
            return {"results": "not-a-list"}

        await weird()

        extra = mock_logger.info.call_args.kwargs["extra"]
        assert "result_count" not in extra

    @pytest.mark.asyncio
    async def test_no_kwargs_omits_params_key(self, monkeypatch):
        """When called with only positional args, the log extra must not
        include a `params` key (avoids logging empty dicts)."""
        from unittest.mock import MagicMock

        mock_logger = MagicMock()
        monkeypatch.setattr("services.decorators.logger", mock_logger)
        monkeypatch.setattr("services.decorators._log_all_queries", lambda: True)

        @log_query_performance(operation="positional_only", category="test")
        async def pos(a, b):
            return a + b

        await pos(1, 2)

        extra = mock_logger.info.call_args.kwargs["extra"]
        assert "params" not in extra

    @pytest.mark.asyncio
    async def test_explicit_threshold_overrides_site_config(self, monkeypatch):
        """slow_threshold_ms passed at decoration time must win over the
        site_config setting — confirms the per-decorator override path."""
        from unittest.mock import MagicMock

        mock_logger = MagicMock()
        monkeypatch.setattr("services.decorators.logger", mock_logger)
        # site_config would report 999999 (never slow), but the decorator
        # override is 0 (always slow) — override must win.
        monkeypatch.setattr("services.decorators._slow_query_threshold_ms", lambda: 999_999)

        @log_query_performance(operation="override_wins", category="test", slow_threshold_ms=0)
        async def fn():
            return "ok"

        await fn()

        assert mock_logger.warning.called
        warn_extra = mock_logger.warning.call_args.kwargs["extra"]
        assert warn_extra["slow"] is True

    def test_set_default_decorators_rewires_module_facade(self):
        """``set_default_decorators(...)`` must replace the module-level
        ``_default_decorators`` instance so subsequent module-level
        ``_enable_query_monitoring()`` reads see the new SiteConfig.

        Regression guard for the SiteConfig DI migration PR 6 facade
        (replaces the post-#330 ``set_site_config`` seam)."""
        from services import decorators as dec_mod
        from services.decorators import Decorators, set_default_decorators
        from services.site_config import SiteConfig

        original = dec_mod._default_decorators
        try:
            sentinel_cfg = SiteConfig(
                initial_config={"enable_query_monitoring": "false"}
            )
            sentinel = Decorators(site_config=sentinel_cfg)
            set_default_decorators(sentinel)
            assert dec_mod._default_decorators is sentinel
            # The new instance has monitoring disabled — verify the
            # module-level convenience reader picks that up.
            assert dec_mod._enable_query_monitoring() is False
        finally:
            set_default_decorators(original)

    def test_decorators_constructor_requires_site_config(self):
        """Decorators(site_config=None) must raise TypeError loudly.

        Fail-loud invariant from the SiteConfig DI migration: no silent
        empty-SiteConfig fallback at the class boundary."""
        from services.decorators import Decorators

        with pytest.raises(TypeError, match="SiteConfig"):
            Decorators(site_config=None)  # type: ignore[arg-type]

    def test_decorators_method_form_uses_instance_site_config(self):
        """The method form ``Decorators(...).log_query_performance(...)``
        must read settings from the instance's own SiteConfig, not the
        module-level facade — confirms Option A is still wired."""
        from services.decorators import Decorators
        from services.site_config import SiteConfig

        cfg = SiteConfig(initial_config={"enable_query_monitoring": "false"})
        dec = Decorators(site_config=cfg)

        # Monitoring disabled on the instance's SiteConfig — wrapped fn
        # must run with the bypass path (no timing wrap, raw return).
        called = []

        @dec.log_query_performance(operation="instance_form", category="test")
        async def fn():
            called.append(True)
            return "bypassed"

        async def _drive():
            return await fn()

        # Reuse pytest_asyncio-equivalent invocation via asyncio.run rather
        # than the class-level @pytest.mark.asyncio because this method is
        # synchronous on purpose (the assertion is about Decorator wiring,
        # not async semantics).
        import asyncio

        result = asyncio.run(_drive())
        assert result == "bypassed"
        assert called == [True]

    def test_functools_wraps_preserves_function_metadata(self):
        """@functools.wraps must preserve __name__ and __doc__ so
        tracing / debugging tools see the original function, not `wrapper`."""

        @log_query_performance(operation="meta", category="test")
        async def get_things_by_owner():
            """Fetch all things for an owner."""
            return []

        assert get_things_by_owner.__name__ == "get_things_by_owner"
        assert get_things_by_owner.__doc__ == "Fetch all things for an owner."
