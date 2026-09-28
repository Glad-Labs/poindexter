"""Host services the firefighter may restart, through the host Recovery Agent.

``restart_container`` reaches docker. Ollama is not a container here: it runs as
host systemd units (``ollama-primary.service`` on :11434, ``ollama-vision`` on
:11435), and the brain, in its own container, can't restart a host unit. The
host Recovery Agent (``scripts/recovery-agent.py``, :9841) can, and the brain's
probe path already uses it (``health_probes.REMEDIATIONS`` -> ``recover_via_agent``).
The ``restart_host_service`` action (``registry.py``) posts to that same agent,
so the firefighter can hold a page like ``PoindexterOllamaDown`` for a
restart-then-verify instead of paging first and restarting 10-15 minutes later.

WHAT MAY BE RESTARTED
---------------------
Only the services in ``HOST_SERVICES``, an allowlist in code, not a setting.
Each entry carries its own check that the service is really down, written for
that service, so adding one means writing that check. Today there is one:

* ``ollama``: the agent restarts ``ollama-primary.service``, the :11434
  instance at ``ollama_base_url``, which is what ``poindexter_ollama_reachable``
  and ``PoindexterOllamaDown`` watch. The vision instance is a separate unit.

The agent's other two services stay off it. ``compose-reapply`` is not a
service restart: it reconciles every drifted container, and
``compose_drift_probe`` owns it with its own cap. ``mcp-http`` has its own
confirm-then-recover path (``mcp_http_probe``) and no alert a rule would match.

CONFIRM THE OUTAGE, DON'T GATE ON THE GPU LOCK
----------------------------------------------
Restarting ``ollama-primary`` kills whatever it is generating. The two brain
paths that touch a healthy Ollama guard against that: ``ollama_embedding``
defers while the GPU advisory lock is held, and ``ollama_runner_ram_watch``
recycles only when no lock covers the runner's card and its CPU is idle. Both
act on an Ollama that is up and may be busy.

This action acts on one that is down, and an Ollama that answers no one is
generating for no one. So the check that matters is whether it is really
down, asked from the brain's own side before anything is restarted
(``confirm_ollama_down``). The alert is one vantage point: the worker's
``/api/tags`` check, 3 s timeout, every scrape. Before the rule was raised to
critical, its gauge read 0 in 23 of 85,318 samples over 15 days, and for each
of the 8 zeros the host journal still covered, Ollama's own log had no request
from the worker at that moment while it answered the rest in under 1 ms
(docs/operations/self-healing.md, "Which brain notices page"). If the brain
gets any HTTP answer from the same
URL, Ollama is up, a restart would kill live work and fix nothing, and the
action refuses, so the alert pages with the reason. Only silence on every
attempt (refused, reset, timed out) lets it restart. The same check makes a
mis-wired rule harmless: pointed at an alert that is not an Ollama outage, it
finds Ollama answering and refuses.

The GPU lock is the wrong gate here. Measured on prod over the 324.5 h from
2026-09-15 to 09-28 (``gpu_task_sessions``, which records the sessions that
carry a task id):

* ``media_render`` held it for 32.6 h, 10% of the time, in 41 sessions of 48
  min on average, and image generation for 2.1 h more. A render holds GPU 0's
  device key, the same key every locked call to the primary takes
  (``gpu_lock_scopes``: ``render`` and ``llm_primary`` are both ``[0]``), so
  no locked LLM call runs on the primary under it, and the render itself
  doesn't use Ollama. Gating on the lock would refuse the self-heal through
  every render and protect nothing.
* LLM calls held it for 8.7 h: ``dispatch_complete`` holds
  ``gpu.lock("ollama")`` around each call. When Ollama is down, that holder is
  the pipeline's own call failing against it, the thing the restart is for.

What is left is an Ollama that accepts connections but answers nothing within
5 s, twice, while a runner still streams a reply. The restart loses that reply.
Nothing like it has been observed, and the probe path already restarts in that
state without asking.
"""
from __future__ import annotations

import asyncio
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from poindexter.brain import health_probes
from poindexter.brain.docker_utils import resolve_url

# The primary Ollama, resolved the way the brain's own ``ollama_models`` probe
# resolves it (``health_probes._sync_config_from_db``): the container's
# OLLAMA_URL wins, then ``app_settings.ollama_base_url``, then the default,
# localized to host.docker.internal inside docker. The worker's gauge reads the
# same row.
OLLAMA_URL_KEY = "ollama_base_url"
OLLAMA_URL_ENV = "OLLAMA_URL"
DEFAULT_OLLAMA_URL = "http://localhost:11434"

