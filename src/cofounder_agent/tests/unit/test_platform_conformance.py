"""Conformance test — ``KernelPlatform`` and ``FakePlatform`` present the SAME
``Platform`` contract (the fake-kernel-drift guard, Wave 1, Glad-Labs/poindexter#667).

Every test runs against BOTH backings via the parametrized ``platform`` fixture,
so a module that passes against ``FakePlatform`` behaves the same against the
real ``KernelPlatform``. If the two ever drift, this test fails.

(asyncio runs in ``Mode.AUTO`` per the repo's pytest config, so ``async def``
tests are collected without an explicit marker.)
"""

from __future__ import annotations

import asyncio

import pytest

from plugins.fake_platform import FakePlatform
from plugins.kernel_platform import KernelPlatform
from plugins.platform import Capability, CapabilityError, Platform, ScopedPlatform

# --- stub kernel services for the KernelPlatform variant ----------------------


class _StubSiteConfig:
    def __init__(self, values: dict[str, object], secrets: dict[str, str]) -> None:
        self._values = values
        self._secrets = secrets

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    # Typed getters mirror services.site_config.SiteConfig — the kernel adapter
    # delegates straight to these.
    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(str(self.get(key, str(default))))
        except (ValueError, TypeError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(str(self.get(key, str(default))))
        except (ValueError, TypeError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        return str(self.get(key, str(default))).lower() in ("true", "1", "yes", "on")

    async def get_secret(self, key: str, default: str | None = None) -> str | None:
        return self._secrets.get(key, default)


class _StubConn:
    async def execute(self, *args: object) -> str:
        return "OK"


class _StubAcquire:
    async def __aenter__(self) -> _StubConn:
        return _StubConn()

    async def __aexit__(self, *exc: object) -> None:
        return None


class _StubPool:
    def acquire(self) -> _StubAcquire:
        return _StubAcquire()


def _make_kernel_platform() -> KernelPlatform:
    async def _dispatch(*args: object, **kwargs: object) -> str:
        return "completion"

    async def _audit_write(
        event_type: str,
        *,
        source: str,
        details: dict[str, object] | None = None,
        task_id: str | None = None,
        severity: str = "info",
    ) -> None:
        return None

    def _metric_emit(name: str, value: float = 1.0, **labels: str) -> None:
        return None

    return KernelPlatform(
        site_config=_StubSiteConfig(
            {"k": "v", "port": "8080", "flag": "true", "ratio": "1.5"},
            {"s": "sv"},
        ),
        pool=_StubPool(),
        dispatch=_dispatch,
        audit_write=_audit_write,
        metric_emit=_metric_emit,
    )


@pytest.fixture(params=["kernel", "fake"])
def platform(request: pytest.FixtureRequest) -> Platform:
    if request.param == "fake":
        return FakePlatform(
            config={"k": "v", "port": "8080", "flag": "true", "ratio": "1.5"},
            secrets={"s": "sv"},
            dispatch_response="completion",
        )
    return _make_kernel_platform()


# --- the shared contract, exercised against both backings ---------------------


def test_satisfies_platform_protocol(platform: Platform) -> None:
    assert isinstance(platform, Platform)


def test_config_reads_value(platform: Platform) -> None:
    assert platform.config.get("k") == "v"
    assert platform.config.get("missing", "fallback") == "fallback"


def test_config_get_int(platform: Platform) -> None:
    # Typed getter (Wave 3e) — the gap that blocked content's config migration.
    assert platform.config.get_int("port") == 8080
    assert platform.config.get_int("missing", 42) == 42
    # Non-int value falls back to the default, never raises.
    assert platform.config.get_int("k", 7) == 7


def test_config_get_bool(platform: Platform) -> None:
    assert platform.config.get_bool("flag") is True
    assert platform.config.get_bool("missing") is False
    assert platform.config.get_bool("missing", True) is True


def test_config_get_float(platform: Platform) -> None:
    assert platform.config.get_float("ratio") == 1.5
    assert platform.config.get_float("missing", 2.5) == 2.5


async def test_secret_reads_value(platform: Platform) -> None:
    assert await platform.secret.get("s") == "sv"


async def test_dispatch_completes(platform: Platform) -> None:
    assert await platform.dispatch.complete(prompt="hi") == "completion"


async def test_db_acquire_yields_connection(platform: Platform) -> None:
    async with platform.db.acquire() as conn:
        assert conn is not None


def test_log_is_callable(platform: Platform) -> None:
    platform.log("hello", run="x")  # must not raise


def test_metric_is_callable(platform: Platform) -> None:
    platform.metric("count", 1.0, unit="n")  # must not raise


async def test_audit_write(platform: Platform) -> None:
    # The production audit shape: event_type + source + structured details
    # (+ optional task_id/severity). Must not raise on either backing.
    await platform.audit.write(
        "thing_happened", source="conformance_test", details={"detail": 1}
    )


def test_scoped_wrapper_works_over_either_backing(platform: Platform) -> None:
    # The capability-scoping wrapper behaves identically over either backing.
    scoped = ScopedPlatform(platform, {Capability.CONFIG})
    assert scoped.config.get("k") == "v"
    with pytest.raises(CapabilityError):
        _ = scoped.audit


async def test_fake_audit_records_production_fields() -> None:
    # FakePlatform records every audit field so a module's test can assert on
    # ``fake.audit.writes`` after migrating a stage off its raw INSERT.
    fake = FakePlatform()
    await fake.audit.write(
        "image_style_picked",
        source="stages.source_featured_image",
        details={"style": "cyberpunk"},
        task_id="task-1",
        severity="info",
    )
    assert fake.audit.writes == [
        {
            "event_type": "image_style_picked",
            "source": "stages.source_featured_image",
            "details": {"style": "cyberpunk"},
            "task_id": "task-1",
            "severity": "info",
        }
    ]


async def test_audit_write_bg_is_non_blocking(platform: Platform) -> None:
    # Fire-and-forget audit (Wave 3c): write_bg returns None *synchronously*
    # (it is not a coroutine) and must never raise on either backing — a
    # telemetry write must never slow or break the pipeline.
    result = platform.audit.write_bg(
        "bg_event", source="conformance_test", details={"detail": 1}
    )
    assert result is None
    # Let the kernel backing's scheduled task run to completion so it doesn't
    # linger as a pending task when the test loop closes.
    await asyncio.sleep(0)


async def test_fake_audit_write_bg_records_production_fields() -> None:
    # FakePlatform records fire-and-forget writes separately so a migrated
    # best-effort site can assert on ``fake.audit.writes_bg``.
    fake = FakePlatform()
    fake.audit.write_bg(
        "qa_pass_completed",
        source="qa.aggregate",
        details={"approved": True},
        task_id="task-2",
        severity="info",
    )
    assert fake.audit.writes_bg == [
        {
            "event_type": "qa_pass_completed",
            "source": "qa.aggregate",
            "details": {"approved": True},
            "task_id": "task-2",
            "severity": "info",
        }
    ]


def test_audit_write_bg_drops_without_event_loop(platform: Platform) -> None:
    # Called outside an event loop (sync context), the kernel backing can't
    # schedule the task — it must drop quietly (return None), never raise,
    # mirroring audit_log_bg's no-running-loop drop.
    assert platform.audit.write_bg("no_loop", source="sync_ctx") is None
