"""``Platform`` — the module→kernel interface (Seam 1 of the kernel-platform
architecture).

A module reaches the kernel **only** through one injected ``Platform`` object:
an interface, never the kernel's internals. This delivers the three hard
requirements of the module system at once — *isolation* (a module touches the
oven only through the handle), *testability* (hand it a fake ``Platform``), and
*painless add/remove* (a module's sole dependency is one stable interface).

The handle is also the **trust boundary**. It is *capability-scoped*: a module
declares the capabilities it needs and the kernel injects a handle that exposes
**only** those. A module that never declared ``secret`` has no ``.secret`` to
reach. See ``docs/architecture/2026-06-04-kernel-platform-architecture.md``
(§Seam 1, §Trust & isolation) and Glad-Labs/poindexter#667.

Scope — this file is **Wave 0**: the contract (the ``Platform`` + per-capability
Protocols) plus the capability-scoping enforcer (``ScopedPlatform``). It is
import-cheap on purpose — stdlib only, no asyncpg/kernel imports — so the
Protocol costs nothing to import (mirrors ``plugins/module.py``).

Deferred:

- The concrete ``KernelPlatform`` (wrapping live kernel services: ``SiteConfig``,
  ``dispatch_complete``, the pool, the ``audit_log`` writer) and the shared
  ``FakePlatform`` land in **Wave 1**, with a conformance test proving both
  satisfy this Protocol identically (the fake-kernel-drift guard).
- Lifecycle wiring (a ``bind_platform`` hook + sourcing each module's granted
  capability set) lands in **Wave 2**. Note: ``ModuleManifest.requires`` is for
  *dependency specifiers*, not capabilities — Wave 2 adds a distinct
  ``capabilities`` field rather than overloading ``requires``.

``events`` is intentionally absent from the v1 surface — there is no event bus
(the "expo line") yet; it joins the handle when that seam is built.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable, Protocol, runtime_checkable


class Capability(str, Enum):
    """The capabilities a module may request on its ``Platform`` handle.

    Each value is *exactly* the attribute name it gates on the handle —
    ``Capability.SECRET.value == "secret"``, so accessing ``platform.secret``
    is governed by ``Capability.SECRET``. A module is granted a subset of
    these; reaching for an ungranted one fails loud (see ``ScopedPlatform``).
    """

    CONFIG = "config"
    SECRET = "secret"
    DISPATCH = "dispatch"
    DB = "db"
    LOG = "log"
    METRIC = "metric"
    AUDIT = "audit"


class CapabilityError(RuntimeError):
    """Raised when a module reaches for a capability it did not declare.

    Fail-loud per the project's no-silent-defaults rule: the kernel never hands
    a module a capability it didn't ask for, and never silently no-ops when a
    module reaches for one it lacks.
    """


# --- per-capability interfaces ------------------------------------------------
# Each capability on the handle is its own small Protocol. Return types that
# would require a heavy import (asyncpg, etc.) are typed ``Any`` to keep this
# module import-cheap, mirroring ``plugins/module.py``.


@runtime_checkable
class ConfigCapability(Protocol):
    """Read a DB-backed ``app_settings`` value (sync, cache-backed)."""

    def get(self, key: str, default: Any = None) -> Any: ...


@runtime_checkable
class SecretCapability(Protocol):
    """Read an encrypted secret (async; hits the DB each call)."""

    async def get(self, key: str, default: str | None = None) -> str | None: ...


@runtime_checkable
class DispatchCapability(Protocol):
    """Run an LLM completion through the cost-guarded, tier-aware router."""

    async def complete(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class DbCapability(Protocol):
    """Acquire an ``OperatorScope``-scoped DB connection.

    Wave 0 keeps the surface minimal: ``acquire()`` returns an async context
    manager yielding a connection. (Tenancy scoping via ``OperatorScope`` is
    Glad-Labs/poindexter#60; Wave 1 wires the real pool, scoped where that
    lands.)
    """

    def acquire(self) -> Any: ...


@runtime_checkable
class LogCapability(Protocol):
    """Emit a structured log line: ``platform.log("message", key=value)``."""

    def __call__(self, message: str, /, **fields: Any) -> None: ...


@runtime_checkable
class MetricCapability(Protocol):
    """Emit a metric sample: ``platform.metric("name", 1.0, label="x")``."""

    def __call__(self, name: str, value: float = 1.0, /, **labels: str) -> None: ...


@runtime_checkable
class AuditCapability(Protocol):
    """Append a scoped ``audit_log`` row (async; DB write).

    Mirrors the columns the ``audit_log`` table + the downstream
    ``findings_alert_router`` actually read: ``event_type`` + ``source`` (who
    emitted it) + structured ``details`` + optional ``task_id`` + ``severity``
    (``info`` / ``warning`` / ``critical``). ``source`` and ``severity`` are
    load-bearing — alert routing keys on them — so they are first-class
    parameters, not buried in ``details``.
    """

    async def write(
        self,
        event_type: str,
        *,
        source: str,
        details: dict[str, Any] | None = None,
        task_id: str | None = None,
        severity: str = "info",
    ) -> None: ...

    def write_bg(
        self,
        event_type: str,
        *,
        source: str,
        details: dict[str, Any] | None = None,
        task_id: str | None = None,
        severity: str = "info",
    ) -> None:
        """Fire-and-forget audit write — schedules the row, never blocks or raises.

        The non-blocking sibling of ``write``. Use it at *best-effort telemetry*
        sites — the pipeline's "a telemetry write must never slow or break the
        chain" rule — where blocking a stage on a DB round-trip (or letting an
        audit failure surface) is worse than dropping the occasional row. Use
        ``write`` when the caller must await durability. Same field shape as
        ``write``; returns ``None`` synchronously (it is not a coroutine).
        """
        ...


# --- the handle ---------------------------------------------------------------


@runtime_checkable
class Platform(Protocol):
    """The single interface a module uses to reach the kernel.

    Modules import this *type* (never kernel modules) and receive a concrete
    instance (``KernelPlatform`` in prod, ``FakePlatform`` in tests — both
    Wave 1). Each capability is exposed as an attribute; a capability-scoped
    handle (``ScopedPlatform``) exposes only the subset a module declared.
    """

    @property
    def config(self) -> ConfigCapability: ...

    @property
    def secret(self) -> SecretCapability: ...

    @property
    def dispatch(self) -> DispatchCapability: ...

    @property
    def db(self) -> DbCapability: ...

    @property
    def log(self) -> LogCapability: ...

    @property
    def metric(self) -> MetricCapability: ...

    @property
    def audit(self) -> AuditCapability: ...


# --- capability-scoping enforcer ----------------------------------------------


class ScopedPlatform:
    """Wraps a backing ``Platform`` and exposes only *granted* capabilities.

    This is the trust boundary made concrete. The kernel constructs one of
    these per module from the module's declared capability set, so a module's
    handle is least-privilege *by construction*. Accessing a granted capability
    delegates to the backing platform; reaching for an ungranted one fails loud.

    The class deliberately does **not** define the capability attributes, so
    every capability access falls through to ``__getattr__`` — a single
    auditable chokepoint where the grant is enforced. It forwards every
    capability attribute, so it stands in wherever a ``Platform`` is expected.
    """

    # The gate-able attribute names == the Capability values.
    _CAPABILITY_ATTRS: frozenset[str] = frozenset(c.value for c in Capability)

    def __init__(self, backing: Platform, granted: Iterable[Capability]) -> None:
        self._backing = backing
        self._granted = frozenset(granted)

    @property
    def granted(self) -> frozenset[Capability]:
        """The capabilities this handle was granted (read-only)."""
        return self._granted

    def __getattr__(self, name: str) -> Any:
        # ``__getattr__`` fires only for attributes not found by normal lookup
        # — i.e. the capability attributes, which this class deliberately does
        # not define. Anything else (a typo, a probed dunder) is a genuine
        # ``AttributeError``.
        if name not in self._CAPABILITY_ATTRS:
            raise AttributeError(name)
        self._require(Capability(name))
        return getattr(self._backing, name)

    def _require(self, capability: Capability) -> None:
        """Fail loud if this handle was not granted ``capability``; else return.

        THE enforcement point of the capability-scoped trust boundary. When the
        capability is granted, return ``None`` and access proceeds. When it is
        not, raise ``CapabilityError`` naming both the missing capability and
        what *was* granted — so the operator (or a failing test) sees exactly
        which entry to add to the module's manifest.

        Per the no-silent-defaults rule: never warn-and-continue, never no-op.
        """
        if capability in self._granted:
            return
        granted = ", ".join(sorted(c.value for c in self._granted)) or "(none)"
        raise CapabilityError(
            f"module reached for the '{capability.value}' capability but did "
            f"not declare it. Granted: {granted}. Add '{capability.value}' to "
            f"the module's declared capabilities to grant access."
        )


def scope_for_module(module: Any, platform: Platform) -> ScopedPlatform:
    """Build a capability-scoped handle for ``module`` from the full ``platform``.

    Reads the module's declared capabilities from its manifest
    (``module.manifest().capabilities``) and wraps the backing platform so the
    module can reach only those — the single place the kernel turns "what a
    module declared" into "what its handle exposes." Duck-typed on ``module``
    so this stays free of a ``plugins.module`` import (one-directional
    dependency: ``module`` names ``platform``, never the reverse).
    """
    return ScopedPlatform(platform, module.manifest().capabilities)


def bind_platform_to_modules(modules: Iterable[Any], platform: Platform) -> int:
    """Bind a capability-scoped handle to each module; return how many were bound.

    The boot-time loop (called from ``main.py``'s lifespan, Wave 3b of Seam 1)
    that turns "the kernel has a ``Platform``" into "every module holds its own
    least-privilege view of it." For each module it builds a ``ScopedPlatform``
    exposing only that module's declared capabilities (via ``scope_for_module``)
    and hands it to ``module.bind_platform(...)``.

    Fails loud (``CapabilityError``) if a module declares a capability the
    backing ``platform`` does not supply — a real mis-wiring (a typo'd or
    not-yet-built capability) that should block boot, not silently degrade
    (no-silent-defaults). Today every declared capability resolves on the live
    ``KernelPlatform``, so this guard never trips in prod; it exists so the day
    it would, boot fails with a precise message instead of an ``AttributeError``
    deep inside a stage.

    Duck-typed on ``module`` (``manifest()`` + ``bind_platform()``) so this
    stays free of a ``plugins.module`` import — the one-directional dependency
    holds: ``module`` names ``platform``, never the reverse.
    """
    bound = 0
    for module in modules:
        manifest = module.manifest()
        for capability in manifest.capabilities:
            if not hasattr(platform, capability.value):
                raise CapabilityError(
                    f"module '{manifest.name}' declared the "
                    f"'{capability.value}' capability but the kernel platform "
                    f"does not supply it — the kernel cannot grant a capability "
                    f"it has no backing for."
                )
        module.bind_platform(scope_for_module(module, platform))
        bound += 1
    return bound


__all__ = [
    "Capability",
    "CapabilityError",
    "Platform",
    "ScopedPlatform",
    "scope_for_module",
    "bind_platform_to_modules",
    "ConfigCapability",
    "SecretCapability",
    "DispatchCapability",
    "DbCapability",
    "LogCapability",
    "MetricCapability",
    "AuditCapability",
]
