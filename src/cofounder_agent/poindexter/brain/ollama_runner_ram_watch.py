"""Recycle an ollama llama-server runner whose host memory has grown past a watermark.

WHY THIS IS NOT PART OF ``sidecar_ram_watch``
---------------------------------------------
That probe recycles **docker containers** — it measures with ``docker exec`` and
acts with ``docker restart``. Ollama runs as a **host systemd unit** here
(``ollama-vision.service`` / ``ollama-primary.service``), so neither half
reaches it: cadvisor's ``container_memory_*`` never covers a host unit, and this
daemon runs in its own cgroup and pid namespaces, so it cannot read the unit's
cgroup files or ``/proc/<pid>/status`` either.

So the two halves come from elsewhere:

* **Measure** — ``scripts/nvidia-smi-exporter.py`` runs with ``pid: host`` and
  publishes ``ollama_runner_anon_bytes{unit=...}``. The brain already scrapes
  that exporter for wall power, so this adds no new dependency.
* **Act** — ollama's own HTTP API. ``keep_alive: 0`` terminates the runner
  process, which is what actually returns the memory; a follow-up load with
  ``keep_alive: -1`` restores the pin. **Restarting the systemd unit is neither
  possible from here nor necessary.**

WHAT GROWS, AND WHY IT STOPS (re-measured 2026-09-25)
-----------------------------------------------------
It is not a leak. It is llama-server's host-RAM PROMPT CACHE (llama.cpp
#16391): when a new task arrives, the idle slot's KV state is copied into host
memory so a later prompt sharing its prefix can restore it instead of
re-prefilling. Ollama 0.32 starts llama-server without ``--cache-ram``, so the
upstream cap applies: 8192 MiB. The runner logs every entry ("saving prompt
with length L, total state size = S MiB") and the total ("cache state: N
prompts, M MiB (limits: 8192.000 MiB ...)") into the unit's journal.

An entry costs its token count times the model's KV bytes per token: 96 KiB
for qwen3-vl:30b-a3b at f16 (2 x 48 layers x 4 KV heads x 128 dims x 2 bytes).
Measured per request by scripts/diagnostics/ollama-runner-prompt-cache-bench.sh:

    short text, ~50 tokens          4.7 MiB  (2026-08-28's "6.9 MiB/request leak")
    long text, ~3,800 tokens        356 MiB
    one 1280x720 frame, ~1,120      151 MiB  = 105 MiB of KV + ~46 MiB image data
    the same prompt again            ~0      (the entry is replaced, not added)

The image data is held by the entry but NOT counted against the cap, so a
vision-heavy cache settles ABOVE 8 GiB: 10.5-10.7 GiB on 2026-09-25, after one
render's qa_shot_vision burst filled it from 2.9 to 8.1 GiB in 80 seconds. The
2026-08-28 bench sent 30 short requests (~0.2 GiB) and so never met the cap;
"dead linear, no plateau" was true of the window, not of the process.

Most of it is never read back: 213 of 1,347 lookups (16%) found a reusable
entry over 2026-09-24/25, so the kernel swaps the rest out and it sits in the
zram fast tier. The structural lever is the cap itself: llama-server reads
``LLAMA_ARG_CACHE_RAM`` and inherits ollama's environment. Until the unit sets
it, this probe gives the memory back by recycling the runner.

WHY A MEMORY WATERMARK, NOT A REQUEST COUNT
-------------------------------------------
Per-request cost spans 75x (4.7 to 356 MiB) and a repeated prompt costs
nothing, so no request count maps onto memory held: sized for text it recycles
on a few hundred MiB, sized for vision it lets a render add 6 GiB first. The
watermark reads the quantity itself. 4 GB sits above a fresh runner (0.2-0.4
GiB) and below the cache's own 8 GiB cap, so it trips on any workload that can
fill the cache, text or vision. The observed plateaus sit higher still: 9.35
GiB (2026-08-28) and 10.6 GiB (2026-09-25).

THE COST OF A RECYCLE IS REAL
-----------------------------
The re-pin took 39-66 s for each of the six recycles on 2026-09-25 (~85 s on
2026-08-28). A request that reaches the endpoint meanwhile waits on the load,
and one whose own timeout is shorter fails. That is why this is
watermark-gated and idle-gated rather than a periodic timer, and why the reload
is issued eagerly instead of waiting for the next caller to pay the latency.

THE RE-PIN SENDS THE PINNED CONTEXT (2026-09-25)
------------------------------------------------
Ollama reloads a resident model whenever a request asks for a different
``num_ctx``. The re-pin used to send none, so it loaded at the instance default
(32768 on the 24 GB card) while every Poindexter call routed to the endpoint
runs at ``pinned_llm_endpoint_num_ctx`` (16384). Every recycle therefore cost
TWO reloads: its own, then the first rail call's. That happened four times on
2026-09-25 (01:53, 04:21, 06:53, 13:17). The re-pin now sends that setting, so
the eager reload is the only one.

THE LOCK GATE READS THE TARGET'S OWN CARD (2026-09-25)
------------------------------------------------------
Since device scoping went live (stack#3457, 2026-08-31) a caller pinned to a
card takes the base advisory key SHARED plus that card's device key, and only
an unscoped caller takes the base key exclusively. This gate kept reading "any
row on the base key", which since then means "any GPU work anywhere", so a
Stage-2 render on GPU 0 deferred the recycle of the GPU-1 judge for its whole
run: 13:31-16:04 EDT on 2026-09-25, while the runner went from 1.5 to 10.6 GiB.

It now reads the device keys covering the target endpoint's cards, derived the
way the worker derives them (see ``resolve_lock_scope``), plus any EXCLUSIVE
base-key row, because an unscoped session claims every card. Whatever it
cannot resolve (scoping off, no node id, an endpoint that is neither ollama
instance, an unreadable scope map) falls back to the whole-box read it used to
do. A wrong narrow read would recycle a model mid-call; a wrong wide read only
defers.

The lock was never what saw most judge traffic, whatever this docstring used
to say. Since #2646 (2026-07-17) a dispatch to a model pinned to its own
endpoint takes no GPU lock, and that covers every QA rail and qa_shot_vision.
Only callers that lock the judge explicitly (image captioning, media QA) and
whole-box sessions show up here. The runner-CPU gate is what sees an in-flight
request, and it stays.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

from poindexter.brain.ram_recycle_common import (
    coerce_bool,
    coerce_float,
    coerce_int,
    emit_finding,
    read_setting,
)

ENABLED_KEY = "ollama_runner_ram_recycle_enabled"
TARGETS_KEY = "ollama_runner_ram_recycle_targets"
COOLDOWN_MINUTES_KEY = "ollama_runner_ram_recycle_cooldown_minutes"
CPU_IDLE_PERCENT_KEY = "ollama_runner_ram_recycle_cpu_idle_percent"
REQUIRE_GPU_LOCK_FREE_KEY = "ollama_runner_ram_recycle_require_gpu_lock_free"
EXPORTER_URL_KEY = "ollama_runner_ram_recycle_exporter_url"

# OFF by default. Every other install's ollama layout differs (one instance, a
# container, a remote host), and a probe that restarts someone's LLM endpoint
# uninvited is a bad default. Matt's operator stack opts in.
DEFAULT_ENABLED = False

# `unit|watermark_gb|endpoint|model`, entries comma-separated. Fields are
# PIPE-delimited, not colon-delimited like sidecar_ram_recycle_targets, because
# two of the four fields carry their own colons: the endpoint is a URL
# (`http://host:11435`) and the model carries a tag (`qwen3-vl:30b`). Colons
# make the split genuinely ambiguous — a right-split reads the model as "30b"
# — and no amount of clever splitting fixes it, so the delimiter changes.
# 4 GB sits above a fresh runner (0.2-0.4 GiB) and below the prompt cache's
# own 8 GiB cap, let alone the 9.35 GiB (2026-08-28) and 10.6 GiB (2026-09-25)
# plateaus, so it returns the memory long before the fast swap tier notices.
DEFAULT_TARGETS = (
    "ollama-vision.service|4|http://host.docker.internal:11435|qwen3-vl:30b-a3b-instruct"
)

DEFAULT_COOLDOWN_MINUTES = 120
# A loaded-but-idle runner sits at 0.0%; one mid-generation pegs a core.
DEFAULT_CPU_IDLE_PERCENT = 5.0
DEFAULT_REQUIRE_GPU_LOCK_FREE = True
DEFAULT_EXPORTER_URL = ""  # empty = derive from the runtime (docker vs host)

# The context every dispatch routed to a pinned endpoint runs at — the worker's
# ``dispatcher.PINNED_NUM_CTX_KEY``, whose default lives in settings_defaults.
# The brain runs stdlib + asyncpg and does not import the worker, so the key and
# its default are copied here and pinned to the originals by
# tests/unit/brain/test_ollama_runner_ram_watch.py. The re-pin loads at this
# size so the next rail call does not reload the model. A value of 0 or less
# sends no num_ctx, leaving the size to the instance default.
PINNED_NUM_CTX_KEY = "pinned_llm_endpoint_num_ctx"
DEFAULT_PINNED_NUM_CTX = 16384

GPU_ADVISORY_LOCK_KEY = 7_777_777_777

# --- lock scope, mirrored from services/gpu_scheduler.py ---------------------
#
# The brain runs stdlib + asyncpg and cannot import the worker package, so the
# setting keys, defaults and the key derivation below are copies, each pinned
# to its original by tests/unit/brain/test_ollama_runner_ram_watch.py. A copy
# that drifts raises nothing: the gate reads keys no caller takes and calls a
# busy judge idle.
GPU_LOCK_PER_DEVICE_KEY = "gpu_lock_per_device_enabled"
GPU_LOCK_SCOPES_KEY = "gpu_lock_scopes"
GPU_LOCK_NODE_ID_KEY = "gpu_lock_node_id"
OLLAMA_BASE_URL_KEY = "ollama_base_url"
OLLAMA_VISION_BASE_URL_KEY = "ollama_vision_base_url"
# services.bootstrap_defaults.DEFAULT_OLLAMA_URL — the primary when the row is empty.
DEFAULT_OLLAMA_URL = "http://localhost:11434"
# gpu_scheduler.DEFAULT_GPU_LOCK_SCOPES — what an empty gpu_lock_scopes row means.
DEFAULT_GPU_LOCK_SCOPES: dict[str, list[int]] = {
    "render": [0],
    "qa_judge": [1],
    "llm_primary": [0],
}
# Host names that reach the same host port as host.docker.internal.
_LOOPBACK_HOST_ALIASES = ("localhost", "127.0.0.1")

_HTTP_TIMEOUT_SECONDS = 15
_RELOAD_TIMEOUT_SECONDS = 420  # a 30B reload is 40-85 s; leave slack for a cold page cache

_SOURCE = "brain.ollama_runner_ram_watch"
_RECYCLED_KIND = "ollama_runner_ram_recycled"
_FAILED_KIND = "ollama_runner_ram_recycle_failed"

_last_recycle_monotonic: dict[str, float] = {}

_ANON_RE = re.compile(r'^ollama_runner_anon_bytes\{unit="([^"]+)"\}\s+([0-9.eE+-]+)\s*$')
_CPU_RE = re.compile(r'^ollama_runner_cpu_percent\{unit="([^"]+)"\}\s+([0-9.eE+-]+)\s*$')


def _reset_recycle_state() -> None:
    """Test seam — clears the per-unit cooldown stamps."""
    _last_recycle_monotonic.clear()


def _require_http_url(url: str, what: str) -> str:
    """Reject anything but http/https before it reaches ``urlopen``.

    Both URLs here come from ``app_settings``, so this is operator input, not
    attacker input — but ``urlopen`` honours ``file://`` and other schemes, and
    a typo that silently reads a local file (then parses it as Prometheus text,
    or POSTs a recycle at it) is a confusing failure rather than a loud one.
    Constraining the scheme makes the failure explicit and satisfies bandit
    B310 in substance rather than by annotation.
    """
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"{what} must be an http(s) URL, got {url!r}")
    return url


def default_exporter_url() -> str:
    """The gpu-exporter, reached the way brain_daemon already reaches it."""
    in_docker = os.path.exists("/.dockerenv")
    host = "host.docker.internal" if in_docker else "localhost"
    return f"http://{host}:9835/metrics"


def parse_targets(raw: Any) -> list[tuple[str, float, str, str]]:
    """Parse ``unit|watermark_gb|endpoint|model`` CSV entries.

    Pipe-delimited on purpose. Two fields carry their own colons — the endpoint
    is a URL and the model carries a ``:tag`` — so a colon split is ambiguous
    no matter which end you split from: ``a:4:http://h:11435:qwen3-vl:30b``
    right-splits to model ``30b``, endpoint ``http://h:11435:qwen3-vl``. Both
    look plausible and neither is right.
    """
    if raw is None:
        return []
    out: list[tuple[str, float, str, str]] = []
    for entry in str(raw).split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = [f.strip() for f in entry.split("|")]
        if len(parts) != 4:
            logger.warning(
                "[OLLAMA_RAM] target %r is not unit|watermark_gb|endpoint|model "
                "— skipping",
                entry,
            )
            continue
        unit, watermark_raw, endpoint, model = parts
        try:
            watermark = float(watermark_raw)
        except ValueError:
            logger.warning(
                "[OLLAMA_RAM] target %r watermark unparseable — skipping", entry
            )
            continue
        if not unit or not endpoint or not model or watermark <= 0:
            logger.warning("[OLLAMA_RAM] target %r is incomplete — skipping", entry)
            continue
        out.append((unit, watermark, endpoint, model))
    return out


async def _read_config(pool: Any) -> dict[str, Any]:
    return {
        "enabled": coerce_bool(
            await read_setting(pool, ENABLED_KEY, "false"), DEFAULT_ENABLED
        ),
        "targets": parse_targets(
            await read_setting(pool, TARGETS_KEY, DEFAULT_TARGETS)
        ),
        "cooldown_minutes": coerce_int(
            await read_setting(pool, COOLDOWN_MINUTES_KEY, DEFAULT_COOLDOWN_MINUTES),
            DEFAULT_COOLDOWN_MINUTES,
        ),
        "cpu_idle_percent": coerce_float(
            await read_setting(pool, CPU_IDLE_PERCENT_KEY, DEFAULT_CPU_IDLE_PERCENT),
            DEFAULT_CPU_IDLE_PERCENT,
        ),
        "require_gpu_lock_free": coerce_bool(
            await read_setting(pool, REQUIRE_GPU_LOCK_FREE_KEY, "true"),
            DEFAULT_REQUIRE_GPU_LOCK_FREE,
        ),
        "exporter_url": str(
            await read_setting(pool, EXPORTER_URL_KEY, DEFAULT_EXPORTER_URL) or ""
        ).strip()
        or default_exporter_url(),
        "num_ctx": coerce_int(
            await read_setting(pool, PINNED_NUM_CTX_KEY, DEFAULT_PINNED_NUM_CTX),
            DEFAULT_PINNED_NUM_CTX,
        ),
    }


async def _read_lock_scope_config(pool: Any) -> dict[str, Any]:
    """The five rows the scope resolves from. Read only when a target is over."""
    return {
        "per_device_enabled": coerce_bool(
            await read_setting(pool, GPU_LOCK_PER_DEVICE_KEY, "false"), False
        ),
        "scopes_raw": str(await read_setting(pool, GPU_LOCK_SCOPES_KEY, "") or ""),
        "node_id": str(await read_setting(pool, GPU_LOCK_NODE_ID_KEY, "") or ""),
        "primary_url": str(await read_setting(pool, OLLAMA_BASE_URL_KEY, "") or ""),
        "vision_url": str(
            await read_setting(pool, OLLAMA_VISION_BASE_URL_KEY, "") or ""
        ),
    }


# --- lock scope --------------------------------------------------------------


def device_lock_key(node_id: str, gpu_index: int) -> int:
    """Advisory key for one physical card — ``gpu_scheduler.device_lock_key``."""
    digest = zlib.crc32(f"{node_id}:{gpu_index}".encode()) & 0xFFFFFFFF
    return GPU_ADVISORY_LOCK_KEY + 1 + digest


def canonical_base_url(url: Any) -> str:
    """Comparable identity of a base URL — ``gpu_scheduler._canonical_base_url``.

    Case and a trailing slash never name a different instance, and
    ``localhost`` / ``127.0.0.1`` name the host port a container reaches as
    ``host.docker.internal``. Nothing else is folded.
    """
    text = str(url or "").strip().rstrip("/").lower()
    for alias in _LOOPBACK_HOST_ALIASES:
        text = text.replace(f"://{alias}:", "://host.docker.internal:")
    return text


def endpoint_role(endpoint: str, *, primary_url: str, vision_url: str) -> str:
    """Scope role of the ollama instance at ``endpoint``; ``""`` when unknown.

    ``gpu_scheduler.ollama_host_role``: the ``ollama_base_url`` instance is
    ``llm_primary``, the ``ollama_vision_base_url`` one is ``qa_judge``, and the
    primary wins a tie (a vision URL pointing back at the primary names the
    same server, on the primary's cards).
    """
    host = canonical_base_url(endpoint)
    if not host:
        return ""
    if host == canonical_base_url(primary_url or DEFAULT_OLLAMA_URL):
        return "llm_primary"
    judge = canonical_base_url(vision_url)
    if judge and host == judge:
        return "qa_judge"
    return ""


def _parse_scope_map(raw: str) -> dict[str, list[int]]:
    """``gpu_scheduler._parse_scope_map``. Raises on anything malformed."""
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"{GPU_LOCK_SCOPES_KEY} must be a JSON object")
    return {str(role): [int(i) for i in idxs] for role, idxs in parsed.items()}


@dataclass(frozen=True)
class LockScope:
    """Which advisory-lock rows make one target busy.

    ``keys is None`` is the whole-box read: any session on the base key, in
    any mode. Otherwise it is a session on one of ``keys`` (the target's cards)
    or an EXCLUSIVE session on the base key. ``label`` says which, in words.
    """

    keys: tuple[int, ...] | None
    label: str


def resolve_lock_scope(
    endpoint: str,
    *,
    per_device_enabled: bool,
    scopes_raw: str,
    node_id: str,
    primary_url: str,
    vision_url: str,
) -> LockScope:
    """The keys a caller of ``endpoint``'s instance takes, or the whole box.

    Mirrors what ``gpu_scheduler.resolve_lock_keys`` hands a caller whose role
    is the instance's role, and falls back to the whole-box read wherever the
    worker could be taking something else. Every fallback is conservative: it
    can only defer a recycle, never permit one mid-call.
    """
    whole = "every GPU"
    if not per_device_enabled:
        return LockScope(None, f"{whole} ({GPU_LOCK_PER_DEVICE_KEY} is off)")
    node = str(node_id or "").strip()
    if not node:
        # A containerised worker with no explicit node id takes the whole-GPU
        # key; a bare-host one would use its hostname. The brain cannot tell
        # which callers are which, so it derives no card keys at all.
        return LockScope(None, f"{whole} ({GPU_LOCK_NODE_ID_KEY} is unset)")
    raw = str(scopes_raw or "").strip()
    try:
        scopes = _parse_scope_map(raw) if raw else DEFAULT_GPU_LOCK_SCOPES
    except (ValueError, TypeError):
        # The worker falls back to its defaults here; which map a given worker
        # process is actually using is unknowable from outside, so read wide.
        return LockScope(None, f"{whole} ({GPU_LOCK_SCOPES_KEY} is unparseable)")
    role = endpoint_role(endpoint, primary_url=primary_url, vision_url=vision_url)
    if not role:
        return LockScope(
            None,
            f"{whole} ({endpoint} is neither {OLLAMA_BASE_URL_KEY} nor "
            f"{OLLAMA_VISION_BASE_URL_KEY})",
        )
    if role not in scopes:
        # A worker whose role is missing takes EVERY device key (fail-closed),
        # which the whole-box read also covers.
        return LockScope(None, f"{whole} (role {role!r} missing from {GPU_LOCK_SCOPES_KEY})")
    cards = sorted({int(i) for i in scopes[role]})
    keys = tuple(sorted({device_lock_key(node, card) for card in cards}))
    where = "GPU " + ", ".join(str(c) for c in cards) if cards else "no GPU"
    return LockScope(keys, f"{where} ({role})")


# --- measurement -------------------------------------------------------------


def parse_runner_stats(exposition: str) -> dict[str, dict[str, float]]:
    """``{unit: {"anon_gb": .., "cpu_percent": ..}}`` from the exporter's text.

    ``cpu_percent`` is absent on the exporter's very first scrape after start —
    it is a rate and needs two samples. Absent is left absent rather than
    defaulted to 0.0, because 0.0 reads as "idle, safe to recycle" and would
    let the probe recycle a busy runner during the exporter's first interval.
    """
    out: dict[str, dict[str, float]] = {}
    for line in exposition.splitlines():
        line = line.strip()
        for pattern, field, scale in (
            (_ANON_RE, "anon_gb", 1024**3),
            (_CPU_RE, "cpu_percent", 1),
        ):
            match = pattern.match(line)
            if not match:
                continue
            try:
                out.setdefault(match.group(1), {})[field] = float(match.group(2)) / scale
            except ValueError:
                logger.warning("[OLLAMA_RAM] unparseable metric line: %r", line)
            break
    return out


def read_runner_stats(exporter_url: str) -> dict[str, dict[str, float]] | None:
    """Scrape the gpu-exporter. ``None`` = unreachable (NOT 'nothing running')."""
    try:
        _require_http_url(exporter_url, "exporter_url")
        with urllib.request.urlopen(  # nosec B310 - scheme constrained to http(s) above
            exporter_url, timeout=_HTTP_TIMEOUT_SECONDS
        ) as resp:
            return parse_runner_stats(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("[OLLAMA_RAM] exporter %s unreadable: %s", exporter_url, exc)
        return None


# --- idle gate ---------------------------------------------------------------

# Every advisory-lock row on the base key or one of the target's card keys,
# granted or queued, with whoever holds it. ``pg_advisory_lock(bigint)`` splits
# the key across classid/objid, so reassemble with a shift rather than
# hardcoding halves (same reassembly as sidecar_ram_watch).
_LOCK_ROWS_SQL = (
    "SELECT ((l.classid::bigint << 32) | l.objid::bigint) AS key,"
    "       l.mode, l.granted, coalesce(a.application_name, '') AS holder"
    "  FROM pg_locks l LEFT JOIN pg_stat_activity a ON a.pid = l.pid"
    " WHERE l.locktype = 'advisory' AND l.objsubid = 1"
    "   AND ((l.classid::bigint << 32) | l.objid::bigint) = ANY($1::bigint[])"
)

_WHOLE_BOX = LockScope(None, "every GPU")


def lock_rows_busy(rows: Any, keys: tuple[int, ...] | None) -> list[str]:
    """The sessions in ``rows`` that make the target busy; empty means free.

    Whole-box read (``keys is None``): any session on the base key, in any
    mode. Scoped read: a session on one of the target's card keys, or an
    EXCLUSIVE session on the base key — an unscoped session claims every card.
    A SHARED base row alone is a scoped session on some card, and its own card
    key says which. Queued sessions count: they are about to call.
    """
    busy: list[str] = []
    for row in rows:
        key = int(row["key"])
        if keys is None:
            hit = key == GPU_ADVISORY_LOCK_KEY
        else:
            hit = key in keys or (
                key == GPU_ADVISORY_LOCK_KEY and row["mode"] == "ExclusiveLock"
            )
        if hit:
            who = str(row["holder"] or "") or "an untagged session"
            busy.append(who if row["granted"] else f"{who} (queued)")
    return busy


async def gpu_lock_held(pool: Any, scope: LockScope = _WHOLE_BOX) -> bool | None:
    """True = a GPU session covers the target, False = free, None = unknown.

    With the default scope this is the old whole-box read. Whoever makes the
    target busy is logged, because the probe's deferral is otherwise invisible:
    the heartbeat records only ok/issue, and a probe that defers for hours
    looks exactly like one with nothing to do.
    """
    wanted = [GPU_ADVISORY_LOCK_KEY, *(scope.keys or ())]
    try:
        rows = await pool.fetch(_LOCK_ROWS_SQL, wanted)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[OLLAMA_RAM] GPU lock query failed: %s", exc)
        return None
    busy = lock_rows_busy(rows, scope.keys)
    if busy:
        logger.info(
            "[OLLAMA_RAM] lock covering %s held by: %s",
            scope.label,
            "; ".join(sorted(set(busy))[:5]),
        )
    return bool(busy)


async def _prove_idle(
    pool: Any,
    unit: str,
    stats: dict[str, float],
    *,
    cpu_idle_percent: float,
    require_gpu_lock_free: bool,
    gpu_lock_fn: Callable[[], Awaitable[bool | None]],
    lock_label: str = _WHOLE_BOX.label,
) -> tuple[bool, str]:
    """``(is_idle, why_not)``. Unprovable counts as NOT idle, by design (#3094).

    Two gates, and they see different traffic.

    The GPU advisory lock, read for the target's own cards (``lock_label``).
    It sees the callers that lock the judge explicitly (image captioning, media
    QA) and any whole-box session. It does NOT see QA rails or qa_shot_vision:
    since #2646 a dispatch to a model pinned to its own endpoint takes no lock.
    It used to be read for the whole box, which let any render anywhere defer
    the recycle for hours without protecting anything on the judge's card.

    The runner's own CPU, which is the gate that sees an in-flight request,
    locked or not: a runner mid-generation pegs a core, an idle one reads
    0.0%. The exporter computes it over its last 10 s collector interval.

    ``/api/ps`` is deliberately NOT used here. It reports which model is
    LOADED, not whether it is generating, so it answers a different question
    and would read "idle" mid-inference.
    """
    if require_gpu_lock_free:
        held = await gpu_lock_fn()
        if held is None:
            return False, "GPU lock state unknown"
        if held:
            return False, f"a GPU session holds the scheduler lock covering {lock_label}"
    cpu = stats.get("cpu_percent")
    if cpu is None:
        return False, f"{unit} runner CPU unknown (exporter needs two scrapes)"
    if cpu >= cpu_idle_percent:
        return False, f"{unit} runner CPU {cpu:.1f}% >= {cpu_idle_percent:g}%"
    return True, ""


# --- the recycle -------------------------------------------------------------


def recycle_runner(endpoint: str, model: str, num_ctx: int) -> tuple[bool, str]:
    """Unload then re-pin. Unloading is what frees the memory.

    ``keep_alive: 0`` makes ollama terminate the runner process; the cached
    prompt states die with it. The reload is issued eagerly so its 40-85 s
    lands here rather than on whichever QA rail calls next, and it re-pins with
    ``keep_alive: -1`` so the model stays resident as the placement doctrine
    intends. It loads at ``num_ctx`` (``pinned_llm_endpoint_num_ctx``), the size
    the rails will ask for; loading at any other size just moves the reload onto
    the next rail call. ``num_ctx <= 0`` sends none (the instance default).
    """
    repin: dict[str, Any] = {"model": model, "prompt": "", "stream": False, "keep_alive": -1}
    if num_ctx > 0:
        repin["options"] = {"num_ctx": num_ctx}

    def _post(body: dict[str, Any], timeout: int) -> None:
        req = urllib.request.Request(
            f"{endpoint}/api/generate",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(  # nosec B310 - scheme constrained to http(s) above
            req, timeout=timeout
        ) as resp:
            resp.read()

    try:
        _require_http_url(endpoint, "target endpoint")
        _post({"model": model, "keep_alive": 0}, _HTTP_TIMEOUT_SECONDS)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"unload failed: {exc}"
    try:
        _post(repin, _RELOAD_TIMEOUT_SECONDS)
    except (urllib.error.URLError, OSError) as exc:
        # The memory IS freed at this point — the unload succeeded. Report the
        # partial outcome loudly rather than as a failure that implies nothing
        # happened; the next caller will load the model on demand.
        return True, f"unloaded, but re-pin failed ({exc}) — will load on demand"
    size = f"num_ctx={num_ctx}" if num_ctx > 0 else "the instance's default num_ctx"
    return True, f"unloaded and re-pinned at {size}"


# --- the probe ---------------------------------------------------------------


async def run_ollama_runner_ram_watch_probe(
    pool: Any,
    *,
    gpu_lock_fn: Callable[[], Awaitable[bool | None]] | None = None,
    mem_fn: Callable[[str], dict[str, dict[str, float]] | None] | None = None,
    recycle_fn: Callable[[str, str, int], tuple[bool, str]] | None = None,
    now_fn: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Single cycle of the ollama-runner host-RAM recycle watch.

    ``gpu_lock_fn`` replaces the lock read wholesale (tests); by default the
    lock is read for the chosen target's own cards, resolved per cycle so a
    settings change lands on the next one.
    """
    mem_fn = mem_fn or read_runner_stats
    recycle_fn = recycle_fn or recycle_runner
    now_fn = now_fn or time.monotonic

    config = await _read_config(pool)
    if not config["enabled"]:
        return {"ok": True, "status": "disabled", "detail": f"{ENABLED_KEY}=false"}

    targets = config["targets"]
    if not targets:
        return {
            "ok": True,
            "status": "no_targets",
            "detail": f"{TARGETS_KEY} is empty or unparseable — nothing watched",
        }

    measured = await asyncio.to_thread(mem_fn, config["exporter_url"])
    if measured is None:
        return {
            "ok": False,
            "status": "exporter_unreachable",
            "detail": (
                f"gpu-exporter at {config['exporter_url']} unreadable — cannot "
                f"tell a runner over its watermark from one under it, so nothing "
                f"is recycled"
            ),
        }

    cooldown_minutes = int(config["cooldown_minutes"])
    over: list[tuple[float, str, float, str, str, dict[str, float]]] = []
    skipped: list[str] = []

    for unit, watermark_gb, endpoint, model in targets:
        stamp = _last_recycle_monotonic.get(unit)
        if stamp is not None and cooldown_minutes > 0:
            since_s = now_fn() - stamp
            if since_s < cooldown_minutes * 60.0:
                skipped.append(
                    f"{unit}: cooldown ({since_s / 60.0:.0f}m < {cooldown_minutes}m)"
                )
                continue
        stats = measured.get(unit)
        anon_gb = (stats or {}).get("anon_gb")
        if anon_gb is None:
            skipped.append(f"{unit}: no runner (not loaded)")
            continue
        if anon_gb < watermark_gb:
            skipped.append(f"{unit}: {anon_gb:.1f} GB < {watermark_gb:g} GB")
            continue
        over.append((anon_gb, unit, watermark_gb, endpoint, model, stats or {}))

    if not over:
        return {
            "ok": True,
            "status": "under_watermark",
            "detail": "; ".join(skipped) or "nothing over watermark",
        }

    # One recycle per cycle, fattest first — bounds the blast radius the same
    # way sidecar_ram_watch does.
    anon_gb, unit, watermark_gb, endpoint, model, stats = max(
        over, key=lambda row: row[0]
    )

    scope = resolve_lock_scope(endpoint, **await _read_lock_scope_config(pool))
    lock_fn = gpu_lock_fn or (lambda: gpu_lock_held(pool, scope))
    idle, why_not = await _prove_idle(
        pool,
        unit,
        stats,
        cpu_idle_percent=float(config["cpu_idle_percent"]),
        require_gpu_lock_free=bool(config["require_gpu_lock_free"]),
        gpu_lock_fn=lock_fn,
        lock_label=scope.label,
    )
    if not idle:
        detail = f"{unit} at {anon_gb:.1f} GB but not idle: {why_not}"
        logger.info("[OLLAMA_RAM] deferred: %s", detail)
        return {
            "ok": True,
            "status": "deferred",
            "detail": detail,
            "unit": unit,
            "anon_gb": round(anon_gb, 2),
            "lock_scope": scope.label,
        }

    ok, detail = await asyncio.to_thread(recycle_fn, endpoint, model, int(config["num_ctx"]))
    if not ok:
        await emit_finding(
            pool,
            source=_SOURCE,
            kind=_FAILED_KIND,
            severity="warning",
            title=f"Ollama runner recycle failed: {unit} ({anon_gb:.1f} GB)",
            body=(
                f"{unit} crossed its {watermark_gb:g} GB watermark while idle, "
                f"but the recycle against {endpoint} failed: {detail}. The "
                f"memory is still held. A failed attempt does not start the "
                f"{COOLDOWN_MINUTES_KEY} cooldown, so the probe retries on its "
                f"next cycle."
            ),
            dedup_key=f"{_FAILED_KIND}:{unit}",
            extra={"unit": unit, "anon_gb": round(anon_gb, 2), "error": detail},
        )
        return {"ok": False, "status": "recycle_failed", "detail": detail, "unit": unit}

    _last_recycle_monotonic[unit] = now_fn()
    if config["require_gpu_lock_free"]:
        idle_proof = (
            f"no GPU session held the scheduler lock covering {scope.label}, and "
            f"the runner's CPU was under {config['cpu_idle_percent']:g}%"
        )
    else:
        idle_proof = (
            f"the runner's CPU was under {config['cpu_idle_percent']:g}% (the "
            f"lock gate is off: {REQUIRE_GPU_LOCK_FREE_KEY}=false)"
        )
    await emit_finding(
        pool,
        source=_SOURCE,
        kind=_RECYCLED_KIND,
        severity="info",
        title=f"Ollama runner recycled: {unit} {anon_gb:.1f} GB reclaimed",
        body=(
            f"{unit}'s llama-server runner held {anon_gb:.1f} GB of host "
            f"anonymous memory (RssAnon+VmSwap), past its {watermark_gb:g} GB "
            f"watermark, while provably idle: {idle_proof}. The brain unloaded "
            f"and re-pinned {model} on {endpoint}, which terminates the runner "
            f"process and returns the memory to the host: {detail}. The memory "
            f"is llama-server's host-RAM prompt cache (--cache-ram, 8 GiB by "
            f"default, plus ~46 MiB of image data per cached vision prompt that "
            f"the cap does not count), so this recurs by design: it is a "
            f"recycle, not a fix. Setting LLAMA_ARG_CACHE_RAM on the unit caps "
            f"it at the source. Tune via app_settings.{TARGETS_KEY} / "
            f"{COOLDOWN_MINUTES_KEY}; disable via {ENABLED_KEY}."
        ),
        dedup_key=f"{_RECYCLED_KIND}:{unit}",
        extra={
            "unit": unit,
            "anon_gb": round(anon_gb, 2),
            "watermark_gb": watermark_gb,
            "endpoint": endpoint,
            "model": model,
            "num_ctx": int(config["num_ctx"]),
            "lock_scope": scope.label,
        },
    )
    return {
        "ok": True,
        "status": "recycled",
        "detail": f"{unit}: {anon_gb:.1f} GB reclaimed — {detail}",
        "unit": unit,
        "anon_gb": round(anon_gb, 2),
        "watermark_gb": watermark_gb,
        "lock_scope": scope.label,
    }


class OllamaRunnerRamWatchProbe:
    """Probe-Protocol wrapper (mirrors SidecarRamWatchProbe)."""

    name: str = "ollama_runner_ram_watch"
    description: str = (
        "Watches ollama llama-server runners' host anonymous memory "
        "(RssAnon+VmSwap, via the gpu-exporter, since host systemd units are "
        "invisible to cadvisor) and unload/re-pins the fattest over-watermark "
        "one through ollama's API — only when no GPU lock covers that "
        "endpoint's own cards and its runner CPU is idle. Emits "
        "ollama_runner_ram_recycled (info)."
    )
    interval_seconds: int = 600

    async def check(self, pool, config):  # type: ignore[override]
        from poindexter.brain.probe_interface import ProbeResult
        summary = await run_ollama_runner_ram_watch_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("detail", summary.get("status", "")),
            metrics={"status": summary.get("status")},
            severity="warning" if not summary.get("ok") else "info",
        )
