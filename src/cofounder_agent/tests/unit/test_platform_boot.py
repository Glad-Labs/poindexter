"""Unit tests for the Seam 1 boot wiring (Wave 3b-i, Glad-Labs/poindexter#667).

Pins the two boot-time helpers that take the Platform handle from "fully built
but unused" (Waves 0-3a) to "constructed from live services and bound to every
module" without yet migrating any module onto it:

- ``build_kernel_platform`` — the lifespan factory that adapts the already-
  initialised kernel services (``SiteConfig``, the pool, ``dispatch_complete``,
  the ``AuditLogger``) into a ``KernelPlatform``.
- ``bind_platform_to_modules`` — loops the discovered modules, builds each a
  capability-scoped handle, and binds it; fails loud if a module declares a
  capability the backing platform can't supply.

Plus the ``KernelPlatform`` metric default: with no Prometheus *push* seam yet
(the exporter is pull-based predefined gauges) the ``metric`` capability routes
to the structured log rather than a silent no-op or a fail-loud stub.

Self-contained stubs — the real services need the worker's heavy deps; their
conformance is covered in ``test_platform_conformance.py``.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from plugins.kernel_platform import KernelPlatform, build_kernel_platform
from plugins.module import ModuleManifest
from plugins.platform import Capability, CapabilityError


# --- stub kernel services -----------------------------------------------------


class _StubSiteConfig:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self._values = values or {}

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    async def get_secret(self, key: str, default: str | None = None) -> str | None:
        return None


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


class _RecordingAuditLogger:
    """Stub for the kernel ``AuditLogger`` — records ``log`` calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def log(
        self,
        event_type: str,
        source: str,
        details: dict[str, object] | None = None,
        task_id: str | None = None,
        severity: str = "info",
    ) -> None:
        self.calls.append(
            {
                "event_type": event_type,
                "source": source,
                "details": details,
                "task_id": task_id,
                "severity": severity,
            }
        )


async def _dispatch(*args: object, **kwargs: object) -> str:
    return "completion"


def _make_platform(audit_logger: object | None = None) -> KernelPlatform:
    return build_kernel_platform(
        site_config=_StubSiteConfig({"k": "v"}),
        pool=_StubPool(),
        dispatch=_dispatch,
        audit_logger=audit_logger or _RecordingAuditLogger(),
    )


# --- stub module --------------------------------------------------------------


class _StubModule:
    def __init__(
        self, name: str, capabilities: tuple[Capability, ...] = ()
    ) -> None:
        self._name = name
        self._capabilities = capabilities
        self.bound: Any = None

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self._name,
            version="1.0.0",
            visibility="public",
            capabilities=self._capabilities,
        )

    def bind_platform(self, platform: object) -> None:
        self.bound = platform


# --- build_kernel_platform ----------------------------------------------------


def test_build_kernel_platform_satisfies_protocol() -> None:
    from plugins.platform import Platform

    assert isinstance(_make_platform(), Platform)


async def test_build_kernel_platform_routes_audit_to_logger() -> None:
    audit = _RecordingAuditLogger()
    platform = _make_platform(audit_logger=audit)

    await platform.audit.write(
        "thing_happened",
        source="boot_test",
        details={"detail": 1},
        task_id="task-1",
        severity="warning",
    )

    assert audit.calls == [
        {
            "event_type": "thing_happened",
            "source": "boot_test",
            "details": {"detail": 1},
            "task_id": "task-1",
            "severity": "warning",
        }
    ]


def test_build_kernel_platform_config_reads_value() -> None:
    assert _make_platform().config.get("k") == "v"


# --- metric default (logger-backed sink, no Prometheus push seam yet) ---------


def test_kernel_metric_default_is_non_raising_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # No metric_emit supplied: the capability must be real (callable, logs)
    # rather than a silent no-op or a fail-loud stub.
    platform = _make_platform()
    with caplog.at_level(logging.DEBUG, logger="poindexter.platform"):
        platform.metric("posts_published", 1.0, niche="ai")
    assert any("posts_published" in rec.getMessage() for rec in caplog.records)


def test_kernel_metric_explicit_emitter_still_used() -> None:
    seen: list[tuple[str, float, dict[str, str]]] = []

    def _emit(name: str, value: float = 1.0, /, **labels: str) -> None:
        seen.append((name, value, labels))

    platform = KernelPlatform(
        site_config=_StubSiteConfig(),
        pool=_StubPool(),
        dispatch=_dispatch,
        audit_write=_RecordingAuditLogger().log,
        metric_emit=_emit,
    )
    platform.metric("count", 2.0, unit="n")
    assert seen == [("count", 2.0, {"unit": "n"})]


