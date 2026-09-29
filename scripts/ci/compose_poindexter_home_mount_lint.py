#!/usr/bin/env python3
"""Fail a compose bind mount that hands a container more of ~/.poindexter than it declares.

Why
===

``~/.poindexter`` is not a data directory. It holds ``bootstrap.toml`` (the
master key: ``poindexter_secret_key``, the database passwords, the OAuth
signing key), and on an operator host it also holds things the HOST executes
as the operator user: the deploy clone (``deploy/``, whose ``start-stack.sh``,
health gate and identity check the deploy driver runs on every pass), the
deploy-sync launcher and its last-known-good driver (``deploy-sync/``), the
host CLI's venv (``cli-venv/``), and the dr-backup and recovery scripts
(``scripts/``). A container that can write any of those can run code on the
host.

Until glad-labs-stack#4186 (2026-09-28), ``worker``, ``pipeline-bot`` and
``prefect-worker`` (the containers that run LLM-driven pipeline code) mounted
the WHOLE directory read-write at ``/root/.poindexter``. The mount was a
Windows-era leftover: no code read it, and on Linux only the host dir's 0700
mode kept uid 1001 out. A root shell in the container, a ``chmod``, or a
Docker Desktop host (which never enforced that mode) would have exposed all
of it.

What this checks
================

Every bind mount of every service in every ``docker-compose*.yml`` at the repo
root. The source is interpolated as compose would on a Linux, macOS and
Windows host (``HOME`` / ``USERPROFILE`` set, every other variable unset, so
``${VAR:-default}`` takes its default: the lint judges what a fresh install
mounts, not one operator's overrides), then normalized. A source fails when
it is:

- ``whole``: ``~/.poindexter`` itself, or an ancestor of it (``~``, ``/home``,
  ``/``), in any spelling (``${USERPROFILE:-${HOME}}``, ``${USERPROFILE:-.}``,
  ``~``, a literal path, a default hidden inside another variable).
- ``protected``: under an entry in ``PROTECTED_ENTRIES``, the master key and
  what the host executes.
- ``undeclared``: under any other entry that ``ALLOWED_ENTRIES`` doesn't list.
  Default-deny, so a new media directory needs a line there saying what the
  container does with it. That line is the review point.
- ``escapes-project``: a relative source that climbs out of the project dir
  with ``..``. On the operator host the project dir IS the deploy clone,
  inside ``~/.poindexter``, so ``../..`` is the master key's directory.

``EXEMPT`` lists the deliberate exceptions, one per (service, container path),
each pinned to the one finding it covers and carrying its reason. The
whole-dir ones must stay read-only.

Out of scope, on purpose: plain relative and ``${POINDEXTER_DEPLOY_ROOT:-.}``
sources (the deployed code tree; ``compose_mount_deploy_root_lint.py`` governs
those), named volumes (including one whose ``driver_opts`` binds a host dir),
and the Docker socket, which is root on the host whatever this lint says.
"""

import posixpath
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import require_scanned  # noqa: E402

LINT = "compose_poindexter_home_mount_lint"
COMPOSE_GLOBS = ("docker-compose*.yml", "docker-compose*.yaml", "compose*.yml", "compose*.yaml")

# Top-level entries of ~/.poindexter a container may mount, and what for.
ALLOWED_ENTRIES = {
    "podcast": "rendered episodes (worker + prefect-worker write, uploaders read)",
    "video": "rendered videos (worker + prefect-worker write, uploaders read)",
    "generated-images": "image-gen server output the pipeline picks up",
    "generated-videos": "video-model output the pipeline picks up",
    "demo-clips": "VHS CLI footage: baked by worker/demo-recorder, read by the renderer",
    "singer-venv": "GA4/GSC singer tap venvs the worker's tap runner executes",
    "backups": "pg_dumps (DbBackupJob, backup-hourly/daily) + the brain's age check",
    "logs": "dr-backup failure sentinels the brain scans (read-only)",
    "comfyui": "ComfyUI model weights (read-only)",
    "tts-voices": "voice reference clips for the TTS sidecar (read-only)",
    "alertmanager-webhook-token": "alertmanager's webhook bearer token (read-only)",
}

# The master key and code the HOST executes. Never mounted without an EXEMPT
# entry naming the service, the container path and the verified consumer.
# Each name also covers its dotted suffixes (bootstrap.toml.bak-*).
PROTECTED_ENTRIES = ("bootstrap.toml", "deploy", "deploy-sync", "cli-venv", "scripts", "worktrees")


