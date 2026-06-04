"""Unit tests for the module↔platform binding contract (Wave 2, Glad-Labs/poindexter#667).

Pins the additions that let the kernel hand each module a capability-scoped
handle: the ``ModuleManifest.capabilities`` field, ``bind_platform`` as part of
the (runtime-checkable) ``Module`` Protocol, and ``scope_for_module`` — the one
place "what a module declared" becomes "what its handle exposes."

Self-contained: a stub module implements the Protocol (the real ContentModule /
FinanceModule need the worker's heavy deps; their conformance is covered in CI +
``test_module_registry.py``).
"""

from __future__ import annotations

from plugins.fake_platform import FakePlatform
from plugins.module import Module, ModuleManifest
from plugins.platform import Capability, ScopedPlatform, scope_for_module


def _stub_module(capabilities: tuple[Capability, ...] = ()) -> object:
    class _StubModule:
        def manifest(self) -> ModuleManifest:
            return ModuleManifest(
                name="stub",
                version="1.0.0",
                visibility="public",
                capabilities=capabilities,
            )

        async def migrate(self, pool: object) -> None: ...
        def register_routes(self, app: object) -> None: ...
        def register_cli(self, parser: object) -> None: ...
        def register_dashboards(self, grafana: object) -> None: ...
        def register_probes(self, brain: object) -> None: ...

        def bind_platform(self, platform: object) -> None:
            self.platform = platform

        async def healthcheck(self, pool: object) -> object:
            return None

    return _StubModule()


def test_manifest_capabilities_defaults_empty() -> None:
    manifest = ModuleManifest(name="m", version="1.0.0", visibility="public")
    assert manifest.capabilities == ()


def test_manifest_accepts_declared_capabilities() -> None:
    manifest = ModuleManifest(
        name="m",
        version="1.0.0",
        visibility="public",
        capabilities=(Capability.CONFIG, Capability.DISPATCH),
    )
    assert manifest.capabilities == (Capability.CONFIG, Capability.DISPATCH)


def test_stub_with_bind_platform_satisfies_module_protocol() -> None:
    # bind_platform is now part of the runtime-checkable Module Protocol.
    assert isinstance(_stub_module(), Module)


def test_module_without_bind_platform_is_not_a_module() -> None:
    # Documents the breaking-change contract: a module lacking bind_platform
    # no longer conforms (this is why ContentModule/FinanceModule/_StubModule
    # all gained it in the same change).
    class _NoBind:
        def manifest(self) -> ModuleManifest:
            return ModuleManifest(name="nb", version="1.0.0", visibility="public")

        async def migrate(self, pool: object) -> None: ...
        def register_routes(self, app: object) -> None: ...
        def register_cli(self, parser: object) -> None: ...
        def register_dashboards(self, grafana: object) -> None: ...
        def register_probes(self, brain: object) -> None: ...

        async def healthcheck(self, pool: object) -> object:
            return None

    assert not isinstance(_NoBind(), Module)


def test_scope_for_module_grants_only_declared_capabilities() -> None:
    module = _stub_module(capabilities=(Capability.CONFIG, Capability.DB))
    backing = FakePlatform(config={"k": "v"})

    scoped = scope_for_module(module, backing)

    assert isinstance(scoped, ScopedPlatform)
    assert scoped.granted == frozenset({Capability.CONFIG, Capability.DB})
    assert scoped.config.get("k") == "v"  # declared → works


def test_scope_for_module_blocks_undeclared_capability() -> None:
    from plugins.platform import CapabilityError

    module = _stub_module(capabilities=(Capability.CONFIG,))
    scoped = scope_for_module(module, FakePlatform(config={"k": "v"}))

    try:
        _ = scoped.audit  # not declared
    except CapabilityError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected CapabilityError for undeclared capability")


def test_bind_platform_stores_the_scoped_handle() -> None:
    module = _stub_module(capabilities=(Capability.CONFIG,))
    scoped = scope_for_module(module, FakePlatform(config={"k": "v"}))

    module.bind_platform(scoped)

    # The module now reaches the kernel only through the handle it was given.
    assert module.platform.config.get("k") == "v"  # type: ignore[attr-defined]
