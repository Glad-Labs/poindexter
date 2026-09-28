#!/usr/bin/env python3
"""Post-deploy health gate with automatic rollback (deploy-checkout-sync step 6b).

Stdlib only — runs on the host under systemd with the system python3, no venv.

Three subcommands, all driven by the deploy sync:

    snapshot --services a b …       -> JSON {service: {container, image_ref, image_id,
                                          manifest_digest, platform, rollback_ref, rollback_note}}
        Taken BEFORE an image rebuild. Each service's running image is TAGGED
        as ``<repository>:rollback-<service>`` (``rollback_ref``), and that tag
        is the rollback target. Recording the image id is not enough: under
        the containerd image store the rebuild deletes the old image record as
        it moves the tag, even while a container still runs it, so an id
        restored after the build names nothing. One tag per service, moved by
        every snapshot; the image it held before is deleted once nothing names
        or uses it. See ``preserve``. One line per service on stderr says what
        a failed gate could roll back to.

    recreate-plan --since EPOCH --services a b …   (step 6a-bis)
        -> one ``<action>\\t<service>\\t<reason>`` line per service. Taken
        AFTER compose-apply: which rebuilt services does compose still leave
        on the previous image? ``recreate`` (content differs, or it cannot be
        told — recreate to be safe), ``skip`` (already running what its image
        ref names, usually because compose-apply just recreated it), or
        ``parked`` (stopped before this deploy, or no container at all —
        recreating it would start it). See ``plan_recreate``.

    verify --snapshot pre.json --services a b … [--rollback] [--timeout N]
        Taken AFTER compose-apply. Polls each service's (new) container until
        it is healthy, or until it shows a definitive failure signal:
        ``restarting``, ``exited``/``dead``, health ``unhealthy``, or a
        ``RestartCount`` of 2+ on a container that was just created. On
        failure with ``--rollback`` and a preserved rollback image, that
        image is re-tagged over the compose image ref, the service is
        recreated onto it, and the new container must be running exactly the
        preserved content before the rollback counts; a critical alert_events
        row carries the container's last log lines either way.

Every subcommand takes ``--project NAME``, the stack's compose project, and
then considers only containers carrying that project's label. Other projects
on the host reuse the same service names (see ``in_project``).

Why this exists (2026-09-13): the deploy sync rebuilt the chatterbox image on
a merged change, recreated the container, logged "Pipeline now running …",
and never looked back. The container died on import and restarted 507 times
over eight hours. A deploy that rebuilds an image and walks away has not
deployed anything; it has placed a bet.

Exit codes: 0 = every gated service healthy; 2 = at least one service rolled
back; 1 = at least one service failed and could not be rolled back (or timed
out without a definitive verdict). The caller records the marker on 0 and 2
(the pass is handled) and withholds it on 1 only for genuine gate errors.

Settings (app_settings, read via psql; defaults apply when unreadable):
    deploy_health_gate_seconds          how long to wait for healthy (300)
    deploy_health_gate_settle_seconds   no-healthcheck services must run this long (30)
    deploy_rollback_on_unhealthy        'true' to roll back, 'false' to page only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

DEFAULT_TIMEOUT = 300
DEFAULT_SETTLE = 30
POLL_SECONDS = 5
LOG_TAIL = 20
PG_CONTAINER = "poindexter-postgres-local"

Runner = Callable[[list[str]], tuple[int, str, str]]


def _run(argv: list[str], timeout: int = 120) -> tuple[int, str, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)  # noqa: S603
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{' '.join(argv[:3])}: timed out after {timeout}s"


# ---------------------------------------------------------------------------
# settings (DB-first; psql through the postgres container like the heartbeat)
# ---------------------------------------------------------------------------

def _psql(sql: str, run: Runner = _run, **variables: str) -> tuple[int, str, str]:
    """Run one statement through the postgres container's psql.

    Values travel as psql variables (``-v name=value``) and are interpolated
    with ``:'name'``, which psql quotes as a proper SQL literal — so no value
    is ever spliced into SQL text here, and the JSON payloads' quotes and
    dollar signs cannot break out of their literal.
    """
    argv = ["docker", "exec", PG_CONTAINER, "psql", "-U", "poindexter", "-d", "poindexter_brain", "-tA"]
    for name, value in variables.items():
        argv += ["-v", f"{name}={value}"]
    argv += ["-c", sql]
    return run(argv)


def read_setting(key: str, default: str, run: Runner = _run) -> str:
    rc, out, _ = _psql("SELECT value FROM app_settings WHERE key = :'k'", run, k=key)
    val = out.strip() if rc == 0 else ""
    return val or default


def read_int_setting(key: str, default: int, run: Runner = _run) -> int:
    try:
        return int(read_setting(key, str(default), run))
    except ValueError:
        return default


def read_bool_setting(key: str, default: bool, run: Runner = _run) -> bool:
    val = read_setting(key, "true" if default else "false", run).strip().lower()
    return val in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# docker
# ---------------------------------------------------------------------------

CONTAINER_PREFIX = "container:"  # a unit named "container:<name>" is verified by container name, never rolled back

# A `compose run` container carries its service's label too, with
# com.docker.compose.oneoff=True. It is not the service: a demo-recorder bake in
# flight (`run --rm`) must not be judged, recreated or gated as demo-recorder,
# and a manual `run` beside a live service must not read as a second container
# of that service. Service containers carry oneoff=False (checked on the live
# stack, 2026-09-28).
NOT_ONE_OFF = ("--filter", "label=com.docker.compose.oneoff=False")


def in_project(project: str | None) -> tuple[str, ...]:
    """The ``docker ps`` filter that keeps another compose project's containers out.

    The service label alone matches every project on the host, and ``docker ps``
    lists the newest first. Throwaway projects are routine here: on 2026-09-28
    a worktree's ``seedorder-repro`` (the consumer compose file, so the same
    service names) left exited ``brain-daemon`` and ``worker`` containers newer
    than the stack's. Unscoped, the next brain rebuild would have snapshotted
    that container, could have gated it (``exited`` = a failed deploy) and
    rolled the stack's brain back over it, and the recreate check read "2
    containers" as "recreate to be safe". Unscoped (no filter) when the
    caller names no project.
    """
    return ("--filter", f"label=com.docker.compose.project={project}") if project else ()


def find_container(service: str, run: Runner = _run, project: str | None = None) -> str | None:
    """The container compose created for ``service`` in ``project`` (label-based, name-agnostic).

    A unit spelled ``container:<name>`` (the deploy's bounce-restarted bind-mount
    containers, addressed by name) resolves to that name directly.
    """
    if service.startswith(CONTAINER_PREFIX):
        return service[len(CONTAINER_PREFIX):] or None
    rc, out, _ = run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.service={service}",
                      *NOT_ONE_OFF, *in_project(project), "--format", "{{.Names}}"])
    names = [n.strip() for n in out.splitlines() if n.strip()] if rc == 0 else []
    return names[0] if names else None


def inspect(container: str, run: Runner = _run) -> dict[str, Any] | None:
    rc, out, _ = run(["docker", "inspect", container])
    if rc != 0:
        return None
    try:
        body = json.loads(out or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(body, list) or not body or not isinstance(body[0], dict):
        return None
    c = body[0]
    state = c.get("State") or {}
    # The platform manifest the container was created from. Present under the
    # containerd image store only; see image_identity() for why it matters.
    descriptor = c.get("ImageManifestDescriptor") or {}
    return {
        "status": str(state.get("Status") or ""),
        "restarting": bool(state.get("Restarting")),
        "restart_count": int(c.get("RestartCount") or 0),
        "health": (state.get("Health") or {}).get("Status"),
        "has_healthcheck": bool((c.get("Config") or {}).get("Healthcheck")),
        "started_at": state.get("StartedAt"),
        "created": str(c.get("Created") or ""),
        "exit_code": state.get("ExitCode"),
        "image_ref": str((c.get("Config") or {}).get("Image") or ""),
        "image_id": str(c.get("Image") or ""),
        "manifest_digest": str(descriptor.get("digest") or ""),
        "platform": _platform(descriptor.get("platform")),
    }


def _platform(p: Any) -> str:
    """``os/architecture[/variant]`` from an OCI platform object, "" if absent."""
    if not isinstance(p, dict) or not p.get("os") or not p.get("architecture"):
        return ""
    return "/".join(str(x) for x in (p["os"], p["architecture"], p.get("variant")) if x)


def _epoch(stamp: Any) -> float | None:
    """A docker timestamp (``2026-09-27T21:40:36.123456789Z``) as epoch seconds.

    docker emits nanoseconds and a ``Z``; ``datetime.fromisoformat`` before
    Python 3.11 takes neither, so both are normalised first. None when the
    stamp is missing or unparseable — callers must then decline to judge.
    """
    text = str(stamp or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    match = re.match(r"^(.*\.\d{1,6})\d*([+-]\d{2}:\d{2})?$", text)
    if match:
        text = match.group(1) + (match.group(2) or "")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


def log_tail(container: str, run: Runner = _run, lines: int = LOG_TAIL) -> str:
    rc, out, err = run(["docker", "logs", "--tail", str(lines), container])
    text = "\n".join(line.rstrip() for line in (out + err).splitlines() if line.strip())
    return text[-2500:] if text else "(no log output)"


# ---------------------------------------------------------------------------
# image identity: is a container running what its image ref names NOW?
# ---------------------------------------------------------------------------

def _short(digest: str) -> str:
    return digest.split(":")[-1][:12] if digest else "?"


def _tidy(line: str) -> str:
    """A docker error, readable in one log line: no daemon preamble, digests shortened."""
    line = line.strip().removeprefix("Error response from daemon: ")
    return re.sub(r"sha256:([0-9a-f]{12})[0-9a-f]{52}", r"sha256:\1", line)[:200]


def _first_line(err: str, fallback: str = "no detail") -> str:
    return _tidy((err.strip().splitlines() or [fallback])[0])


def _last_line(err: str) -> str:
    """compose writes progress to stderr too; its error is the last line, not the first."""
    return _tidy((err.strip().splitlines() or ["no detail"])[-1])


def _content(rec: dict[str, Any]) -> tuple[str, str]:
    """``(what it runs, platform)`` for an ``inspect()`` result or a snapshot entry.

    Under the containerd image store that is the platform manifest digest, and
    a ref is resolved with ``--platform`` to compare against it. On the classic
    store (no manifest descriptor) it is the image ID, and the platform is "".
    See image_identity() for why the image ID is useless on this host.
    """
    if rec.get("manifest_digest"):
        return str(rec["manifest_digest"]), str(rec.get("platform") or "")
    return str(rec.get("image_id") or ""), ""


def _names(ref: str, platform: str, run: Runner = _run) -> tuple[str, str]:
    """``(digest, error)`` for what ``ref`` names now, in _content() terms."""
    argv = ["docker", "image", "inspect", *(["--platform", platform] if platform else []), ref, "--format", "{{.Id}}"]
    rc, out, err = run(argv)
    digest = out.strip() if rc == 0 else ""
    return digest, "" if digest else _first_line(err, "no digest returned")


def image_identity(container: str, run: Runner = _run, info: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compare the image content ``container`` runs with what its image ref names now.

    Compared on the PLATFORM MANIFEST digest, not on ``docker inspect .Image``
    vs ``docker image inspect .Id``. Under the containerd image store (this
    host, Docker 29) an image ID is the digest of an OCI index that also holds
    the build's attestation manifest, and that is minted fresh on EVERY build:
    two builds of an unchanged Dockerfile get two IDs around one identical
    platform manifest (measured 2026-09-28). Comparing IDs therefore reads
    every no-op rebuild as "rebuilt and never recreated" — the false positive
    that had five services reported stale on 2026-09-22 and led to a
    force-recreate of every rebuilt service on every deploy. The platform
    manifest changes exactly when the image content does, and compose records
    the same digest in its ``com.docker.compose.image`` label: ``up -d``
    recreates a container when it differs and leaves a no-op rebuild alone
    (compose 5.5.1, dry-run and real run alike).

    The running side comes from the container's own ``ImageManifestDescriptor``
    because the image it was created from does not survive a same-tag rebuild
    on this store: ``docker image inspect <old id>`` answers "No such image"
    while the container is still running it. Without a descriptor (the classic
    store) ``.Image`` / ``.Id`` are compared as before.

    Returns ``{"same", "running", "tagged", "image_ref", "why"}``. ``same`` is
    None when it cannot be decided, and callers must treat that as unknown,
    never as a match.
    """
    out: dict[str, Any] = {"same": None, "running": "", "tagged": "", "image_ref": "", "why": ""}
    info = info if info is not None else inspect(container, run)
    if info is None:
        out["why"] = f"docker inspect {container} failed"
        return out
    ref = out["image_ref"] = info["image_ref"]
    if not ref:
        out["why"] = f"{container} records no image ref"
        return out
    running, platform = _content(info)
    if info["manifest_digest"] and not platform:
        out["why"] = f"{container}'s manifest descriptor names no platform"
        return out
    out["running"] = running
    if not running:
        out["why"] = f"{container} records no image"
        return out
    out["tagged"], err = _names(ref, platform, run)
    if not out["tagged"]:
        out["why"] = f"docker image inspect {ref}: {err}"[:200]
        return out
    out["same"] = running == out["tagged"]
    return out


