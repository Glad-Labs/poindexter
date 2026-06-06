"""``BrainProbeRegistry`` — Module v1 Phase 4 brain-probe wire-up.

Modules contribute brain probes via ``Module.register_probes(registry)``.
The host (FastAPI lifespan) constructs one shared
:class:`BrainProbeRegistry`, hands it to every module's
``register_probes`` hook, and stashes it on ``app.state`` so the
``GET /api/modules/probes`` route can surface what's registered.

Why a worker-side registry when probes run in the brain daemon
-----------------------------------------------------------------
The brain daemon is a separate process. It cannot import worker-side
modules directly (per ``brain/brain_daemon.py``'s "Standalone — no
imports from the FastAPI codebase" contract).

The cross-process bridge is HTTP: the worker exposes the registered
probe specs at ``/api/modules/probes``. The brain daemon polls that
endpoint on its cycle, runs the probes via a worker callback URL, or
surfaces the spec list in the audit_log for operator visibility. The
brain-side polling is a follow-up (no concrete probes registered
today); this file delivers the worker-side contract that future
probes will register against.

What this is NOT
----------------
- **Not the probe execution loop.** The brain daemon runs probes on
  its own cadence. This registry only records what's available.
- **Not a replacement for ``brain/probe_interface.py``.** That file
  hosts the brain-internal ``Probe`` protocol and the brain-internal
  ``register_probe`` registry, which infrastructure probes (built into
  the brain) use directly. The Module v1 ``BrainProbeRegistry`` is
  the *worker → brain* bridge for module-contributed probes; the two
  registries deliberately don't share state.
- **Not async.** Registration happens at lifespan startup (sync). The
  probe callable itself can be async — the brain daemon awaits it
  when it eventually executes the probe.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

ProbeCallable = Callable[..., Awaitable[Any]]
"""A registered probe's callable. Awaits to a dict-shaped result the
brain daemon can record. Kept loose (``Callable[..., Awaitable[Any]]``)
because each probe defines its own signature — the registry's job is
identification, not invocation orchestration."""


@dataclass(frozen=True)
class RegisteredProbe:
    """A single module-contributed probe.

    Frozen so a registration is hashable + safe to log without
    accidental mutation. Identity is ``(module, name)`` — the
    registry rejects duplicate keys per
    ``feedback_no_silent_defaults`` so a typo'd second registration
    fails loud at boot rather than silently shadowing the first.
    """

    module: str
    """Owning module's manifest name (e.g. ``"content"``,
    ``"finance"``). Forms the first segment of the probe's fully
    qualified identifier."""

    name: str
    """Probe-local slug. Combined with ``module`` to form the FQID:
    ``f"{module}.{name}"``. Must be unique within a module's
    registrations."""

    callable: ProbeCallable = field(compare=False)
    """The async callable the brain daemon will eventually invoke.
    Excluded from equality so two registrations with the same FQID
    collide regardless of which callable is passed (the collision is
    the bug — callable identity wouldn't fix it)."""

    description: str = ""
    """One-line description for ``/api/modules/probes`` listings and
    the eventual operator UI. Optional but encouraged."""

    interval_seconds: int = 300
    """How often the brain daemon should run this probe. Default
    matches the brain's existing 5-minute monitor cycle so a probe
    that doesn't specify an interval rides the standard cadence."""

    @property
    def fqid(self) -> str:
        """Fully qualified identifier: ``"<module>.<name>"``."""
        return f"{self.module}.{self.name}"


class BrainProbeRegistry:
    """Collects module-contributed brain probes during lifespan startup.

    Single instance per worker process, owned by ``app.state``. Each
    module's ``register_probes(registry)`` adds zero or more probes
    via :meth:`register`; the registry rejects duplicates per
    ``feedback_no_silent_defaults``.

    Thread / async safety: registration runs single-threaded during
    FastAPI lifespan startup. Reads (``probes()``, ``__len__``,
    iteration) happen from request handlers and are read-only — the
    registry is effectively immutable after lifespan completes.
    """

    def __init__(self) -> None:
        self._probes: dict[str, RegisteredProbe] = {}

    def register(
        self,
        *,
        module: str,
        name: str,
        callable: ProbeCallable,
        description: str = "",
        interval_seconds: int = 300,
    ) -> RegisteredProbe:
        """Add a probe. Returns the stored :class:`RegisteredProbe`.

        Raises ``ValueError`` if a probe with the same ``(module,
        name)`` FQID is already registered — per
        ``feedback_no_silent_defaults`` a duplicate registration is a
        bug, not a tolerated overwrite.
        """
        if not module or not name:
            raise ValueError(
                "BrainProbeRegistry.register: both module and name "
                "are required (got module=%r, name=%r)" % (module, name)
            )
        probe = RegisteredProbe(
            module=module,
            name=name,
            callable=callable,
            description=description,
            interval_seconds=interval_seconds,
        )
        if probe.fqid in self._probes:
            existing = self._probes[probe.fqid]
            raise ValueError(
                f"BrainProbeRegistry: duplicate probe registration "
                f"{probe.fqid!r} (existing description: "
                f"{existing.description!r}, new description: "
                f"{description!r}). Pick a unique name."
            )
        self._probes[probe.fqid] = probe
        return probe

    def probes(self) -> list[RegisteredProbe]:
        """Snapshot of registered probes. Deterministic order
        (insertion order — Python 3.7+ dicts are ordered)."""
        return list(self._probes.values())

    def by_module(self, module: str) -> list[RegisteredProbe]:
        """Probes belonging to one module. Useful for per-module
        healthcheck aggregation."""
        return [p for p in self._probes.values() if p.module == module]

    def __len__(self) -> int:
        return len(self._probes)

    def __contains__(self, fqid: object) -> bool:
        return isinstance(fqid, str) and fqid in self._probes

    def __iter__(self):
        return iter(self._probes.values())


__all__ = ["BrainProbeRegistry", "RegisteredProbe", "ProbeCallable"]