@dataclass(frozen=True)
class Exemption:
    kind: str  # "whole" | "protected" | "undeclared" | "escapes-project"
    entry: str | None  # the ~/.poindexter entry it covers; None for "whole"
    read_only: bool  # the mount must carry :ro / read_only: true
    reason: str


EXEMPT: dict[tuple[str, str], Exemption] = {
    ("backup-offsite", "/config/poindexter"): Exemption(
        kind="whole",
        entry=None,
        read_only=True,
        reason="offsite config snapshot (poindexter#889): bootstrap.toml is exactly "
        "what it backs up. Runs restic as the host uid, not pipeline code.",
    ),
    ("cadvisor", "/rootfs"): Exemption(
        kind="whole",
        entry=None,
        read_only=True,
        reason="cAdvisor's standard host-filesystem mount, for disk usage metrics",
    ),
    ("brain-daemon", "/host-deploy"): Exemption(
        kind="protected",
        entry="deploy",
        read_only=False,
        reason="migration-drift self-heal resets the deploy clone (poindexter#228). "
        "The brain also holds the Docker socket, which is root on the host anyway.",
    ),
}

# Interpolation environments: the host kinds a compose file is run on.
HOST_ENVS: dict[str, dict[str, str]] = {
    "linux": {"HOME": "/home/operator"},
    "macos": {"HOME": "/Users/operator"},
    "windows": {"USERPROFILE": "/c/Users/operator", "HOME": "/c/Users/operator"},
}


class ComposeScanError(ValueError):
    """The scanner met compose it cannot read. Fail loud, never skip."""


@dataclass(frozen=True)
class Mount:
    file: str
    line: int
    service: str
    source: str  # as written, before interpolation
    target: str
    read_only: bool


@dataclass(frozen=True)
class Finding:
    mount: Mount
    kind: str
    entry: str | None
    detail: str


# ---------------------------------------------------------------------------
# Compose scanning: a stdlib line scanner, no PyYAML, same posture as
# ports_lint.py. tests/unit/scripts/test_compose_poindexter_home_mount_lint.py
# holds it to PyYAML on every real compose file.
# ---------------------------------------------------------------------------

_KEY = re.compile(r"^(?P<key>[A-Za-z0-9_.-]+):(?:\s+(?P<value>.*))?$")


def _strip_comment(text: str) -> str:
    """Drop a trailing YAML comment (``#`` after whitespace, outside quotes).

    A quote opens a quoted scalar only where a scalar can start (line start,
    after whitespace or a flow delimiter), so the apostrophe in ``O'Brien``
    is a character, not a quote.
    """
    quote = None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'" and (i == 0 or text[i - 1] in " \t[{,"):
            quote = ch
        elif ch == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip()
    return text.rstrip()


