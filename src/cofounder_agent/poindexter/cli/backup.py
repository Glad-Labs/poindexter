"""`poindexter backup` — off-machine (Tier 2) backup operator surface (#386).

Configures + drives restic against an S3-compatible bucket. The wizard
(`backup setup`) writes the repo URL (non-secret) to app_settings and the
restic password + S3 creds as ENCRYPTED app_settings secrets; start-stack.sh
materializes those into the backup-offsite container's env at boot. restic is
invoked via `docker run <offsite_backup_restic_image>` so no host install is
needed.
"""
from __future__ import annotations

import asyncio
import os
import secrets as _secrets
import shlex
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import click

# Pinned restic image for the wizard's `restic init` + first backup. Matches
# the alpine restic baked into scripts/Dockerfile.backup (the runner) so the
# repo is created, written, and checked by one version. Kept in sync with
# app_settings.offsite_backup_restic_image (settings_defaults.py).
_DEFAULT_RESTIC_IMAGE = "restic/restic:0.16.4"
_APPEND_ONLY_PROBE_KEY = ".poindexter-append-only-probe-nonexistent"

# The poindexter-backup image (scripts/Dockerfile.backup) carries BOTH pg_dump
# and restic, so the wizard's acceptance gate can stream a fresh dump exactly
# like the in-stack runner (scripts/backup-offsite/run.sh). Matches the image
# tag on the backup-* compose services.
_BACKUP_IMAGE = "poindexter-backup:latest"
# The postgres container the one-shot gate joins (compose container_name) so its
# pg_dump reaches postgres over the compose network, same as the runner.
_POSTGRES_CONTAINER = "poindexter-postgres-local"
_RUNNER_PG_HOST = "postgres-local"  # compose PG_HOST on the backup services
_RUNNER_PG_PORT = "5432"  # compose PG_PORT
# These MUST match the runner's snapshot identity so restic's (host, paths)
# parent selection treats the wizard's first snapshot as the runner's parent
# (else the runner's first tick finds "no parent" and re-ingests the full dump):
#   - restic --host           → offsite_backup_restic_host default (run.sh DEFAULT_RESTIC_HOST)
#   - restic --stdin-filename → the paths half of the key (run.sh hardcodes this)
#   - --tag <tier>            → offsite_backup_source_tier default (cosmetic; not in the key)
_RUNNER_RESTIC_HOST = "poindexter"
_RUNNER_STDIN_FILENAME = "poindexter_brain.dump"
_RUNNER_SOURCE_TIER = "daily"


# --- pure helpers (unit-tested without docker/DB) ---------------------------


def _strip_scheme(host: str) -> str:
    """Reduce a host/endpoint to a bare ``host[/path]`` (no scheme, no trailing /)."""
    host = host.strip().rstrip("/")
    if host.startswith("https://"):
        return host[len("https://"):]
    if host.startswith("http://"):
        return host[len("http://"):]
    return host


def build_repo_url(endpoint: str, bucket: str, path: str) -> str:
    """Assemble a restic S3 repo URL from parts.

    restic wants ``s3:https://<host>/<bucket>[/<path>]``. We normalize a
    bare host, a full ``https://host/`` and trailing slashes to the same shape.
    """
    parts = [_strip_scheme(endpoint), bucket.strip().strip("/")]
    p = path.strip().strip("/")
    if p:
        parts.append(p)
    return "s3:https://" + "/".join(parts)


def interpret_delete_probe(status_code: int) -> str:
    """Classify the append-only probe result.

    We attempt to DELETE a random object key that does not exist:
    - 401/403 (AccessDenied) ⇒ the key lacks delete ⇒ ``append_only`` (good).
    - 204/404 (deleted / NoSuchKey) ⇒ the DELETE was authorized ⇒
      ``delete_capable`` (warn — this key can destroy backup history).
    """
    if status_code in (401, 403):
        return "append_only"
    return "delete_capable"


def generate_restic_password() -> str:
    """High-entropy restic repository password."""
    return _secrets.token_urlsafe(32)