# ---------------------------------------------------------------------------
# rollback image: keep what runs now through the rebuild (snapshot)
# ---------------------------------------------------------------------------

ROLLBACK_TAG_PREFIX = "rollback-"
# The deploy sync logs a snapshot note that starts with this at WARN: a
# service with a container that a failed gate could not roll back.
NO_ROLLBACK_NOTE = "no rollback image for "


def _repository(image_ref: str) -> str:
    """``image_ref`` without its tag or digest; "" when it is an image ID.

    ``host:5000/team/app:1.2`` -> ``host:5000/team/app``. A colon is a tag
    separator only after the last slash; before it, it is a registry port.
    """
    ref = image_ref.split("@", 1)[0]
    if not ref or ref.startswith("sha256:") or re.fullmatch(r"[0-9a-f]{64}", ref):
        return ""
    colon, slash = ref.rfind(":"), ref.rfind("/")
    return ref[:colon] if colon > slash else ref


def rollback_ref_for(image_ref: str, service: str) -> str:
    """The tag that holds ``service``'s pre-rebuild image: ``<repository>:rollback-<service>``.

    One fixed tag per service, so each snapshot MOVES it rather than adding
    another (and ``preserve`` deletes the image it held before once nothing
    names it): nothing accumulates. Named per service rather than per image
    because services share image refs (backup-daily / -hourly / -offsite all
    run ``poindexter-backup``) and need not all run the same image when the
    snapshot is taken. "" when the ref has no repository to tag under.
    """
    repo = _repository(image_ref)
    return f"{repo}:{ROLLBACK_TAG_PREFIX}{service}" if repo else ""