# --- bind_platform_to_modules -------------------------------------------------


def test_bind_returns_count_and_binds_each_module() -> None:
    from plugins.platform import bind_platform_to_modules

    modules = [_StubModule("a"), _StubModule("b")]
    platform = _make_platform()

    count = bind_platform_to_modules(modules, platform)

    assert count == 2
    assert all(m.bound is not None for m in modules)


def test_bind_grants_only_declared_capabilities() -> None:
    from plugins.platform import bind_platform_to_modules

    module = _StubModule("c", capabilities=(Capability.CONFIG,))
    platform = _make_platform()

    bind_platform_to_modules([module], platform)

    # Granted capability delegates to the backing.
    assert module.bound.config.get("k") == "v"
    # Undeclared capability fails loud.
    with pytest.raises(CapabilityError):
        _ = module.bound.audit


def test_bind_empty_module_gets_capability_free_handle() -> None:
    from plugins.platform import bind_platform_to_modules

    module = _StubModule("d")  # declares nothing
    platform = _make_platform()

    bind_platform_to_modules([module], platform)

    with pytest.raises(CapabilityError):
        _ = module.bound.config


def test_bind_fails_loud_when_backing_cannot_supply_capability() -> None:
    from plugins.platform import bind_platform_to_modules

    class _PartialBacking:
        # Supplies only ``config`` — no ``audit`` attribute at all.
        @property
        def config(self) -> object:
            return object()

    module = _StubModule("e", capabilities=(Capability.AUDIT,))

    with pytest.raises(CapabilityError, match="audit"):
        # _PartialBacking deliberately does not satisfy Platform — that is the
        # condition under test (the kernel can't supply ``audit``).
        bind_platform_to_modules([module], _PartialBacking())  # type: ignore[arg-type]


# --- content declares the audit capability (Wave 3c) --------------------------


def test_content_module_declares_audit_capability() -> None:
    # Content's first capability migration: its manifest must declare AUDIT so
    # the scoped handle exposes ``audit`` to the migrated stage/atom sites.
    from modules.content.content_module import ContentModule

    assert Capability.AUDIT in ContentModule().manifest().capabilities


# --- build_platform_for_subprocess (Prefect flow seam, Wave 3c) ---------------


def test_build_platform_for_subprocess_scopes_to_content(monkeypatch) -> None:
    # The Prefect subprocess never runs main.py's lifespan, so it builds + scopes
    # its own handle. The helper returns content's *scoped* handle (audit only),
    # mirroring how the subprocess rebuilds site_config.
    from plugins.module import ModuleManifest
    from services import di_wiring

    class _AuditLogger:
        async def log(self, *a: Any, **k: Any) -> None: ...

    class _ContentLike:
        def manifest(self) -> ModuleManifest:
            return ModuleManifest(
                name="content",
                version="1.0.0",
                visibility="public",
                capabilities=(Capability.AUDIT,),
            )

        def bind_platform(self, platform: object) -> None: ...

    monkeypatch.setattr(
        "services.audit_log.get_audit_logger", lambda: _AuditLogger()
    )
    monkeypatch.setattr(
        "services.llm_providers.dispatcher.dispatch_complete", _dispatch
    )
    monkeypatch.setattr(
        "plugins.registry.get_modules", lambda: [_ContentLike()]
    )

    scoped = di_wiring.build_platform_for_subprocess(
        pool=_StubPool(), site_config=_StubSiteConfig({"k": "v"})
    )

    assert scoped is not None
    # audit is granted — reachable without raising.
    assert scoped.audit is not None
    # an undeclared capability fails loud (scoping holds over the real handle).
    with pytest.raises(CapabilityError):
        _ = scoped.config


def test_build_platform_for_subprocess_returns_none_without_audit_logger(
    monkeypatch,
) -> None:
    # Best-effort: if the subprocess has no global AuditLogger, the helper
    # returns None (audit telemetry quietly drops) rather than raising — a
    # telemetry seam must never break content generation.
    from services import di_wiring

    monkeypatch.setattr("services.audit_log.get_audit_logger", lambda: None)

    assert (
        di_wiring.build_platform_for_subprocess(
            pool=_StubPool(), site_config=_StubSiteConfig()
        )
        is None
    )
