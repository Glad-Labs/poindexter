"""Unit tests for the content-flow concurrency cap (Glad-Labs/poindexter#578).

Stress test 2026-05-31 found that 5 concurrent content pipelines pin the
single 5090 at ~98% VRAM (3 sits at a stable ~60%). The work-pool
``concurrency_limit`` is the native Prefect cap on simultaneous flow runs,
but it had no DB-configurable safe default and no guardrail — an operator
(or a stress test) could set ``prefect_content_flow_concurrency`` to any
value and silently exhaust VRAM.

These tests pin the contract of ``resolve_safe_concurrency`` — the pure
clamp/fail-loud function the deploy script uses to translate the two
DB-configurable settings (``prefect_content_flow_concurrency`` requested
value + ``content_flow_max_concurrency`` hard ceiling) into the value
actually applied to the work pool.
"""

from __future__ import annotations

import pytest

from scripts.deploy_content_flow import (
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_CONCURRENCY,
    resolve_safe_concurrency,
)


class TestResolveSafeConcurrency:
    def test_default_is_safe_for_the_5090(self) -> None:
        """The shipped default must sit in the stable VRAM band (issue #578:
        3 concurrent = ~60% VRAM stable; 5 = ~98% danger)."""
        assert DEFAULT_CONCURRENCY == 3
        assert DEFAULT_MAX_CONCURRENCY == 3

    def test_requested_below_ceiling_is_applied_verbatim(self) -> None:
        assert resolve_safe_concurrency(requested=2, ceiling=3) == 2

    def test_requested_equal_to_ceiling_is_allowed(self) -> None:
        assert resolve_safe_concurrency(requested=3, ceiling=3) == 3

    def test_requested_above_ceiling_fails_loud(self) -> None:
        """The whole point of the issue: a fat-fingered 5 must abort the
        deploy, not silently pin the GPU at 98% VRAM."""
        with pytest.raises(ValueError, match="exceeds"):
            resolve_safe_concurrency(requested=5, ceiling=3)

    def test_operator_can_raise_the_ceiling_to_unlock_higher_concurrency(
        self,
    ) -> None:
        """The ceiling is DB-configurable, not hardcoded — an operator who
        upgrades the GPU raises ``content_flow_max_concurrency`` and the
        higher requested value is then permitted."""
        assert resolve_safe_concurrency(requested=5, ceiling=8) == 5

    @pytest.mark.parametrize("bad", [0, -1, -10])
    def test_non_positive_requested_fails_loud(self, bad: int) -> None:
        with pytest.raises(ValueError, match="positive"):
            resolve_safe_concurrency(requested=bad, ceiling=3)

    @pytest.mark.parametrize("bad", [0, -1])
    def test_non_positive_ceiling_fails_loud(self, bad: int) -> None:
        with pytest.raises(ValueError, match="positive"):
            resolve_safe_concurrency(requested=2, ceiling=bad)


class TestSettingsDefaultsSeedTheCap:
    """The cap must be DB-configurable (config-in-DB principle): both keys
    ship in ``settings_defaults`` so a fresh install gets the safe default
    and the regen-app-settings doc picks them up."""

    def test_both_keys_present_with_safe_defaults(self) -> None:
        from services.settings_defaults import DEFAULTS

        assert DEFAULTS["prefect_content_flow_concurrency"] == "3"
        assert DEFAULTS["content_flow_max_concurrency"] == "3"