def _confirm(target: str, running: str, platform: str, how: str, run: Runner) -> tuple[str, str]:
    """Read ``target`` back: it counts only if it names exactly what the container runs."""
    held, err = _names(target, platform, run)
    if held != running:
        return "", f"tagged {target}, but it names {_short(held) if held else err}, not the running {_short(running)}"
    return target, f"{'manifest' if platform else 'image'} {_short(running)}, {how}"


def _release(previous: str, run: Runner) -> None:
    """Delete ``previous``, the image the rollback tag held before this snapshot moved it, if it is now unnamed.

    ``docker tag`` over an existing tag does not delete the image the tag
    named: the daemon keeps it as an untagged (dangling) image. Left there,
    every snapshot would leave the one before's rollback image behind, one per
    service per rebuild. It is deleted only when no tag names it any more,
    because ``docker image rm <id>`` on an image with one tag left removes
    that tag, and the tag could be the service's live one. Never forced, so an
    image a container still uses stays.
    """
    rc, out, _ = run(["docker", "image", "inspect", previous, "--format", "{{json .RepoTags}}"])
    if rc == 0 and out.strip() in ("[]", "null"):
        run(["docker", "image", "rm", previous])


def preserve(service: str, container: str, info: dict[str, Any], run: Runner = _run) -> tuple[str, str]:
    """Tag the image ``container`` runs as ``rollback_ref_for()``. Returns ``(rollback_ref, note)``.

    ``rollback_ref`` is "" when nothing could be preserved, and ``note`` says
    why; otherwise ``note`` says what the tag holds.

    The tag is what keeps the image through the rebuild. Under the containerd
    image store (this host, Docker 29) a same-tag rebuild deletes the
    superseded image record the moment the tag moves, even while a container
    still runs it: ``docker image inspect <old id>`` and ``docker tag <old id>
    …`` both answer "No such image" (measured 2026-09-28). An image id recorded
    here and restored after the build therefore names nothing, which is how
    every rollback from 2026-09-13 to 09-28 would have failed, unnoticed
    because none fired. A tag taken BEFORE the build holds the record, and
    with it the content, through the build.

    The source is the image the container was created from, when that record
    still exists. Often it already does not: a rebuild that changed nothing
    mints a new image ID around the same platform manifest, compose rightly
    leaves the container on the old one, and that record is gone. The image
    ref then names the same content, and is tagged instead. Either way the tag
    is read back, and it is recorded only if it names exactly what the
    container runs. A container whose content no image names any more has no
    rollback image: that is reported, never papered over with a different one.

    Once the tag has moved, the image it held from the snapshot before is
    released (``_release``), so one rollback image per service is all that is
    ever kept.
    """
    target = rollback_ref_for(info["image_ref"], service)
    if not target:
        return "", f"{container}'s image ref ({info['image_ref'] or 'none'}) has no repository to tag under"
    running, platform = _content(info)
    if not running:
        return "", f"{container} records no image"
    if info["manifest_digest"] and not platform:
        return "", f"{container}'s manifest descriptor names no platform"
    previous, _ = _names(target, "", run)  # the image ID an earlier snapshot preserved, if any
    kept, note = _tag_running(container, info, target, running, platform, run)
    if kept and previous:
        _release(previous, run)
    return kept, note


