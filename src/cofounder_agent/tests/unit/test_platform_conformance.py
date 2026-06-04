"""Conformance test — ``KernelPlatform`` and ``FakePlatform`` present the SAME
``Platform`` contract (the fake-kernel-drift guard, Wave 1, Glad-Labs/poindexter#667).

Every test runs against BOTH backings via the parametrized ``platform`` fixture,
so a module that passes against ``FakePlatform`` behaves the same against the
real ``KernelPlatform``. If the two ever drift, this test fails.

(asyncio runs in ``Mode.AUTO`` per the repo's pytest config, so ``async def``
tests are collected without an explicit marker.)
"""

from __future__ import annotations

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
        site_config=_StubSiteConfig({"k": "v"}, {"s": "sv"}),
        pool=_StubPool(),
        dispatch=_dispatch,
        audit_write=_audit_write,
        metric_emit=_metric_emit,
    )


@pytest.fixture(params=["kernel", "fake"])
def platform(request: pytest.FixtureRequest) -> Platform:
    if request.param == "fake":
        return FakePlatform(
            config={"k": "v"}, secrets={"s": "sv"}, dispatch_response="completion"
        )
    return _make_kernel_platform()


# --- the shared contract, exercised against both backings ---------------------


def test_satisfies_platform_protocol(platform: Platform) -> None:
    assert isinstance(platform, Platform)


def test_config_reads_value(platform: Platform) -> None:
    assert platform.config.get("k") == "v"
    assert platform.config.get("missing", "fallback") == "fallback"


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
