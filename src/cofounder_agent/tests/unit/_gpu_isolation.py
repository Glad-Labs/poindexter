"""Keep a unit test's GPU reclaim rungs off the real sidecars.

The render path clears the card through ``GPUScheduler._unload_*``: image-gen's
hard unload before a hero clip, wan and ComfyUI before the stills, the full
ladder before a presenter shot, the soft levers before an escalation still.
Each rung is a real ``POST /unload`` or ``/free`` to a sidecar addressed by
its compose service name, and on the self-hosted CI runner that name is the
PRODUCTION container. Until 2026-09-25 ``test_shot_list_renderer.py`` sent
about 11 hard unloads to the live image-gen server per CI job, and
``test_shot_list_progress.py`` hard-unloaded the live wan server and freed
ComfyUI's models.

The rung list is DERIVED from the scheduler, not typed out. A hand-kept list
is how ``test_shot_list_renderer.py`` came to stub the wan and ComfyUI rungs
but not image-gen's, the one that did the damage. A rung added to the ladder
is inert here the day it lands; ``test_network_egress_guard.py`` pins that the
derivation still finds the rungs it knows about.

The egress guard is the backstop behind this helper: a rung this missed would
still fail the test on its production hostname rather than reach the sidecar.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock

RUNG_PREFIX = "_unload_"


def reclaim_rung_names() -> tuple[str, ...]:
    """Every ``GPUScheduler._unload_*`` coroutine method, sorted."""
    from poindexter.services.gpu_scheduler import GPUScheduler

    return tuple(sorted(
        name for name, member in vars(GPUScheduler).items()
        if name.startswith(RUNG_PREFIX) and inspect.iscoroutinefunction(member)
    ))


def make_reclaim_rungs_inert(monkeypatch: Any, scheduler: Any = None) -> dict[str, AsyncMock]:
    """Replace every reclaim rung on ``scheduler`` with an ``AsyncMock``.

    ``scheduler`` defaults to the process-wide ``gpu`` instance the render code
    imports. Returns ``{rung name: mock}``. Call it from an autouse fixture; a
    test asserting on one rung re-patches it, and the innermost patch wins.

    ``monkeypatch``, not ``patch()``, for the reason
    ``test_shot_list_renderer._neutralize_card_clear`` records: when a test
    re-patches the same name with ``monkeypatch``, a ``patch()`` taken in a
    fixture exits first and the shared monkeypatch then restores the fixture's
    mock over the real method for every later test in the process. One
    monkeypatch undoes in order.

    ``setitem`` on the instance ``__dict__``, not ``setattr``: the rungs are
    class methods, so ``setattr``'s undo would write the bound method back as
    an instance attribute. That shadows the class for the rest of the process,
    and a later ``patch.object(GPUScheduler, ...)`` would never reach the
    singleton. ``setitem`` undoes by deleting the key.
    """
    if scheduler is None:
        from poindexter.services.gpu_scheduler import gpu as scheduler
    mocks: dict[str, AsyncMock] = {}
    for name in reclaim_rung_names():
        mocks[name] = AsyncMock(name=name)
        monkeypatch.setitem(vars(scheduler), name, mocks[name])
    return mocks