def _scalar(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def split_top_level_colons(spec: str) -> list[str]:
    """Split ``src:dst:mode`` on colons outside ``${...}``.

    A leading drive letter stays with its path, as compose reads it:
    ``C:\\Users\\x\\.poindexter:/data`` is two fields, not three.
    """
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in spec:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    drive = len(parts) > 2 and re.fullmatch(r"[A-Za-z]", parts[0])
    if drive and parts[1][:1] in ("\\", "/") and parts[2][:1] == "/":
        parts[:2] = [f"{parts[0]}:{parts[1]}"]
    return parts


def mount_from_short(spec: str, *, file: str, line: int, service: str) -> Mount | None:
    """``src:dst[:mode]``; None for an anonymous volume (a container path only)."""
    parts = split_top_level_colons(spec)
    if len(parts) < 2:
        return None
    mode = parts[2] if len(parts) > 2 else ""
    return Mount(file, line, service, parts[0], parts[1], "ro" in mode.split(","))


def mount_from_long(fields: dict[str, str], *, file: str, line: int, service: str) -> Mount | None:
    """A long-syntax entry; None unless ``type: bind``."""
    if str(fields.get("type", "")).strip() != "bind":
        return None
    if "source" not in fields or "target" not in fields:
        raise ComposeScanError(f"{file}:{line}: long-syntax bind without source/target")
    read_only = str(fields.get("read_only", "false")).strip().lower() in {"true", "yes", "on"}
    return Mount(file, line, service, str(fields["source"]), str(fields["target"]), read_only)


def scan_mounts(text: str, *, file: str) -> list[Mount]:
    """Every entry under ``services.<name>.volumes`` that names a source."""
    lines = text.splitlines()
    mounts: list[Mount] = []
    in_services = False
    service_indent: int | None = None
    service: str | None = None
    body_indent: int | None = None
    i = 0
    while i < len(lines):
        content = _strip_comment(lines[i])
        if not content.strip():
            i += 1
            continue
        indent = _indent(content)
        key = _KEY.match(content.strip())
        if indent == 0:
            in_services = bool(key and key.group("key") == "services")
            service_indent = service = body_indent = None
            i += 1
            continue
        if not in_services:
            i += 1
            continue
        if service_indent is None:
            service_indent = indent
        if indent <= service_indent:
            service = key.group("key") if key and indent == service_indent else None
            body_indent = None
            i += 1
            continue
        if service is None:
            i += 1
            continue
        if body_indent is None:
            body_indent = indent
        if not (indent == body_indent and key and key.group("key") == "volumes"):
            i += 1
            continue
        if key.group("value"):
            raise ComposeScanError(
                f"{file}:{i + 1}: {service}.volumes is not a block list "
                f"({key.group('value')!r}); this lint reads only '- entry' lists"
            )
        i += 1
        item_indent: int | None = None
        while i < len(lines):
            content = _strip_comment(lines[i])
            if not content.strip():
                i += 1
                continue
            indent = _indent(content)
            stripped = content.strip()
            if indent < body_indent or (indent == body_indent and not stripped.startswith("-")):
                break
            if item_indent is None:
                item_indent = indent
            if indent != item_indent or not stripped.startswith("-"):
                raise ComposeScanError(f"{file}:{i + 1}: unexpected line in {service}.volumes")
            item = stripped[1:].strip()
            start = i + 1
            first = _KEY.match(item)
            if first:  # long syntax: this key plus the deeper ones below it
                fields = {first.group("key"): _scalar(first.group("value") or "")}
                i += 1
                while i < len(lines):
                    sub = _strip_comment(lines[i])
                    if not sub.strip():
                        i += 1
                        continue
                    if _indent(sub) <= item_indent:
                        break
                    field = _KEY.match(sub.strip())
                    if field and field.group("value") is not None:
                        fields[field.group("key")] = _scalar(field.group("value"))
                    i += 1
                mount = mount_from_long(fields, file=file, line=start, service=service)
            else:
                mount = mount_from_short(_scalar(item), file=file, line=start, service=service)
                i += 1
            if mount is not None:
                mounts.append(mount)
    return mounts


# ---------------------------------------------------------------------------
# Interpolation (compose semantics) and classification
# ---------------------------------------------------------------------------

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def interpolate(text: str, env: dict[str, str]) -> str:
    """Expand ``$VAR``, ``${VAR}``, ``${VAR:-x}``, ``${VAR-x}``, ``:?``, ``:+``, ``$$``."""
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch != "$":
            out.append(ch)
            i += 1
        elif text.startswith("$$", i):
            out.append("$")
            i += 2
        elif text.startswith("${", i):
            depth, j = 0, i + 1
            while j < len(text):
                depth += {"{": 1, "}": -1}.get(text[j], 0)
                if depth == 0:
                    break
                j += 1
            else:
                raise ComposeScanError(f"unbalanced ${{ in {text!r}")
            out.append(_expand(text[i + 2 : j], env))
            i = j + 1
        else:
            name = _NAME.match(text, i + 1)
            out.append(env.get(name.group(0), "") if name else "$")
            i = name.end() if name else i + 1
    return "".join(out)


def _expand(body: str, env: dict[str, str]) -> str:
    name = _NAME.match(body)
    if not name:
        raise ComposeScanError(f"bad interpolation ${{{body}}}")
    value, rest = env.get(name.group(0)), body[name.end() :]
    if not rest:
        return value or ""
    op = next((o for o in (":-", ":?", ":+", "-", "?", "+") if rest.startswith(o)), None)
    if op is None:
        raise ComposeScanError(f"bad interpolation ${{{body}}}")
    arg = rest[len(op) :]
    missing = value is None or (op.startswith(":") and value == "")
    if op.endswith("-"):
        return interpolate(arg, env) if missing else value
    if op.endswith("+"):
        return "" if missing else interpolate(arg, env)
    return "" if missing else value  # :? / ? -- compose refuses to start when unset


def _protected(entry: str) -> bool:
    return any(entry == p or entry.startswith(p + ".") for p in PROTECTED_ENTRIES)


def classify(source: str, env: dict[str, str]) -> tuple[str, str | None] | None:
    """``(kind, entry)`` when *source* reaches ~/.poindexter on this host, else None."""
    resolved = interpolate(source, env).replace("\\", "/")
    home = env.get("USERPROFILE") or env["HOME"]
    if resolved == "~" or resolved.startswith("~/"):
        resolved = home + resolved[1:]
    path = PurePosixPath(posixpath.normpath(resolved))
    if ".poindexter" in path.parts:
        rest = path.parts[path.parts.index(".poindexter") + 1 :]
        if not rest:
            return ("whole", None)
        if _protected(rest[0]):
            return ("protected", rest[0])
        return None if rest[0] in ALLOWED_ENTRIES else ("undeclared", rest[0])
    if not path.is_absolute():
        return ("escapes-project", None) if path.parts[:1] == ("..",) else None
    if (PurePosixPath(home) / ".poindexter").is_relative_to(path):
        return ("whole", None)
    return None


_DETAIL = {
    "whole": "exposes the WHOLE ~/.poindexter (bootstrap.toml, the deploy clone, "
    "everything the host executes)",
    "protected": "mounts ~/.poindexter/{entry}: the master key or code the host executes",
    "undeclared": "mounts ~/.poindexter/{entry}, which ALLOWED_ENTRIES does not declare",
    "escapes-project": "climbs out of the project dir; on the operator host that "
    "dir is the deploy clone inside ~/.poindexter",
}


def check_mount(mount: Mount) -> list[Finding]:
    """Findings for one mount across every host environment, deduplicated."""
    found: dict[tuple[str, str | None], Finding] = {}
    for env in HOST_ENVS.values():
        verdict = classify(mount.source, env)
        if verdict is None or verdict in found:
            continue
        exemption = EXEMPT.get((mount.service, mount.target))
        if exemption and (exemption.kind, exemption.entry) == verdict:
            if mount.read_only or not exemption.read_only:
                continue
            detail = f"is exempt only when read-only; add :ro (exemption: {exemption.reason})"
        else:
            detail = _DETAIL[verdict[0]].format(entry=verdict[1])
        found[verdict] = Finding(mount, verdict[0], verdict[1], detail)
    return list(found.values())


def compose_files(root: Path) -> list[Path]:
    return sorted({p for pattern in COMPOSE_GLOBS for p in root.glob(pattern) if p.is_file()})


def main() -> int:
    clash = sorted(e for e in ALLOWED_ENTRIES if _protected(e))
    if clash:
        print(f"{LINT}: ALLOWED_ENTRIES lists protected entries {clash}; remove them")
        return 1
    root = Path(__file__).resolve().parents[2]
    files = compose_files(root)
    require_scanned(len(files), lint=LINT, what="compose files", roots=(root,))
    mounts: list[Mount] = []
    for path in files:
        try:
            mounts += scan_mounts(path.read_text(encoding="utf-8"), file=path.name)
        except ComposeScanError as exc:
            print(f"{LINT}: cannot read {path.name}: {exc}")
            return 1
    require_scanned(len(mounts), lint=LINT, what="volume entries", roots=tuple(files))
    findings = [f for m in mounts for f in check_mount(m)]

    if findings:
        print(f"{LINT}: FAIL — {len(findings)} mount(s) expose ~/.poindexter\n")
        for f in findings:
            m = f.mount
            mode = "ro" if m.read_only else "rw"
            print(f"  {m.file}:{m.line}  {m.service}: {m.source} -> {m.target} ({mode})")
            print(f"      {f.kind}: {f.detail}")
        print(
            "\nMount the one subdirectory the container uses, at its appuser home\n"
            "(/home/appuser/.poindexter/<entry>), read-only unless it writes there.\n"
            "A new media dir goes in ALLOWED_ENTRIES in this script, with a reason.\n"
            "A genuine exception goes in EXEMPT, naming the service, container path\n"
            "and verified consumer. Never move a PROTECTED_ENTRIES name into\n"
            "ALLOWED_ENTRIES."
        )
        return 1

    print(f"{LINT}: OK ({len(mounts)} volume entries in {len(files)} compose file(s) checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
