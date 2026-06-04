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
    """Append a scoped ``audit_log`` row (async; DB write)."""

    async def write(self, event_type: str, /, **details: Any) -> None: ...


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


__all__ = [
    "Capability",
    "CapabilityError",
    "Platform",
    "ScopedPlatform",
    "ConfigCapability",
    "SecretCapability",
    "DispatchCapability",
    "DbCapability",
    "LogCapability",
    "MetricCapability",
    "AuditCapability",
]