def _tag_running(container: str, info: dict[str, Any], target: str, running: str, platform: str,
                 run: Runner) -> tuple[str, str]:
    """Point ``target`` at the image ``container`` runs: its own image, else a ref naming the same content."""
    gone = ""
    if info["image_id"]:
        rc, _, err = run(["docker", "tag", info["image_id"], target])
        if rc == 0:
            return _confirm(target, running, platform, "the image it was created from", run)
        gone = f"its image {_short(info['image_id'])} is gone ({_first_line(err)}); "
    tagged, err = _names(info["image_ref"], platform, run)
    if tagged != running:
        now = f"now names {_short(tagged)}" if tagged else f"cannot be read ({err})"
        return "", (f"{gone}{info['image_ref']} {now}, not the {_short(running)} {container} runs, "
                    "and no image holds that any more")
    rc, _, err = run(["docker", "tag", info["image_ref"], target])
    if rc != 0:
        return "", f"{gone}docker tag {info['image_ref']} {target} failed: {_first_line(err)}"
    return _confirm(target, running, platform, f"via {info['image_ref']}, which names the same content", run)


def snapshot(services: list[str], run: Runner = _run, project: str | None = None) -> dict[str, dict[str, str]]:
    """What each service runs before the rebuild, with that image preserved (``preserve``)."""
    out: dict[str, dict[str, str]] = {}
    for svc in services:
        container = find_container(svc, run, project)
        info = inspect(container, run) if container else None
        entry = {
            "container": container or "",
            "image_ref": str((info or {}).get("image_ref", "")),
            "image_id": str((info or {}).get("image_id", "")),
            "manifest_digest": str((info or {}).get("manifest_digest", "")),
            "platform": str((info or {}).get("platform", "")),
            "rollback_ref": "",
            "rollback_note": "no container",
        }
        if container and info is None:
            entry["rollback_note"] = f"docker inspect {container} failed"
        elif container and info is not None:
            entry["rollback_ref"], entry["rollback_note"] = preserve(svc, container, info, run)
            if not entry["rollback_ref"] and info["status"] != "running":
                # A parked service (voice) is never gated; a game-mode-parked
                # sidecar is, once compose-apply starts it.
                entry["rollback_note"] += f" ({container} is {info['status'] or 'not running'}; gated only if this pass starts it)"
        out[svc] = entry
    return out