def _pg_creds_from_dsn(dsn: str) -> tuple[str, str, str]:
    """Extract ``(user, password, database)`` from a postgres DSN.

    Host/port are intentionally NOT taken from the DSN: the one-shot streaming
    gate reaches postgres over the compose network as ``postgres-local:5432``
    (the runner's PG_HOST/PG_PORT), never via the host-side address the DSN
    encodes. The password is URL-decoded so a percent-encoded DSN password is
    handed to PGPASSWORD verbatim. Defaults mirror the compose service vars
    (``poindexter`` / ``poindexter_brain``) for a bare host-only DSN.
    """
    parsed = urlparse(dsn)
    user = unquote(parsed.username) if parsed.username else "poindexter"
    password = unquote(parsed.password) if parsed.password else ""
    database = (parsed.path or "").lstrip("/") or "poindexter_brain"
    return user, password, database


def _streaming_backup_cmd(
    *,
    image: str,
    repo: str,
    network: str,
    pg_host: str,
    pg_port: str,
    pg_user: str,
    pg_database: str,
    restic_host: str,
    source_tier: str,
    stdin_filename: str,
    env: dict[str, str],
) -> list[str]:
    """Build the one-shot ``docker run … sh -c 'pg_dump -Z0 | restic --stdin'``.

    Mirrors ``scripts/backup-offsite/run.sh::run_backup`` exactly — the
    UNCOMPRESSED (``-Z0``) custom-format dump, the ``--stdin-filename`` /
    ``--host`` pair that fixes restic's ``(host, paths)`` parent key, and the
    tags — so the wizard's first snapshot is a valid parent for the runner's
    first tick (which then dedupes instead of re-ingesting the whole dump).

    Secrets ride in through ``-e KEY=VALUE`` (like the sibling ``_run_restic``),
    never interpolated into the shell string. ``set -o pipefail`` propagates a
    pg_dump failure through the pipe so a half-streamed dump fails the gate
    rather than saving a truncated snapshot (the runner relies on the same).
    """
    q = shlex.quote
    pipeline = (
        "set -eo pipefail; "
        f"pg_dump -h {q(pg_host)} -p {q(pg_port)} -U {q(pg_user)} -d {q(pg_database)} "
        "--format=custom -Z0 --no-owner --no-acl "
        f"| restic -r {q(repo)} backup --stdin "
        f"--stdin-filename {q(stdin_filename)} --host {q(restic_host)} "
        f"--tag poindexter --tag {q(source_tier)}"
    )
    cmd = ["docker", "run", "--rm", "--network", network]
    for key, value in env.items():
        cmd += ["-e", f"{key}={value}"]
    cmd += ["--entrypoint", "sh", image, "-c", pipeline]
    return cmd


@click.group(name="backup")
def backup_group() -> None:
    """Off-machine (Tier 2) backup — wizard, status, run, verify, snapshots."""


# --- DB seams (monkeypatched in tests) --------------------------------------


async def _connect(dsn: str) -> Any:
    import asyncpg

    return await asyncpg.connect(dsn, timeout=8)


async def _set_secret(conn: Any, key: str, value: str, description: str = "") -> None:
    """Write an ENCRYPTED app_settings row (pgcrypto, ``enc:v1:`` sentinel)."""
    from plugins.secrets import set_secret

    await set_secret(conn, key, value, description=description, category="backup")