# Each attempt waits longer than the worker's 3 s check, and /api/tags answers
# in under 1 ms at p99 (never over 1 s in 9,773 logged requests), so a
# timeout here is not a slow answer. Two tries a second apart keep a blip on
# the brain's own side from reading as an outage. The worst case, a server
# that accepts and never answers, costs 11 s inside the dispatch cycle.
OLLAMA_CONFIRM_ATTEMPTS = 2
OLLAMA_CONFIRM_TIMEOUT_SECONDS = 5.0
OLLAMA_CONFIRM_GAP_SECONDS = 1.0

# The agent waits up to 30 s on ``systemctl restart`` (its TASK_TIMEOUT_SECONDS)
# and returns systemd's own error when that fails. Waiting longer than that
# records the agent's answer. The probe path's 15 s could give up while a
# restart that works is still running and report it as failed. The dispatcher
# already waits up to 40 s inline on ``docker_restart_container``.
AGENT_TIMEOUT_SECONDS = 45.0

_USER_AGENT = "brain-firefighter"


@dataclass(frozen=True)
class Confirmation:
    """Whether a host service is down, and the evidence either way."""

    down: bool
    detail: str


@dataclass(frozen=True)
class HostService:
    """A host service ``restart_host_service`` may restart.

    ``name`` is the Recovery Agent's service key (``_LINUX_SERVICES``);
    ``restarts`` says what that key restarts, for people (the catalog entry,
    the docs); ``confirm_down`` asks, from the brain, whether the service is
    really down, and runs before every restart.
    """

    name: str
    restarts: str
    confirm_down: Callable[[Any], Awaitable[Confirmation]]


def _http_status(url: str, timeout: float) -> int | None:
    """The HTTP status ``url`` answered with, or None when nothing answered.

    Any HTTP response is an answer, an error status included: the server is up
    and talking, so it is not the outage a restart fixes. A refused, reset or
    timed-out connection, or a name that doesn't resolve, is silence
    (``urllib.error.URLError`` and ``TimeoutError`` are both ``OSError``s).
    Anything else raises, and a check that raises refuses the restart.
    """
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(  # nosec B310 - confirm_ollama_down constrains the scheme to http(s)
            request, timeout=timeout,
        ) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except OSError:
        return None


async def confirm_ollama_down(pool: Any) -> Confirmation:
    """Ask the primary Ollama for ``/api/tags`` from the brain.

    Down only when no attempt gets any HTTP answer. One answer, from any
    attempt, means it is up. See the module docstring for why this, and not
    the GPU lock, decides whether the restart may run.
    """
    base = await resolve_url(
        pool, OLLAMA_URL_KEY, default=DEFAULT_OLLAMA_URL, env_var=OLLAMA_URL_ENV,
    )
    target = f"{base.rstrip('/')}/api/tags"
    if urllib.parse.urlparse(target).scheme.lower() not in ("http", "https"):
        return Confirmation(
            down=False,
            detail=f"can't check Ollama from the brain: {target!r} is not an http(s) URL",
        )
    for attempt in range(1, OLLAMA_CONFIRM_ATTEMPTS + 1):
        status = await asyncio.to_thread(_http_status, target, OLLAMA_CONFIRM_TIMEOUT_SECONDS)
        if status is not None:
            return Confirmation(
                down=False,
                detail=(
                    f"Ollama answers {target} from the brain (HTTP {status}), "
                    "so it is not down; a live server is not restarted"
                ),
            )
        if attempt < OLLAMA_CONFIRM_ATTEMPTS:
            await asyncio.sleep(OLLAMA_CONFIRM_GAP_SECONDS)
    return Confirmation(
        down=True,
        detail=(
            f"no answer from {target} on {OLLAMA_CONFIRM_ATTEMPTS} tries "
            f"({OLLAMA_CONFIRM_TIMEOUT_SECONDS:g} s each)"
        ),
    )


HOST_SERVICES: dict[str, HostService] = {
    "ollama": HostService(
        name="ollama",
        restarts="ollama-primary.service, the :11434 instance at ollama_base_url",
        confirm_down=confirm_ollama_down,
    ),
}


async def restart_via_agent(pool: Any, service: str) -> tuple[bool, str]:
    """POST ``{"service": service}`` to the host Recovery Agent.

    The same call the probe path makes (``health_probes._call_agent_recovery``):
    the agent URL and bearer token come from ``mcp_http_probe_recovery_url``
    and ``mcp_http_probe_recovery_token`` through the brain's secret reader,
    and it never raises. Only the timeout differs (``AGENT_TIMEOUT_SECONDS``).
    Looked up on the module at call time, so both paths share one
    implementation.
    """
    return await health_probes._call_agent_recovery(
        pool, service, timeout=AGENT_TIMEOUT_SECONDS,
    )