def snapshot_notes(snap: dict[str, dict[str, str]]) -> list[str]:
    """One line per service for the deploy log: what a failed gate could roll back to."""
    lines = []
    for svc, entry in snap.items():
        if entry.get("rollback_ref"):
            lines.append(f"rollback image for {svc}: {entry['rollback_ref']} ({entry.get('rollback_note', '')})")
        elif not entry.get("container"):
            lines.append(f"nothing to preserve for {svc}: no container")
        else:
            lines.append(f"{NO_ROLLBACK_NOTE}{svc}: {entry.get('rollback_note') or 'not preserved'}; "
                         "a failed gate will page without rolling it back")
    return lines


RECREATE, SKIP, PARKED = "recreate", "skip", "parked"


def plan_recreate(service: str, *, since: float | None = None, run: Runner = _run,
                  project: str | None = None) -> tuple[str, str]:
    """Step 6a-bis: after compose-apply, does ``service`` still need a force-recreate?

    ``since`` is when compose-apply began (epoch seconds). Returns
    ``(action, reason)``:

    ``skip``      the container already runs the content its image ref names.
                  Almost always because compose-apply just recreated it — it
                  does that whenever a rebuilt image's content changed — or
                  because the rebuild changed nothing the image contains. A
                  force-recreate here was the second restart of every rebuilt
                  service (the brain started twice per brain deploy from
                  2026-09-22 to 09-28).
    ``recreate``  it runs different content (compose left it on the previous
                  image), or the comparison could not be made. Unknown is
                  recreated on purpose: a needless restart is cheaper than
                  stale code that looks healthy.
    ``parked``    no container at all, or one that is stopped and was not
                  started by this deploy — a service whose compose profile is
                  off (voice), since ``up -d`` creates and starts every service
                  in an active profile. Naming it in ``up --force-recreate``
                  enables its profile and starts it, which is how a Dockerfile
                  change would have un-parked voice. Nothing runs a stale image
                  here, and compose recreates it from the fresh one when the
                  profile comes back.
    """
    rc, out, err = run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.service={service}",
                        *NOT_ONE_OFF, *in_project(project), "--format", "{{.Names}}"])
    if rc != 0:
        return RECREATE, f"could not list its container ({(err.strip() or 'docker ps failed')[:120]}); recreating to be safe"
    names = [n.strip() for n in out.splitlines() if n.strip()]
    if not names:
        return PARKED, "no container (its compose profile is off); a named recreate would create and start it"
    if len(names) > 1:
        # Another compose project with the same service name (the caller did
        # not scope to the stack's project, see in_project), or a scaled
        # service. Judging the wrong container could skip a stale one, so don't pick.
        return RECREATE, f"{len(names)} containers carry this service's label ({', '.join(names[:3])}); recreating to be safe"
    container = names[0]
    info = inspect(container, run)
    if info is None:
        return RECREATE, f"could not inspect {container}; recreating to be safe"
    started = _epoch(info["started_at"])
    if info["status"] in ("exited", "dead") and since is not None and started is not None and started < since:
        return PARKED, (f"{container} is {info['status']} and this deploy did not start it (parked); "
                        "a named recreate would start it")
    ident = image_identity(container, run, info=info)
    if ident["same"] is True:
        created = _epoch(info["created"])
        if since is not None and created is not None and created >= since:
            return SKIP, f"compose-apply already recreated {container} onto the rebuilt image ({_short(ident['running'])})"
        return SKIP, (f"{container} already runs what {ident['image_ref']} names ({_short(ident['running'])}); "
                      "the rebuild changed nothing in the image")
    if ident["same"] is False:
        return RECREATE, (f"{container} runs {_short(ident['running'])} but {ident['image_ref']} now names "
                          f"{_short(ident['tagged'])}; compose-apply left it on the previous image")
    return RECREATE, f"could not compare images ({ident['why']}); recreating to be safe"