async def _set_setting(conn: Any, key: str, value: str) -> None:
    """Upsert a NON-secret app_settings row (plaintext, e.g. the repo URL)."""
    await conn.execute(
        """
        INSERT INTO app_settings (key, value, category, is_secret, is_active)
        VALUES ($1, $2, 'backup', false, true)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        key,
        value,
    )


async def persist_config(
    *,
    dsn: str,
    repo_url: str,
    region: str,
    restic_password: str,
    access_key_id: str,
    secret_access_key: str,
) -> None:
    """Write the repo URL + region (plain) + the 3 secrets (encrypted) to app_settings.

    The repo URL and S3 region go through :func:`_set_setting` (plaintext); the
    restic password and S3 key pair go through :func:`_set_secret` (encrypted at
    rest). ``start-stack.sh`` later decrypts the three secrets into the
    backup-offsite container's env_file. Never route a secret through
    ``_set_setting`` or the repo URL through ``_set_secret`` — the split is
    the whole point of the DB-first-secret materialization.
    """
    conn = await _connect(dsn)
    try:
        await _set_setting(conn, "offsite_backup_repository", repo_url)
        await _set_setting(conn, "offsite_backup_s3_region", region)
        await _set_secret(
            conn,
            "offsite_backup_restic_password",
            restic_password,
            description="restic repo password for Tier 2 offsite backup (#386)",
        )
        await _set_secret(
            conn,
            "offsite_backup_s3_access_key_id",
            access_key_id,
            description="S3 access key id for Tier 2 offsite backup (#386)",
        )
        await _set_secret(
            conn,
            "offsite_backup_s3_secret_access_key",
            secret_access_key,
            description="S3 secret access key for Tier 2 offsite backup (#386)",
        )
    finally:
        await conn.close()


# --- restic / S3 adapters (exercised at the live test, not in unit tests) ----


def _run_restic(
    image: str,
    repo: str,
    args: list[str],
    *,
    env: dict[str, str],
    source_mount: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """``docker run --rm <image> -r <repo> <args>`` with creds passed as env.

    When ``source_mount`` is set, bind it read-only at /data so
    ``restic backup /data/<tier>`` can read the host dumps from inside the
    one-shot container. Mirrors how the in-stack runner invokes restic, so the
    wizard's repo is created by the same version that later writes to it.
    """
    cmd = ["docker", "run", "--rm"]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    if source_mount:
        cmd += ["-v", f"{source_mount}:/data:ro"]
    cmd += [image, "-r", repo, *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600)


def _docker_image_present(image: str) -> bool:
    """True when ``docker image inspect <image>`` succeeds (image built locally).

    Tier 1 (backup-hourly/daily) builds ``poindexter-backup:latest``, so a
    healthy stack always has it. Any failure (image absent, docker unreachable)
    returns False so the wizard degrades to the legacy ``restic/restic`` gate.
    """
    try:
        res = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:  # noqa: BLE001
        # silent-ok: image presence is advisory — docker unreachable/absent ⇒
        # fall back to the legacy restic/restic gate, never block the wizard.
        return False
    return res.returncode == 0


def _postgres_network(container: str = _POSTGRES_CONTAINER) -> str | None:
    """First docker network the postgres container is attached to, or None.

    The one-shot streaming gate joins this network so its pg_dump reaches
    ``postgres-local:5432`` exactly as the in-stack runner does. Returns None
    when the container isn't running (Tier 1 down) or docker is unreachable, so
    the caller falls back to the legacy gate.
    """
    try:
        res = subprocess.run(
            [
                "docker",
                "inspect",
                "-f",
                "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}",
                container,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:  # noqa: BLE001
        # silent-ok: network discovery is advisory — docker unreachable ⇒ fall
        # back to the legacy gate, never block the wizard.
        return None
    if res.returncode != 0:
        return None
    nets = res.stdout.split()
    return nets[0] if nets else None


def _derive_s3_region(endpoint: str) -> str:
    """Best-effort region for the boto3 client when an explicit endpoint is set.

    Backblaze B2:  ``s3.<region>.backblazeb2.com``  → ``<region>``
    AWS:           ``s3.<region>.amazonaws.com``     → ``<region>``
                   ``s3.amazonaws.com``              → ``us-east-1``
    Cloudflare R2: ``<acct>.r2.cloudflarestorage.com`` → ``auto``
    Anything else (MinIO, etc.)                       → ``us-east-1``
    """
    host = _strip_scheme(endpoint).lower()
    # Dot-anchored suffix check — a bare endswith("r2.cloudflarestorage.com")
    # would also match unrelated hosts like "evilr2.cloudflarestorage.com".
    if host == "r2.cloudflarestorage.com" or host.endswith(".r2.cloudflarestorage.com"):
        return "auto"
    parts = host.split(".")
    if len(parts) >= 3 and parts[0] == "s3" and parts[1] != "amazonaws":
        return parts[1]
    return "us-east-1"


def _probe_append_only(
    *, endpoint: str, bucket: str, access_key_id: str, secret_access_key: str
) -> str:
    """Probe whether the S3 key can DELETE objects (a real capability check).

    restic has no 'try-delete' verb, and ``restic unlock`` on a fresh repo
    finds no stale locks → exits 0 regardless of delete capability, so it
    can't tell an append-only key from a delete-capable one. Instead we issue
    a real S3 ``DeleteObject`` against a key that does not exist and read the
    HTTP status via :func:`interpret_delete_probe`:

    - 403 AccessDenied ⇒ the key lacks delete ⇒ ``append_only`` (good).
    - 204/200/404 (idempotent delete authorized) ⇒ ``delete_capable`` (warn).

    Requires boto3 (a declared dependency, used by r2_upload_service.py). Any
    failure (boto3 missing, network, endpoint quirk) degrades to the SAFE
    assumption ``append_only`` so the wizard never blocks on the probe — the
    runner is backup-only regardless, so this signal is advisory.
    """
    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        return "append_only"
    try:
        client = boto3.client(
            "s3",
            endpoint_url=f"https://{_strip_scheme(endpoint)}",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=_derive_s3_region(endpoint),
        )
        resp = client.delete_object(Bucket=bucket, Key=_APPEND_ONLY_PROBE_KEY)
        status = resp.get("ResponseMetadata", {}).get("HTTPStatusCode", 204)
        return interpret_delete_probe(int(status))
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 403)
        return interpret_delete_probe(int(status))
    except Exception:  # noqa: BLE001 — advisory probe, never block the wizard
        # silent-ok: the append-only check is advisory; on any error (network,
        # endpoint quirk, bad creds) we degrade to the SAFE assumption so the
        # wizard never blocks. The runner is backup-only regardless, and the
        # 403/204 ClientError path above is what gives the real signal.
        return "append_only"


def _host_backup_dir() -> str:
    """Host directory holding Tier 1's pg_dumps (``daily/`` lives under here)."""
    override = os.getenv("POINDEXTER_BACKUP_DIR")
    if override:
        return override
    return str(Path.home() / ".poindexter" / "backups" / "auto")


# --- the wizard -------------------------------------------------------------


@backup_group.command(name="setup")
def backup_setup() -> None:
    """Interactive wizard: configure restic against an S3-compatible bucket.

    Staged so nothing is persisted until a real first backup succeeds:
      1/4 append-only key check (advisory) → 2/4 ``restic init`` →
      3/4 first backup — a creds/repo/network acceptance gate that streams a
          fresh ``pg_dump -Z0 | restic --stdin`` matching the in-stack runner
          (so its first tick dedupes against this snapshot), falling back to a
          pinned-restic backup of an existing dump when the backup image or
          postgres network isn't available →
      4/4 encrypted persist + an OFFLINE-save banner for the restic password.
    """
    from brain import bootstrap

    from ._bootstrap import ensure_secret_key

    ensure_secret_key()

    dsn = bootstrap.resolve_database_url()
    if not dsn:
        raise click.ClickException("No database_url — run `poindexter setup` first.")

    click.secho("Poindexter off-machine backup setup (Tier 2)", fg="cyan", bold=True)
    click.echo(
        "Backblaze B2 (S3) / AWS S3 / Cloudflare R2 / MinIO — same restic S3 backend.\n"
    )

    endpoint = click.prompt(
        "S3 endpoint host (e.g. s3.us-west-002.backblazeb2.com)"
    ).strip()
    bucket = click.prompt("Bucket name").strip()
    path = click.prompt("Path within bucket", default="poindexter").strip()
    # SigV4 signs with the bucket's region; B2 (and AWS) reject the signature
    # when it's wrong, so we must pass AWS_DEFAULT_REGION to restic — not just
    # the creds. Default to the region derived from the endpoint host, but let
    # the operator correct it (our heuristic can't know every provider).
    region = click.prompt("S3 region", default=_derive_s3_region(endpoint)).strip()
    access_key_id = click.prompt("S3 access key id").strip()
    secret_access_key = click.prompt("S3 secret access key", hide_input=True).strip()

    repo_url = build_repo_url(endpoint, bucket, path)
    restic_password = generate_restic_password()
    # Mirror app_settings.offsite_backup_restic_image default (settings_defaults.py)
    # so the wizard inits + writes the repo with the same restic the runner uses.
    image = _DEFAULT_RESTIC_IMAGE
    s3_env = {
        "AWS_ACCESS_KEY_ID": access_key_id,
        "AWS_SECRET_ACCESS_KEY": secret_access_key,
        "AWS_DEFAULT_REGION": region,
        "RESTIC_PASSWORD": restic_password,
    }

    # 1/4 append-only guard ---------------------------------------------------
    click.secho("\n1/4 — checking key capability (append-only is recommended)…", fg="cyan")
    posture = _probe_append_only(
        endpoint=endpoint,
        bucket=bucket,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )
    if posture == "delete_capable":
        click.secho(
            "  WARNING: this key can DELETE objects. A ransomed host could "
            "destroy backup history. Prefer a B2 key without deleteFiles, or "
            "enable bucket Object Lock.",
            fg="yellow",
        )
        if not click.confirm("  Proceed with a delete-capable key anyway?", default=False):
            raise click.ClickException("Aborted — create an append-only key and re-run.")
    else:
        click.secho("  OK — key appears append-only (cannot delete).", fg="green")

    # 2/4 init ----------------------------------------------------------------
    click.secho("2/4 — initializing restic repo…", fg="cyan")
    init = _run_restic(image, repo_url, ["init"], env=s3_env)
    if init.returncode != 0 and "already initialized" not in (init.stderr or "").lower():
        raise click.ClickException(f"restic init failed:\n{init.stderr}")
    click.secho("  OK", fg="green")

    # 3/4 first backup (acceptance gate) -------------------------------------
    click.secho(
        "3/4 — running first backup (must succeed before we save config)…", fg="cyan"
    )
    backup_dir = _host_backup_dir()
    src_tier = _RUNNER_SOURCE_TIER
    daily_dir = Path(backup_dir) / src_tier
    if not daily_dir.is_dir() or not any(daily_dir.iterdir()):
        raise click.ClickException(
            f"No Tier 1 daily dumps found at {daily_dir}. This setup step backs one "
            "up to prove your bucket creds/repo/network work — ensure the in-stack "
            "backup has produced at least one daily dump first (check the backup "
            "container / `poindexter backup status`)."
        )
    # Acceptance gate: prove creds + repo + network end-to-end before we persist
    # config. PREFER the STREAMING gate so this first snapshot shares the in-stack
    # runner's (host, stdin-filename) parent key and the runner's first tick
    # dedupes against it instead of re-ingesting the full dump once (follow-up to
    # PR #2317). It runs a fresh `pg_dump -Z0 | restic backup --stdin` through
    # poindexter-backup:latest (which has pg_dump) on postgres's compose network.
    # Tier 1 being up (the daily-dump precondition above) implies that image is
    # built and postgres is reachable — but if either is unavailable we fall back
    # to the legacy `restic/restic` gate on an existing daily dump, which still
    # proves creds/repo/network (at the one-time cost of one runner re-ingest).
    network = _postgres_network() if _docker_image_present(_BACKUP_IMAGE) else None
    if network:
        click.echo(
            "  streaming a fresh pg_dump into restic (matches the in-stack runner)…"
        )
        pg_user, pg_password, pg_database = _pg_creds_from_dsn(dsn)
        cmd = _streaming_backup_cmd(
            image=_BACKUP_IMAGE,
            repo=repo_url,
            network=network,
            pg_host=_RUNNER_PG_HOST,
            pg_port=_RUNNER_PG_PORT,
            pg_user=pg_user,
            pg_database=pg_database,
            restic_host=_RUNNER_RESTIC_HOST,
            source_tier=src_tier,
            stdin_filename=_RUNNER_STDIN_FILENAME,
            env={**s3_env, "PGPASSWORD": pg_password},
        )
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if res.returncode != 0:
            raise click.ClickException(
                f"First backup failed — nothing saved as configured.\n{res.stderr}"
            )
        click.secho(
            "  OK — first snapshot created (streamed; the runner dedupes against it).",
            fg="green",
        )
    else:
        # Fallback: poindexter-backup:latest isn't built or its network isn't
        # discoverable (Tier 1 not up). Prove creds/repo/network with the pinned
        # restic image against an existing Tier 1 dump. This snapshot's path
        # (/data/daily) differs from the runner's (poindexter_brain.dump), so the
        # runner's first tick re-ingests once — a one-time, fresh-install cost.
        click.echo(
            "  poindexter-backup image/network unavailable — proving creds with the "
            "pinned restic image on an existing daily dump."
        )
        first = _run_restic(
            image,
            repo_url,
            ["backup", f"/data/{src_tier}", "--tag", "poindexter"],
            env=s3_env,
            source_mount=backup_dir,
        )
        if first.returncode != 0:
            raise click.ClickException(
                f"First backup failed — nothing saved as configured.\n{first.stderr}"
            )
        click.secho("  OK — first snapshot created.", fg="green")

    # 4/4 persist + save-offline banner --------------------------------------
    click.secho("4/4 — saving config…", fg="cyan")
    asyncio.run(
        persist_config(
            dsn=dsn,
            repo_url=repo_url,
            region=region,
            restic_password=restic_password,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )
    )
    click.secho("  OK — repo URL + encrypted creds written to app_settings.", fg="green")
    click.echo()
    click.secho("=" * 70, fg="yellow")
    click.secho("SAVE THIS RESTIC PASSWORD NOW — OFFLINE:", fg="yellow", bold=True)
    click.secho(f"    {restic_password}", fg="yellow", bold=True)
    click.secho(
        "In a drive-failure / theft / ransomware event the DB and this machine\n"
        "are gone. Without this password the remote backup is UNRECOVERABLE.",
        fg="yellow",
    )
    click.secho("=" * 70, fg="yellow")
    click.echo(
        "\nRestart the stack (or `docker compose up -d backup-offsite`) to activate."
    )


# --- status / run / verify / snapshots --------------------------------------


def _fmt_age(seconds: float | None) -> str:
    """Human age: ``never`` / ``N.Nh ago`` (< 48h) / ``N.Nd ago``."""
    if seconds is None:
        return "never"
    h = float(seconds) / 3600.0
    if h < 48:
        return f"{h:.1f}h ago"
    return f"{h / 24:.1f}d ago"


def _format_status(
    *, repo: str, last_success_age_s: float | None, last_verify_age_s: float | None
) -> str:
    """Render `backup status` output (pure — unit-tested)."""
    if not repo:
        return "Offsite backup: not configured. Run `poindexter backup setup`."
    return (
        f"Offsite backup repo: {repo}\n"
        f"  last backup:  {_fmt_age(last_success_age_s)}\n"
        f"  last verify:  {_fmt_age(last_verify_age_s)}"
    )


async def _age_of_event(dsn: str, event: str) -> float | None:
    """Seconds since the newest ``audit_log`` row of ``event_type=event`` (or None)."""
    conn = await _connect(dsn)
    try:
        val = await conn.fetchval(
            'SELECT EXTRACT(EPOCH FROM (now() - MAX("timestamp")))'
            " FROM audit_log WHERE event_type = $1",
            event,
        )
        return float(val) if val is not None else None
    finally:
        await conn.close()


async def _get_setting(dsn: str, key: str) -> str:
    conn = await _connect(dsn)
    try:
        return (
            await conn.fetchval("SELECT value FROM app_settings WHERE key = $1", key)
        ) or ""
    finally:
        await conn.close()


@backup_group.command(name="status")
def backup_status() -> None:
    """Show repo + last backup/verify ages (reads the audit_log heartbeat)."""
    from brain import bootstrap

    dsn = bootstrap.resolve_database_url()
    if not dsn:
        raise click.ClickException("No database_url — run `poindexter setup` first.")
    repo = asyncio.run(_get_setting(dsn, "offsite_backup_repository"))
    succ = asyncio.run(_age_of_event(dsn, "offsite_backup_succeeded"))
    ver = asyncio.run(_age_of_event(dsn, "offsite_backup_verified"))
    click.echo(
        _format_status(repo=repo, last_success_age_s=succ, last_verify_age_s=ver)
    )


def _resolved_secret_env(dsn: str) -> dict[str, str]:
    """Decrypt the 3 secrets for an ad-hoc ``docker run restic`` (run/verify/snapshots)."""
    from plugins.secrets import get_secret

    async def _go() -> dict[str, str]:
        conn = await _connect(dsn)
        try:
            return {
                "RESTIC_PASSWORD": await get_secret(
                    conn, "offsite_backup_restic_password"
                )
                or "",
                "AWS_ACCESS_KEY_ID": await get_secret(
                    conn, "offsite_backup_s3_access_key_id"
                )
                or "",
                "AWS_SECRET_ACCESS_KEY": await get_secret(
                    conn, "offsite_backup_s3_secret_access_key"
                )
                or "",
            }
        finally:
            await conn.close()

    return asyncio.run(_go())


def _run_or_die(
    dsn: str, restic_args: list[str], *, source_mount: str | None = None
) -> str:
    """Resolve config + decrypt creds, then ``docker run restic`` or raise."""
    from ._bootstrap import ensure_secret_key

    ensure_secret_key()
    repo = asyncio.run(_get_setting(dsn, "offsite_backup_repository"))
    if not repo:
        raise click.ClickException("Not configured — run `poindexter backup setup`.")
    image = (
        asyncio.run(_get_setting(dsn, "offsite_backup_restic_image"))
        or _DEFAULT_RESTIC_IMAGE
    )
    env = _resolved_secret_env(dsn)
    if not env["RESTIC_PASSWORD"]:
        raise click.ClickException(
            "restic password unset — re-run `poindexter backup setup`."
        )
    # SigV4 region — without it restic signs with us-east-1 and a non-us-east-1
    # bucket (e.g. B2 us-east-005) rejects the signature. Skip when unset so
    # restic keeps its own default for legacy configs.
    region = asyncio.run(_get_setting(dsn, "offsite_backup_s3_region"))
    if region:
        env["AWS_DEFAULT_REGION"] = region
    res = _run_restic(image, repo, restic_args, env=env, source_mount=source_mount)
    if res.returncode != 0:
        raise click.ClickException(f"restic failed:\n{res.stderr}")
    return res.stdout


@backup_group.command(name="run")
def backup_run() -> None:
    """Trigger an offsite backup now (manual; the in-stack runner does this on cron)."""
    from brain import bootstrap

    dsn = bootstrap.resolve_database_url()
    if not dsn:
        raise click.ClickException("No database_url — run `poindexter setup` first.")
    tier = asyncio.run(_get_setting(dsn, "offsite_backup_source_tier")) or "daily"
    out = _run_or_die(
        dsn,
        ["backup", f"/data/{tier}", "--tag", "poindexter"],
        source_mount=_host_backup_dir(),
    )
    click.echo(out)
    click.secho("Backup complete.", fg="green")


@backup_group.command(name="verify")
def backup_verify() -> None:
    """Run ``restic check --read-data-subset`` against the remote now (bit-rot scan)."""
    from brain import bootstrap

    dsn = bootstrap.resolve_database_url()
    if not dsn:
        raise click.ClickException("No database_url — run `poindexter setup` first.")
    pct = (
        asyncio.run(
            _get_setting(dsn, "offsite_backup_verify_read_data_subset_percent")
        )
        or "5"
    )
    out = _run_or_die(dsn, ["check", f"--read-data-subset={pct}%"])
    click.echo(out)
    click.secho("Verify complete.", fg="green")


@backup_group.command(name="snapshots")
def backup_snapshots() -> None:
    """List remote snapshots."""
    from brain import bootstrap

    dsn = bootstrap.resolve_database_url()
    if not dsn:
        raise click.ClickException("No database_url — run `poindexter setup` first.")
    click.echo(_run_or_die(dsn, ["snapshots"]))


# --- recovery drill ---------------------------------------------------------
#
# Glad-Labs/poindexter#889. Every other command in this file resolves its
# credentials THROUGH THE DATABASE (`_run_or_die` → `_resolved_secret_env` →
# `get_secret`), because on a healthy machine that is the convenient path. That
# convenience hides a circular dependency: the restic password and S3 keys live
# as encrypted `app_settings` rows *inside the database the backup contains*,
# and the key that decrypts them (`poindexter_secret_key`) lives only in
# `~/.poindexter/bootstrap.toml`, which the runner never backs up. After a total
# loss the operator holds a verified, current, retained repository that nobody
# can open — and every monitoring signal stayed green the whole time, because
# `run` / `verify` / `snapshots` only ever prove the repo is *writable and
# intact*, never that it is *reachable without this machine*.
#
# So this command's defining property is a NEGATIVE one: it does not read the
# database, at all. No `bootstrap.resolve_database_url()`, no `_get_setting`,
# no `get_secret`. Credentials come from the operator's own off-machine copy.
# That is the whole test — if it passes, the recovery path is real; if the
# operator cannot supply the values, they have just discovered their backup is
# unrecoverable while they still have a working machine to fix it from.


@backup_group.command(name="verify-recovery")
@click.option(
    "--repository", envvar="POINDEXTER_RECOVERY_REPOSITORY", required=True,
    help="restic repository URL, e.g. s3:https://s3.us-east-005.backblazeb2.com/my-bucket",
)
@click.option(
    "--restic-password", envvar="RESTIC_PASSWORD", prompt="restic password",
    hide_input=True, help="Reads $RESTIC_PASSWORD; prompts if unset.",
)
@click.option(
    "--s3-access-key-id", envvar="AWS_ACCESS_KEY_ID", prompt="S3 access key id",
    hide_input=True, help="Reads $AWS_ACCESS_KEY_ID; prompts if unset.",
)
@click.option(
    "--s3-secret-access-key", envvar="AWS_SECRET_ACCESS_KEY",
    prompt="S3 secret access key", hide_input=True,
    help="Reads $AWS_SECRET_ACCESS_KEY; prompts if unset.",
)
@click.option(
    "--region", envvar="AWS_DEFAULT_REGION", default="",
    help="SigV4 region. Required by non-us-east-1 buckets (e.g. B2 us-east-005).",
)
@click.option(
    "--image", default=_DEFAULT_RESTIC_IMAGE, show_default=True,
    help="restic docker image to run the drill with.",
)
def backup_verify_recovery(
    repository: str,
    restic_password: str,
    s3_access_key_id: str,
    s3_secret_access_key: str,
    region: str,
    image: str,
) -> None:
    """Prove the offsite repo opens with OFF-MACHINE credentials (recovery drill).

    Simulates total loss: reads NOTHING from this install — not the database,
    not bootstrap.toml, not app_settings. You supply the credentials you keep
    off-machine, and this lists the snapshots they can actually open.

    A backup is only proven by a recovery drill performed WITHOUT access to the
    source machine. `backup verify` proves the repo is intact; only this proves
    you can get back in.

    Credentials are read from the environment when set, so the normal shape is:

        export RESTIC_PASSWORD=... AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...
        poindexter backup verify-recovery --repository s3:https://...

    Any option left unset is prompted for with hidden input, which keeps it out
    of shell history. Run it from a DIFFERENT machine for a true drill — running
    it here still proves the credentials are correct and complete, which is the
    part operators actually get wrong.
    """
    env = {
        "RESTIC_PASSWORD": restic_password,
        "AWS_ACCESS_KEY_ID": s3_access_key_id,
        "AWS_SECRET_ACCESS_KEY": s3_secret_access_key,
    }
    if region:
        env["AWS_DEFAULT_REGION"] = region

    click.echo(f"Opening {repository} with supplied credentials…")
    res = _run_restic(image, repository, ["snapshots", "--json"], env=env)
    if res.returncode != 0:
        # Fail loud with restic's own stderr — "wrong password" vs "bucket not
        # found" vs "access denied" are three different recovery problems and
        # the operator needs to know which one they have.
        raise click.ClickException(
            "RECOVERY DRILL FAILED — these credentials do NOT open the "
            f"repository.\n\nrestic said:\n{res.stderr.strip()}\n\n"
            "This is the failure that would have surfaced during a real "
            "restore. Fix it now, then re-run this command."
        )

    count = _snapshot_count(res.stdout)
    click.secho("RECOVERY DRILL PASSED", fg="green", bold=True)
    if count is None:
        # Opened fine but the JSON didn't parse — still a pass (restic
        # authenticated), just don't fabricate a number.
        click.echo("  repository opened; snapshot list returned (unparsed)")
    else:
        click.echo(f"  repository opened; {count} snapshot(s) readable")
        if count == 0:
            click.secho(
                "  WARNING: the repo opens but holds ZERO snapshots — "
                "credentials are right, backups are not landing.",
                fg="yellow",
            )
    click.echo(
        "\nThese credentials are sufficient to recover. Keep them somewhere "
        "that survives losing this machine — a password manager or printed "
        "copy, NOT only in app_settings (which lives inside the backup)."
    )


def _snapshot_count(stdout: str) -> int | None:
    """Count snapshots in ``restic snapshots --json`` output.

    Returns ``None`` when the payload isn't the expected JSON list — the drill
    still passed (restic authenticated and listed), so this must not turn a
    pass into a failure; it just declines to report a number it doesn't have.
    """
    import json

    try:
        parsed = json.loads(stdout or "[]")
    except ValueError:
        return None
    return len(parsed) if isinstance(parsed, list) else None
