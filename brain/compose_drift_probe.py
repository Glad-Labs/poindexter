"""Compose-spec drift probe (Glad-Labs/poindexter#213).

Sibling to ``migration_drift_probe.py`` (#228). Where the migration probe
catches *schema* drift (worker has a new migration file that hasn't been
applied to the live DB), this probe catches *container spec* drift —
the case where ``docker-compose.local.yml`` was updated (new mount, new
env var, new exposed port, new image tag) but the running container
wasn't recreated. The compose file and the live container then disagree
silently. Symptoms surface far from the cause: a panel goes empty
because Prometheus is on a stale spec missing the new scrape target,
the worker can't read a new bind-mount because the container was never
recreated, etc.

Probe behavior every 5-min cycle:

1. Read ``docker-compose.local.yml`` from a configurable path (defaults to
   ``/app/docker-compose.local.yml`` — bind-mount the brain container has
   from the operator's repo).
2. For each service in the YAML, run ``docker inspect <container_name>``
   to pull the live ``Config`` + ``HostConfig`` and compare:

   * **Bind mounts** — every host-path bind in ``volumes:`` should be in
     ``HostConfig.Binds`` / ``HostConfig.Mounts``.
   * **Env vars** — every key in ``environment:`` should be in
     ``Config.Env`` (key presence only — values aren't compared, to avoid
     leaking secrets to logs / audit rows).
   * **Image tag** — ``Config.Image`` should match the YAML's ``image:``
     (or for ``build:`` entries, the auto-generated build tag).
   * **Port publishing** — every host-side port in ``ports:`` should be
     in ``HostConfig.PortBindings``.

3. If drift is detected on N services, emit per-service ``audit_log``
   events with the diff summary (no secret values), then call
   :func:`brain.operator_notifier.notify_operator` once with a count
   summary. Capped at one notification per cycle, with a dedupe pattern
   so a stuck "5 services drifting forever" doesn't blast Telegram every
   five minutes.
4. If ``compose_drift_auto_recover_enabled`` (app_setting, default
   ``"false"``) is true, run
   ``docker compose -f docker-compose.local.yml up -d <drifted-services>``
   to recreate them. Wait for them to come back, re-probe, and emit a
   recovery event if drift cleared, or escalate if it didn't.

Things deliberately NOT compared (mirrors the issue's scope):

* ``command:`` / ``entrypoint:`` — array-vs-string formatting differs
  enough between YAML and ``docker inspect`` to make this brittle.
* ``restart:`` policy — normalized differently between the two.
* ``networks:`` / ``depends_on:`` — compose-runtime concerns, not
  container-spec.

Standalone module — depends on stdlib + PyYAML (added to brain
dependencies for #213). Mirrors the patterns of
``brain/migration_drift_probe.py`` so it slots into the existing probe
registry without new infrastructure.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from typing import Any, Iterable

# Compose env-var interpolation: ${VAR}, ${VAR:-default}, $VAR.
# Matches Docker Compose's variable substitution rules — see
# https://docs.docker.com/compose/compose-file/12-interpolation/.
# We support the two most common forms (the bare and `:-default` ones);
# the rarer `:?error` and `:+replacement` forms aren't expanded —
# they degrade to empty string, which manifests as missing-mount drift
# the operator can fix by setting the var.
_ENV_VAR_RE = re.compile(
    r"""
    \$\{
        (?P<name1>[A-Za-z_][A-Za-z0-9_]*)
        (?: :- (?P<default>[^}]*) )?
    \}
    |
    \$ (?P<name2>[A-Za-z_][A-Za-z0-9_]*)
    """,
    re.VERBOSE,
)


def _expand_compose_value(s: str) -> str:
    """Expand ``${VAR}``, ``${VAR:-default}``, and ``$VAR`` in a compose value.

    Compose YAML uses env-var interpolation in mount paths and ports
    (``${HOME:-.}/.cache/x:/cache``). PyYAML doesn't expand it, but the
    running container's docker-inspect output has the resolved value.
    Without expansion, the per-service diff sees `${HOME` and `/cache` as
    separate colon-split parts and reports false drift.
    """
    def _replace(m: re.Match) -> str:
        name = m.group("name1") or m.group("name2")
        if not name:  # pragma: no cover — re shouldn't match nothing
            return m.group(0)
        val = os.environ.get(name, "")
        if val:
            return val
        default = m.group("default")
        return default if default is not None else ""

    return _ENV_VAR_RE.sub(_replace, s)

try:  # PyYAML is the only new dep (#213).
    import yaml as _yaml
except ImportError:  # pragma: no cover — bubbled up below
    _yaml = None

try:  # Flat import when brain/ is on sys.path (container runtime).
    from operator_notifier import notify_operator
except ImportError:  # pragma: no cover — package-qualified for tests
    from brain.operator_notifier import notify_operator

logger = logging.getLogger("brain.compose_drift_probe")

# ---------------------------------------------------------------------------
# Tunables — all read from app_settings at probe time so an operator can
# adjust without redeploying the brain.
# ---------------------------------------------------------------------------

# Whether to call ``docker compose up -d`` on drifted services. Defaults
# to False so a buggy probe can't cause cascading container recreates on
# Matt's instance. Operators flip it on with
# ``poindexter set compose_drift_auto_recover_enabled true`` once
# they're confident in the probe's accuracy.
AUTO_RECOVER_SETTING_KEY = "compose_drift_auto_recover_enabled"

# Path to the compose file inside the brain container. The brain
# container bind-mounts the host's ``./docker-compose.local.yml`` into
# this path read-only; configurable so an operator with a non-standard
# layout can point us at a different file.
COMPOSE_PATH_SETTING_KEY = "compose_spec_path"
COMPOSE_PATH_DEFAULT = "/app/docker-compose.local.yml"

# Comma-separated list of service names to skip — useful for services
# with intentional spec drift (e.g. a sidecar that gets hot-patched in
# place during a migration). Empty by default.
SKIP_SERVICES_SETTING_KEY = "compose_drift_skip_services"

# Comma-separated list of services that legitimately run on-demand
# (spun up only when needed, then shut down to free GPU/CPU resources).
# When such a service is missing, we suppress the `container_missing`
# alert path — the probe still inspects+diffs env/mounts/ports if the
# container *does* happen to be running, so genuine spec drift on these
# services is still caught. See Glad-Labs/poindexter#425.
ON_DEMAND_SERVICES_SETTING_KEY = "compose_drift_on_demand_services"
ON_DEMAND_SERVICES_DEFAULT = "wan-server,sdxl-server"

# How long to wait after ``docker compose up -d`` before re-probing.
RECOVER_WAIT_SECONDS = 30

# Subprocess timeout for ``docker inspect`` / ``docker compose`` calls.
# Generous because ``docker compose up`` on a stack can take >30s when
# multiple services need to be recreated.
DOCKER_COMMAND_TIMEOUT_SECONDS = 120

# Probe interval — runs every brain cycle (5 min). The dedupe state
# below is what keeps it from spamming.
PROBE_INTERVAL_SECONDS = 300

# Module-level dedupe state: last-cycle drifted service names. Reset to
# an empty set when drift clears so a fresh drift event re-notifies.
# ``frozenset`` so the equality check is order-insensitive.
_last_notified_drifted: frozenset[str] = frozenset()

# Timestamp of the last notify_fn fire for the current drift set.
# 2026-05-12 bug: with set-only dedup, persistent drift on the same
# service set went silent forever after the first notify (Matt's
# compose drift wasn't paging for 38+ hours despite firing every 5
# min). Re-notify after this window so persistent drift gets re-paged.
_last_notified_at: float = 0.0
_RENOTIFY_AFTER_SECONDS = 3600.0  # 1 hour


# ---------------------------------------------------------------------------
# YAML / spec parsing
# ---------------------------------------------------------------------------


def _load_compose_yaml(path: str) -> dict[str, Any] | None:
    """Read + parse the compose file. Returns None on any failure.

    We deliberately don't raise — the probe degrades to a structured
    "can't tell" status rather than crashing the brain cycle.
    """
    if _yaml is None:
        logger.warning(
            "[COMPOSE_DRIFT] PyYAML not installed in brain image — "
            "skipping. Add `pyyaml` to brain/pyproject.toml."
        )
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = _yaml.safe_load(fh)
        if not isinstance(data, dict):
            logger.warning(
                "[COMPOSE_DRIFT] %s parsed but isn't a mapping (%s)",
                path, type(data).__name__,
            )
            return None
        return data
    except FileNotFoundError:
        logger.info(
            "[COMPOSE_DRIFT] Compose file not found at %s — "
            "set %s in app_settings or bind-mount the file into the brain.",
            path, COMPOSE_PATH_SETTING_KEY,
        )
        return None
    except Exception as exc:
        logger.warning(
            "[COMPOSE_DRIFT] Failed to parse %s: %s",
            path, exc,
        )
        return None


def _yaml_env_keys(env_block: Any) -> set[str]:
    """Normalize a compose `environment:` block into a set of keys.

    Compose accepts either dict form ({KEY: value}) or list form
    (["KEY=value", "KEY"]). Both reduce to the set of declared keys.
    """
    if not env_block:
        return set()
    keys: set[str] = set()
    if isinstance(env_block, dict):
        for k in env_block.keys():
            if k:
                keys.add(str(k))
    elif isinstance(env_block, list):
        for entry in env_block:
            if not isinstance(entry, str):
                continue
            # "KEY=value" → KEY ; bare "KEY" → KEY
            key, _sep, _val = entry.partition("=")
            if key:
                keys.add(key.strip())
    return keys


def _yaml_volume_targets(volumes_block: Any) -> set[str]:
    """Extract the container-side mount targets from `volumes:`.

    Compose entries can be either short string form
    (``./host:/container:ro``) or long dict form
    (``{type: bind, source: ./host, target: /container}``). Either way we
    care about the container-side path because that's what
    ``docker inspect`` lets us cross-check.
    """
    if not volumes_block:
        return set()
    targets: set[str] = set()
    if not isinstance(volumes_block, list):
        return targets
    for entry in volumes_block:
        if isinstance(entry, str):
            # Expand ${VAR:-default} env vars BEFORE splitting on `:`,
            # otherwise the colon inside `${HOME:-.}` butchers the parse.
            expanded = _expand_compose_value(entry)
            # "src:target[:mode]"
            parts = expanded.split(":")
            if len(parts) >= 2:
                target = parts[1]
                if target:
                    targets.add(target)
        elif isinstance(entry, dict):
            t = entry.get("target") or entry.get("destination")
            if t:
                targets.add(_expand_compose_value(str(t)))
    return targets


def _yaml_port_host_publishings(ports_block: Any) -> set[str]:
    """Extract host-side port publishings from `ports:`.

    Compose port specs can be:
      - "8080:80"            → host port 8080
      - "127.0.0.1:8080:80"  → host port 8080 (with bind addr)
      - "8080"               → host port 8080
      - {published: 8080, target: 80}
    We normalize to the published port number as a string.
    """
    if not ports_block:
        return set()
    if not isinstance(ports_block, list):
        return set()
    out: set[str] = set()
    for entry in ports_block:
        if isinstance(entry, (int, str)):
            # Expand env vars first — see _yaml_volume_targets for why.
            text = _expand_compose_value(str(entry))
            # "host:container" or "ip:host:container"
            parts = text.split(":")
            if len(parts) == 1:
                out.add(parts[0])
            elif len(parts) == 2:
                out.add(parts[0])
            elif len(parts) >= 3:
                out.add(parts[1])
        elif isinstance(entry, dict):
            published = entry.get("published")
            if published is not None:
                out.add(_expand_compose_value(str(published)))
    return out


def _yaml_image_tag(service_block: dict[str, Any], service_name: str) -> str | None:
    """Derive the image tag for a service.

    For ``image: foo:bar`` we return ``"foo:bar"``. For ``build:`` only
    services we return None — image tag comparison isn't meaningful when
    the operator builds a fresh tag every up. (Bind mounts + env vars
    still get compared, which is where most build-only drift hides.)
    """
    image = service_block.get("image")
    if isinstance(image, str) and image.strip():
        return image.strip()
    return None


# ---------------------------------------------------------------------------
# Docker inspect helpers
# ---------------------------------------------------------------------------


def _docker_reachable() -> tuple[bool, str]:
    """Pre-flight check that the docker daemon is actually reachable.

    Without this, a brain container that has the socket bind-mounted but
    can't read it (root-owned socket, brain runs as non-root) treats every
    service as "container missing" and writes one audit_log row per
    service per cycle. Matt's prod hit 2117 such rows in 6h before this
    guard.

    Returns ``(True, "")`` on success or ``(False, reason)`` so the
    caller can short-circuit and surface the reason in audit_log.
    """
    try:
        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": DOCKER_COMMAND_TIMEOUT_SECONDS,
        }
        if os.name == "nt":  # pragma: no cover
            kwargs["creationflags"] = 0x08000000
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            **kwargs,
        )
        if result.returncode == 0 and (result.stdout or "").strip():
            return True, ""
        return False, (result.stderr or result.stdout or "no output").strip()[:200]
    except FileNotFoundError:
        return False, "docker CLI not on PATH"
    except subprocess.TimeoutExpired:
        return False, f"docker version timed out after {DOCKER_COMMAND_TIMEOUT_SECONDS}s"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"


def _docker_inspect(container_name: str) -> dict[str, Any] | None:
    """Run ``docker inspect`` and return the parsed JSON body.

    Returns None if the container doesn't exist or docker errored. The
    probe treats a missing container as drift (it's "in the spec but not
    running") and reports it in the diff.
    """
    try:
        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": DOCKER_COMMAND_TIMEOUT_SECONDS,
        }
        if os.name == "nt":  # pragma: no cover — host-side dev only
            kwargs["creationflags"] = 0x08000000
        result = subprocess.run(
            ["docker", "inspect", container_name],
            **kwargs,
        )
        if result.returncode != 0:
            logger.debug(
                "[COMPOSE_DRIFT] docker inspect %s exit %d: %s",
                container_name, result.returncode,
                (result.stderr or "").strip()[:160],
            )
            return None
        body = json.loads(result.stdout or "[]")
        if isinstance(body, list) and body:
            first = body[0]
            return first if isinstance(first, dict) else None
        return None
    except FileNotFoundError:
        logger.warning(
            "[COMPOSE_DRIFT] docker CLI not on PATH — brain image is "
            "missing the docker binary; can't inspect containers."
        )
        return None
    except subprocess.TimeoutExpired:
        logger.warning(
            "[COMPOSE_DRIFT] docker inspect %s timed out after %ds",
            container_name, DOCKER_COMMAND_TIMEOUT_SECONDS,
        )
        return None
    except Exception as exc:
        logger.warning(
            "[COMPOSE_DRIFT] docker inspect %s error: %s",
            container_name, exc,
        )
        return None


def _live_env_keys(inspect: dict[str, Any]) -> set[str]:
    """Extract env-var KEYS (no values) from inspect's Config.Env."""
    config = inspect.get("Config") or {}
    env = config.get("Env") or []
    keys: set[str] = set()
    for entry in env:
        if isinstance(entry, str):
            key, _sep, _val = entry.partition("=")
            if key:
                keys.add(key)
    return keys


def _live_mount_targets(inspect: dict[str, Any]) -> set[str]:
    """Extract container-side mount destinations from inspect.

    Honors both ``HostConfig.Binds`` (string form ``src:dest[:ro]``) and
    ``Mounts[].Destination`` (the structured form Docker also reports).
    Either is sufficient — we union them.
    """
    targets: set[str] = set()
    host_config = inspect.get("HostConfig") or {}
    binds = host_config.get("Binds") or []
    for b in binds:
        if isinstance(b, str):
            parts = b.split(":")
            if len(parts) >= 2:
                target = parts[1]
                if target:
                    targets.add(target)
    mounts = inspect.get("Mounts") or []
    for m in mounts:
        if isinstance(m, dict):
            dest = m.get("Destination") or m.get("Target")
            if dest:
                targets.add(str(dest))
    return targets


def _live_port_publishings(inspect: dict[str, Any]) -> set[str]:
    """Extract published host ports from HostConfig.PortBindings."""
    host_config = inspect.get("HostConfig") or {}
    bindings = host_config.get("PortBindings") or {}
    out: set[str] = set()
    if not isinstance(bindings, dict):
        return out
    for _container_port_proto, host_specs in bindings.items():
        if not host_specs:
            continue
        if isinstance(host_specs, list):
            for spec in host_specs:
                if isinstance(spec, dict):
                    hp = spec.get("HostPort")
                    if hp:
                        out.add(str(hp))
    return out


def _live_image_tag(inspect: dict[str, Any]) -> str | None:
    """Pull Config.Image (the image the container was started from)."""
    config = inspect.get("Config") or {}
    image = config.get("Image")
    if isinstance(image, str) and image.strip():
        return image.strip()
    return None


# ---------------------------------------------------------------------------
# Diffing
# ---------------------------------------------------------------------------


def _diff_service(
    yaml_block: dict[str, Any],
    inspect: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a per-service drift summary.

    Output shape::

        {
            "drifted": bool,
            "missing_env": [keys],            # in YAML, not in container
            "missing_mounts": [targets],      # in YAML, not in container
            "missing_ports": [host_ports],    # in YAML, not in container
            "image_mismatch": (yaml, live) or None,
            "container_missing": bool,        # container doesn't exist
        }

    Only "missing in container" deltas count as drift — extra runtime
    items (env vars set by Docker, system mounts like /etc/hosts) are
    legitimately added by Docker and shouldn't trigger an alert.
    """
    out: dict[str, Any] = {
        "drifted": False,
        "missing_env": [],
        "missing_mounts": [],
        "missing_ports": [],
        "image_mismatch": None,
        "container_missing": False,
    }

    if inspect is None:
        out["drifted"] = True
        out["container_missing"] = True
        return out

    yaml_env = _yaml_env_keys(yaml_block.get("environment"))
    live_env = _live_env_keys(inspect)
    missing_env = sorted(yaml_env - live_env)
    if missing_env:
        out["missing_env"] = missing_env

    yaml_mounts = _yaml_volume_targets(yaml_block.get("volumes"))
    live_mounts = _live_mount_targets(inspect)
    missing_mounts = sorted(yaml_mounts - live_mounts)
    if missing_mounts:
        out["missing_mounts"] = missing_mounts

    yaml_ports = _yaml_port_host_publishings(yaml_block.get("ports"))
    live_ports = _live_port_publishings(inspect)
    missing_ports = sorted(yaml_ports - live_ports)
    if missing_ports:
        out["missing_ports"] = missing_ports

    yaml_image = _yaml_image_tag(yaml_block, "")
    live_image = _live_image_tag(inspect)
    if yaml_image and live_image and yaml_image != live_image:
        out["image_mismatch"] = (yaml_image, live_image)

    if (
        out["missing_env"]
        or out["missing_mounts"]
        or out["missing_ports"]
        or out["image_mismatch"]
    ):
        out["drifted"] = True

    return out


def _summarize_diff(diff: dict[str, Any]) -> str:
    """Human-readable one-liner for a drift summary (no secret values)."""
    if diff.get("container_missing"):
        return "container not running"
    parts: list[str] = []
    if diff.get("missing_env"):
        # Just key names — values never logged.
        parts.append(f"env keys missing: {', '.join(diff['missing_env'])}")
    if diff.get("missing_mounts"):
        parts.append(f"mounts missing: {', '.join(diff['missing_mounts'])}")
    if diff.get("missing_ports"):
        parts.append(f"ports missing: {', '.join(diff['missing_ports'])}")
    if diff.get("image_mismatch"):
        y, live = diff["image_mismatch"]
        parts.append(f"image mismatch: yaml={y} live={live}")
    return "; ".join(parts) or "no diff"


# ---------------------------------------------------------------------------
# DB I/O — settings + audit log
# ---------------------------------------------------------------------------


async def _read_setting(pool, key: str, default: str = "") -> str:
    """Read a string app_settings value. Defaults gracefully on failure."""
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            key,
        )
    except Exception as exc:
        logger.warning(
            "[COMPOSE_DRIFT] Could not read %s from app_settings: %s",
            key, exc,
        )
        return default
    if val is None:
        return default
    return str(val)


async def _read_auto_recover_enabled(pool) -> bool:
    val = await _read_setting(pool, AUTO_RECOVER_SETTING_KEY, default="")
    return val.strip().lower() in ("true", "1", "yes", "on")


async def _read_compose_path(pool) -> str:
    val = await _read_setting(
        pool, COMPOSE_PATH_SETTING_KEY, default=COMPOSE_PATH_DEFAULT
    )
    return val.strip() or COMPOSE_PATH_DEFAULT


async def _read_skip_services(pool) -> set[str]:
    val = await _read_setting(pool, SKIP_SERVICES_SETTING_KEY, default="")
    return {s.strip() for s in val.split(",") if s.strip()}


async def _read_on_demand_services(pool) -> set[str]:
    val = await _read_setting(
        pool,
        ON_DEMAND_SERVICES_SETTING_KEY,
        default=ON_DEMAND_SERVICES_DEFAULT,
    )
    return {s.strip() for s in val.split(",") if s.strip()}


async def _emit_audit_event(
    pool,
    event: str,
    detail: str,
    *,
    extra: dict[str, Any] | None = None,
) -> None:
    """Best-effort audit_log write. Mirrors migration_drift_probe pattern."""
    payload: dict[str, Any] = {"detail": detail}
    if extra:
        payload.update(extra)
    try:
        await pool.execute(
            """
            INSERT INTO audit_log (event_type, source, details, severity)
            VALUES ($1, $2, $3::jsonb, $4)
            """,
            event,
            "brain.compose_drift_probe",
            json.dumps(payload),
            "warning" if "detected" in event else "info",
        )
    except Exception as exc:
        logger.debug(
            "[COMPOSE_DRIFT] Could not write audit event %s: %s",
            event, exc,
        )


# ---------------------------------------------------------------------------
# Recovery — docker compose up -d <services>
# ---------------------------------------------------------------------------


def _recreate_services(
    compose_path: str, services: Iterable[str]
) -> tuple[bool, str]:
    """Run ``docker compose -f <path> up -d <services>``.

    Returns (ok, message). Never raises — caller handles the bool.
    """
    svc_list = list(services)
    if not svc_list:
        return True, "no services to recreate"
    try:
        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": DOCKER_COMMAND_TIMEOUT_SECONDS,
        }
        if os.name == "nt":  # pragma: no cover
            kwargs["creationflags"] = 0x08000000
        result = subprocess.run(
            ["docker", "compose", "-f", compose_path, "up", "-d", *svc_list],
            **kwargs,
        )
        if result.returncode == 0:
            return True, f"Recreated: {', '.join(svc_list)}"
        return False, (
            f"docker compose up exit {result.returncode}: "
            f"{(result.stderr or '').strip()[:200]}"
        )
    except FileNotFoundError:
        return False, "docker CLI not on PATH"
    except subprocess.TimeoutExpired:
        return False, (
            f"docker compose up timed out after "
            f"{DOCKER_COMMAND_TIMEOUT_SECONDS}s"
        )
    except Exception as exc:
        return False, f"docker compose up error: {type(exc).__name__}: {str(exc)[:160]}"


# ---------------------------------------------------------------------------
# Top-level probe entry point
# ---------------------------------------------------------------------------


async def run_compose_drift_probe(
    pool,
    *,
    notify_fn=None,
    inspect_fn=None,
    recreate_fn=None,
    yaml_loader=None,
    sleep_fn=time.sleep,
    docker_reachable_fn=None,
) -> dict[str, Any]:
    """Single execution of the compose-spec drift probe.

    Args:
        pool: asyncpg pool for app_settings + audit_log writes.
        notify_fn: operator notifier callable. Defaults to
            :func:`brain.operator_notifier.notify_operator`.
        inspect_fn: ``container_name -> dict | None``. Defaults to
            :func:`_docker_inspect`.
        recreate_fn: ``(compose_path, [services]) -> (ok, msg)``. Defaults
            to :func:`_recreate_services`.
        yaml_loader: ``path -> dict | None``. Defaults to
            :func:`_load_compose_yaml`.
        sleep_fn: callable used to wait between recreate and re-probe so
            tests can substitute a no-op.

    Returns a summary dict for storage in brain_decisions /
    probe_results.
    """
    global _last_notified_drifted

    notify_fn = notify_fn or notify_operator
    inspect_fn = inspect_fn or _docker_inspect
    recreate_fn = recreate_fn or _recreate_services
    yaml_loader = yaml_loader or _load_compose_yaml
    docker_reachable_fn = docker_reachable_fn or _docker_reachable

    compose_path = await _read_compose_path(pool)
    skip_services = await _read_skip_services(pool)
    on_demand_services = await _read_on_demand_services(pool)
    auto_recover_enabled = await _read_auto_recover_enabled(pool)

    # ---- 0) Pre-flight: docker daemon reachable? ----------------------------
    # Without this, an unreachable docker daemon (socket bind-mounted but
    # not readable, daemon stopped, CLI missing) makes every per-service
    # `docker inspect` return None, which the diff treats as "container
    # missing" and writes one audit_log row per service per cycle. Matt's
    # prod hit 2117 such rows in 6h before this guard.
    docker_ok, docker_reason = docker_reachable_fn()
    if not docker_ok:
        detail = (
            f"Docker daemon unreachable from brain — can't check drift. "
            f"Reason: {docker_reason}. Most common cause is the docker "
            f"socket being root-owned while the brain runs as a non-root "
            f"user; bind-mount with the right group or grant the brain "
            f"user access to /var/run/docker.sock."
        )
        logger.info("[COMPOSE_DRIFT] %s", detail)
        await _emit_audit_event(
            pool, "probe.compose_drift_unknown", detail
        )
        return {
            "ok": True,  # not OUR failure to surface
            "status": "unknown",
            "detail": detail,
            "compose_path": compose_path,
            "auto_recover_enabled": auto_recover_enabled,
            "docker_reachable": False,
            "docker_unreachable_reason": docker_reason,
        }

    # ---- 1) Load + parse compose --------------------------------------------
    spec = yaml_loader(compose_path)
    if spec is None:
        detail = (
            f"Compose spec unreadable at {compose_path} (PyYAML missing or "
            f"file not bind-mounted) — can't check drift"
        )
        logger.info("[COMPOSE_DRIFT] %s", detail)
        await _emit_audit_event(
            pool, "probe.compose_drift_unknown", detail
        )
        return {
            "ok": True,  # not OUR failure — separate concern
            "status": "unknown",
            "detail": detail,
            "compose_path": compose_path,
            "auto_recover_enabled": auto_recover_enabled,
        }

    services = spec.get("services") or {}
    if not isinstance(services, dict) or not services:
        detail = (
            f"Compose spec has no 'services:' mapping at {compose_path} "
            f"— nothing to compare"
        )
        logger.info("[COMPOSE_DRIFT] %s", detail)
        await _emit_audit_event(
            pool, "probe.compose_drift_unknown", detail
        )
        return {
            "ok": True,
            "status": "unknown",
            "detail": detail,
            "compose_path": compose_path,
            "auto_recover_enabled": auto_recover_enabled,
        }

    # ---- 2) Diff each service against its running container ----------------
    drifted: dict[str, dict[str, Any]] = {}
    inspected_count = 0
    for svc_name, svc_block in services.items():
        if not isinstance(svc_block, dict):
            continue
        if svc_name in skip_services:
            logger.debug(
                "[COMPOSE_DRIFT] Skipping %s (in %s)",
                svc_name, SKIP_SERVICES_SETTING_KEY,
            )
            continue
        # Compose's container_name overrides the default project_service_N
        # naming. Most poindexter services set it explicitly, so we can
        # rely on it for ``docker inspect``. If it's missing, skip rather
        # than guess project prefixes — guessing would create false positives.
        container_name = svc_block.get("container_name")
        if not container_name:
            logger.debug(
                "[COMPOSE_DRIFT] Service %s has no container_name — "
                "skipping (would need to guess project prefix)", svc_name,
            )
            continue
        inspect = inspect_fn(container_name)
        inspected_count += 1
        diff = _diff_service(svc_block, inspect)
        # On-demand services (e.g. wan-server, sdxl-server) spin up only
        # when needed — `container_missing` is the expected steady state,
        # not drift. Suppress that specific signal but keep diffing if the
        # container does happen to be running, so genuine env/mount/port
        # drift still surfaces. See Glad-Labs/poindexter#425.
        if diff["container_missing"] and svc_name in on_demand_services:
            logger.debug(
                "[COMPOSE_DRIFT] %s missing but flagged on-demand "
                "(%s) — suppressing container_missing alert",
                svc_name, ON_DEMAND_SERVICES_SETTING_KEY,
            )
            continue
        if diff["drifted"]:
            drifted[svc_name] = {
                "container": container_name,
                "diff": diff,
                "summary": _summarize_diff(diff),
            }

    # ---- 3) Happy path: no drift -------------------------------------------
    if not drifted:
        if _last_notified_drifted:
            logger.info(
                "[COMPOSE_DRIFT] Drift cleared (was %d service(s), now 0)",
                len(_last_notified_drifted),
            )
            _last_notified_drifted = frozenset()
        await _emit_audit_event(
            pool,
            "probe.compose_drift_ok",
            f"No drift across {inspected_count} service(s) inspected",
        )
        return {
            "ok": True,
            "status": "no_drift",
            "detail": f"No drift across {inspected_count} service(s)",
            "drifted_count": 0,
            "inspected_count": inspected_count,
            "compose_path": compose_path,
            "auto_recover_enabled": auto_recover_enabled,
        }

    # ---- 4) Drift > 0 — always per-service audit ---------------------------
    drifted_names = frozenset(drifted.keys())
    detected_summary = (
        f"{len(drifted)} service(s) drifted from compose spec: "
        + ", ".join(f"{n} ({d['summary']})" for n, d in drifted.items())
    )
    logger.warning("[COMPOSE_DRIFT] %s", detected_summary)
    for svc_name, info in drifted.items():
        await _emit_audit_event(
            pool,
            "probe.compose_drift_detected",
            f"{svc_name}: {info['summary']}",
            extra={
                "service": svc_name,
                "container": info["container"],
                # diff fields are key-only — no secret values leak.
                "missing_env": info["diff"]["missing_env"],
                "missing_mounts": info["diff"]["missing_mounts"],
                "missing_ports": info["diff"]["missing_ports"],
                "image_mismatch": info["diff"]["image_mismatch"],
                "container_missing": info["diff"]["container_missing"],
                "auto_recover_enabled": auto_recover_enabled,
            },
        )

    # ---- 5a) Auto-recover disabled — periodic notify (set-aware + time-bound) --
    if not auto_recover_enabled:
        global _last_notified_at
        now_ts = time.time()
        # Fire when the drift set changed OR the renotify window elapsed
        # on the same set. The time gate stops persistent drift from
        # going silent for hours after the first page (2026-05-12 bug).
        set_changed = drifted_names != _last_notified_drifted
        time_elapsed = (now_ts - _last_notified_at) >= _RENOTIFY_AFTER_SECONDS
        if set_changed or time_elapsed:
            try:
                notify_fn(
                    title=f"Compose drift detected ({len(drifted)} service(s))",
                    detail=(
                        f"{detected_summary}\n\n"
                        f"Auto-recover is DISABLED "
                        f"(app_settings.{AUTO_RECOVER_SETTING_KEY}=false).\n"
                        f"Recommended fix: run `docker compose -f "
                        f"{compose_path} up -d "
                        f"{' '.join(sorted(drifted))}` to recreate the "
                        f"drifted services, or enable auto-recover via "
                        f"`poindexter set {AUTO_RECOVER_SETTING_KEY} true`."
                    ),
                    source="brain.compose_drift_probe",
                    severity="warning",
                )
                _last_notified_drifted = drifted_names
                _last_notified_at = now_ts
            except Exception as exc:
                logger.warning(
                    "[COMPOSE_DRIFT] notify_fn failed: %s", exc
                )
        else:
            logger.debug(
                "[COMPOSE_DRIFT] Drift unchanged across %d service(s) "
                "and within renotify window — skipping duplicate notification",
                len(drifted),
            )
        return {
            "ok": False,
            "status": "drift_detected_no_recover",
            "detail": detected_summary,
            "drifted_count": len(drifted),
            "drifted_services": sorted(drifted),
            "inspected_count": inspected_count,
            "compose_path": compose_path,
            "auto_recover_enabled": False,
            "notified": _last_notified_drifted == drifted_names,
        }

    # ---- 5b) Auto-recover enabled — recreate, wait, re-probe ---------------
    logger.info(
        "[COMPOSE_DRIFT] Auto-recover enabled — recreating %s",
        ", ".join(sorted(drifted)),
    )
    recreate_ok, recreate_msg = recreate_fn(compose_path, sorted(drifted))
    if not recreate_ok:
        try:
            notify_fn(
                title=(
                    f"Compose drift auto-recover FAILED to recreate "
                    f"{len(drifted)} service(s)"
                ),
                detail=(
                    f"{detected_summary}\n\n"
                    f"docker compose up failed: {recreate_msg}\n\n"
                    f"Recommended fix: investigate why the brain container "
                    f"can't reach the docker socket, or recreate the "
                    f"services manually."
                ),
                source="brain.compose_drift_probe",
                severity="critical",
            )
        except Exception as exc:
            logger.warning("[COMPOSE_DRIFT] notify_fn failed: %s", exc)
        await _emit_audit_event(
            pool,
            "probe.compose_drift_recover_failed",
            f"docker compose up failed: {recreate_msg}",
            extra={"drifted_services": sorted(drifted)},
        )
        _last_notified_drifted = drifted_names
        return {
            "ok": False,
            "status": "recover_recreate_failed",
            "detail": f"{detected_summary} — recreate failed: {recreate_msg}",
            "drifted_count": len(drifted),
            "drifted_services": sorted(drifted),
            "compose_path": compose_path,
            "auto_recover_enabled": True,
        }

    # Wait for the containers to settle, then re-probe.
    sleep_fn(RECOVER_WAIT_SECONDS)
    post_drifted: dict[str, dict[str, Any]] = {}
    for svc_name, info in drifted.items():
        post_inspect = inspect_fn(info["container"])
        post_diff = _diff_service(services.get(svc_name) or {}, post_inspect)
        if post_diff["drifted"]:
            post_drifted[svc_name] = {
                "container": info["container"],
                "diff": post_diff,
                "summary": _summarize_diff(post_diff),
            }

    if not post_drifted:
        recovered_detail = (
            f"Drift cleared after recreate: was {len(drifted)} service(s), now 0"
        )
        logger.info("[COMPOSE_DRIFT] %s", recovered_detail)
        await _emit_audit_event(
            pool,
            "probe.compose_drift_recovered",
            recovered_detail,
            extra={
                "previous_drifted_services": sorted(drifted),
                "recreate_msg": recreate_msg,
            },
        )
        _last_notified_drifted = frozenset()
        return {
            "ok": True,
            "status": "recovered",
            "detail": recovered_detail,
            "drifted_count": 0,
            "previous_drifted_services": sorted(drifted),
            "compose_path": compose_path,
            "auto_recover_enabled": True,
        }

    # Recreate happened, but drift persists — escalate.
    persistent_detail = (
        f"Drift persists after recreate: was {len(drifted)}, "
        f"now {len(post_drifted)} ("
        + ", ".join(f"{n} ({d['summary']})" for n, d in post_drifted.items())
        + ")"
    )
    try:
        notify_fn(
            title=(
                f"Compose drift PERSISTS after auto-recreate "
                f"({len(post_drifted)} service(s))"
            ),
            detail=(
                f"{persistent_detail}\n\n"
                f"`docker compose up -d` ran successfully but the running "
                f"containers still don't match the spec. This may mean the "
                f"compose file references a build that's failing, an image "
                f"tag that can't be pulled, or a mount source that doesn't "
                f"exist on the host.\n\n"
                f"Auto-recover will not retry this cycle. Disable it with "
                f"`poindexter set {AUTO_RECOVER_SETTING_KEY} false` if "
                f"you don't want it to retry next cycle either."
            ),
            source="brain.compose_drift_probe",
            severity="critical",
        )
    except Exception as exc:
        logger.warning("[COMPOSE_DRIFT] notify_fn failed: %s", exc)
    await _emit_audit_event(
        pool,
        "probe.compose_drift_recover_failed",
        persistent_detail,
        extra={
            "previous_drifted_services": sorted(drifted),
            "still_drifted_services": sorted(post_drifted),
        },
    )
    _last_notified_drifted = frozenset(post_drifted.keys())
    return {
        "ok": False,
        "status": "recover_drift_persists",
        "detail": persistent_detail,
        "drifted_count": len(post_drifted),
        "drifted_services": sorted(post_drifted),
        "previous_drifted_services": sorted(drifted),
        "compose_path": compose_path,
        "auto_recover_enabled": True,
    }


# ---------------------------------------------------------------------------
# Probe Protocol adapter — for the registry-driven path.
# ---------------------------------------------------------------------------


class ComposeDriftProbe:
    """Probe-Protocol-compatible wrapper around :func:`run_compose_drift_probe`."""

    name: str = "compose_drift"
    description: str = (
        "Detects drift between docker-compose.local.yml and the running "
        "containers (missing mounts/env/ports/image tag), and (when "
        "enabled) auto-recreates the drifted services."
    )
    interval_seconds: int = PROBE_INTERVAL_SECONDS

    async def check(self, pool, config):  # type: ignore[override]
        try:
            from probe_interface import ProbeResult
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult

        summary = await run_compose_drift_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("detail", ""),
            metrics={
                k: summary[k]
                for k in (
                    "drifted_count",
                    "inspected_count",
                    "drifted_services",
                    "status",
                )
                if k in summary
            },
            severity="warning" if not summary.get("ok") else "info",
        )