def recreate_plan(services: list[str], *, since: float | None = None, run: Runner = _run,
                  project: str | None = None) -> list[tuple[str, str, str]]:
    """``[(action, service, reason), …]`` in the order given — one line each for the shell."""
    plan: list[tuple[str, str, str]] = []
    for svc in services:
        action, reason = plan_recreate(svc, since=since, run=run, project=project)
        plan.append((action, svc, reason))
    return plan


# ---------------------------------------------------------------------------
# verdicts
# ---------------------------------------------------------------------------

def verdict(info: dict[str, Any] | None, *, running_for: float, settle: int) -> str:
    """``healthy`` | ``failed:<why>`` | ``pending``."""
    if info is None:
        return "pending"
    if info["restarting"]:
        return f"failed:restarting (exit {info.get('exit_code')})"
    if info["status"] in ("exited", "dead"):
        return f"failed:{info['status']} (exit {info.get('exit_code')})"
    if info["health"] == "unhealthy":
        return "failed:unhealthy"
    if info["restart_count"] >= 2:
        return f"failed:restarted {info['restart_count']}x since recreate"
    if info["has_healthcheck"]:
        return "healthy" if info["health"] == "healthy" else "pending"
    if info["status"] == "running" and running_for >= settle and info["restart_count"] == 0:
        return "healthy"
    return "pending"


def wait_for(
    service: str, *, timeout: int, settle: int, run: Runner = _run,
    clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
    project: str | None = None,
) -> tuple[str, str | None, dict[str, Any] | None]:
    """Poll one service until healthy/failed/timeout. Returns (verdict, container, last info)."""
    start = clock()
    first_running: float | None = None
    container: str | None = None
    info: dict[str, Any] | None = None
    while True:
        container = container or find_container(service, run, project)
        info = inspect(container, run) if container else None
        now = clock()
        if info and info["status"] == "running":
            first_running = first_running if first_running is not None else now
        else:
            first_running = None
        v = verdict(info, running_for=(now - first_running) if first_running is not None else 0.0, settle=settle)
        if v != "pending":
            return v, container, info
        if now - start >= timeout:
            return "timeout", container, info
        sleep(POLL_SECONDS)


def rollback(service: str, snap: dict[str, str], stack_cmd: list[str], run: Runner = _run,
             project: str | None = None) -> tuple[bool, str]:
    """Re-tag the preserved rollback image over the compose ref, recreate the service onto it, and prove it.

    The source is the tag the snapshot preserved (``rollback_ref``), never the
    bare image id: under the containerd store the rebuild deleted that record.
    Before anything is re-tagged, the preserved tag must still name what the
    snapshot saw running (an operator's ``docker image prune -a`` removes
    it). After the recreate, the new container must run exactly that content,
    or the rollback has not happened, whatever the commands returned.
    """
    image_ref, target = snap.get("image_ref", ""), snap.get("rollback_ref", "")
    held, platform = _content(snap)
    if not image_ref or not target or not held:
        why = snap.get("rollback_note") or "the snapshot recorded none"
        return False, f"no previous image was preserved before the rebuild ({why})"
    now, err = _names(target, platform, run)
    if now != held:
        state = f"now names {_short(now)}, not the preserved {_short(held)}" if now else f"is gone ({err})"
        return False, f"the rollback image {target} {state}"
    rc, _, err = run(["docker", "tag", target, image_ref])
    if rc != 0:
        return False, f"docker tag {target} {image_ref} failed: {_first_line(err)}"
    rc, _, err = run([*stack_cmd, "up", "-d", "--no-build", "--force-recreate", service])
    if rc != 0:
        return False, f"re-tagged {image_ref} from {target}, but the recreate failed: {_last_line(err)}"
    container = find_container(service, run, project)
    info = inspect(container, run) if container else None
    runs = _content(info)[0] if info else ""
    if runs != held:
        return False, (f"re-tagged {image_ref} from {target} and recreated, but {container or service} "
                       f"runs {_short(runs)}, not the preserved {_short(held)}")
    return True, f"re-tagged {image_ref} from {target} and recreated; {container} runs {_short(held)} again"


def restore_hint(service: str, snap: dict[str, str], stack_cmd: list[str]) -> str:
    """The commands that put ``service`` back on its preserved image by hand; "" without one."""
    target, image_ref = snap.get("rollback_ref", ""), snap.get("image_ref", "")
    if not target or not image_ref:
        return ""
    return (f" The image it ran before the rebuild was preserved as `{target}`; to put it back by hand: "
            f"`docker tag {target} {image_ref} && {' '.join(stack_cmd)} up -d --no-build --force-recreate {service}`.")


def write_alert(*, service: str, sha: str, severity: str, title: str, body: str, run: Runner = _run) -> None:
    labels = json.dumps({"probe": "deploy_health_gate", "service": service, "sha": sha})
    annotations = json.dumps({"summary": title, "description": body})
    _psql(
        "INSERT INTO alert_events (alertname, status, severity, category, labels, annotations, fingerprint) "
        "VALUES ('deploy_health_gate', 'firing', :'sev', 'infrastructure', :'labels'::jsonb, :'ann'::jsonb, :'fp')",
        run,
        sev=severity, labels=labels, ann=annotations, fp=f"deploy_health_gate:{service}:{sha}",
    )


def verify(
    services: list[str], snap: dict[str, dict[str, str]], *, sha: str, timeout: int, settle: int,
    do_rollback: bool, stack_cmd: list[str], run: Runner = _run,
    clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
    project: str | None = None,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for svc in services:
        v, container, info = wait_for(svc, timeout=timeout, settle=settle, run=run, clock=clock, sleep=sleep,
                                      project=project)
        entry: dict[str, Any] = {"verdict": v, "container": container or "", "rolled_back": False}
        if v == "healthy":
            results[svc] = entry
            continue
        tail = log_tail(container, run) if container else "(container not found)"
        hint = restore_hint(svc, snap.get(svc, {}), stack_cmd)
        if v.startswith("failed") and do_rollback and not svc.startswith(CONTAINER_PREFIX):
            ok, note = rollback(svc, snap.get(svc, {}), stack_cmd, run, project)
            entry["rolled_back"] = ok
            entry["rollback_note"] = note
            if ok:
                v2, _, _ = wait_for(svc, timeout=min(timeout, 120), settle=settle, run=run, clock=clock, sleep=sleep,
                                    project=project)
                entry["after_rollback"] = v2
            write_alert(
                service=svc, sha=sha, severity="critical",
                title=f"deploy {sha[:9]}: {svc} {v}; " + ("rolled back to the previous image" if ok else f"rollback FAILED ({note})"),
                body=(
                    f"The deploy sync rebuilt and recreated `{svc}` at {sha[:9]} and the new container failed its "
                    f"health gate ({v}). " + (f"Rolled back: {note}. The fix must merge as a new commit; this sha will not be "
                    f"rebuilt again for this service." if ok else f"Rollback failed: {note}. The service is DOWN.{hint}")
                    + f"\n\nLast {LOG_TAIL} log lines of the failed container:\n```\n{tail}\n```"
                ), run=run,
            )
        else:
            severity = "critical" if v.startswith("failed") else "warning"
            write_alert(
                service=svc, sha=sha, severity=severity,
                title=f"deploy {sha[:9]}: {svc} {v}" + ("" if v.startswith("failed") else " (no verdict within the gate window)"),
                body=(
                    f"`{svc}` did not come up healthy after the deploy sync at {sha[:9]}: {v}. "
                    + ("Rollback is disabled (deploy_rollback_on_unhealthy=false) or this is a bind-mount service; "
                       "the fix is a code revert or a pinned deploy clone." if v.startswith("failed")
                       else "It may still be starting; if the next cycle does not clear this, treat it as down.")
                    + hint
                    + f"\n\nLast {LOG_TAIL} log lines:\n```\n{tail}\n```"
                ), run=run,
            )
        results[svc] = entry
    return results


def exit_code(results: dict[str, Any]) -> int:
    if any(r.get("rolled_back") for r in results.values()):
        return 2
    if any(r["verdict"] != "healthy" for r in results.values()):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("--services", nargs="+", required=True)
    rp = sub.add_parser("recreate-plan")
    rp.add_argument("--services", nargs="+", required=True)
    rp.add_argument("--since", type=float, default=None, help="epoch seconds when compose-apply began")
    v = sub.add_parser("verify")
    v.add_argument("--services", nargs="+", required=True)
    v.add_argument("--snapshot", default="")
    v.add_argument("--sha", default="unknown")
    v.add_argument("--rollback", action="store_true")
    v.add_argument("--no-rollback", action="store_true")
    v.add_argument("--timeout", type=int, default=None)
    v.add_argument("--settle", type=int, default=None)
    v.add_argument("--stack-cmd", default="", help="command prefix that runs docker compose for the stack")
    for p in (s, rp, v):
        p.add_argument("--project", default="",
                       help="the stack's compose project; only its containers are considered (see in_project)")
    args = ap.parse_args(argv)
    project = args.project or None
    if args.cmd == "snapshot":
        snap = snapshot(args.services, project=project)
        print(json.dumps(snap))
        # stdout is the JSON the verify half reads; these go to the deploy log.
        for line in snapshot_notes(snap):
            print(" ".join(line.split()), file=sys.stderr)
        return 0
    if args.cmd == "recreate-plan":
        # Tab-separated so the shell can `read` it without a JSON parser; a
        # reason can never smuggle in a field or a line of its own.
        for action, svc, reason in recreate_plan(args.services, since=args.since, project=project):
            print(f"{action}\t{svc}\t{' '.join(reason.split())}")
        return 0
    snap: dict[str, dict[str, str]] = {}
    if args.snapshot and os.path.isfile(args.snapshot):
        with open(args.snapshot, encoding="utf-8") as fh:
            snap = json.load(fh)
    timeout = args.timeout if args.timeout is not None else read_int_setting("deploy_health_gate_seconds", DEFAULT_TIMEOUT)
    settle = args.settle if args.settle is not None else read_int_setting("deploy_health_gate_settle_seconds", DEFAULT_SETTLE)
    do_rollback = (not args.no_rollback) and (args.rollback or read_bool_setting("deploy_rollback_on_unhealthy", True))
    stack_cmd = args.stack_cmd.split() if args.stack_cmd else ["docker", "compose"]
    results = verify(args.services, snap, sha=args.sha, timeout=timeout, settle=settle,
                     do_rollback=do_rollback, stack_cmd=stack_cmd, project=project)
    print(json.dumps(results))
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
