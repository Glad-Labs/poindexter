"""Unit tests for poindexter/cli/backup.py (poindexter#386)."""
from __future__ import annotations

import asyncio
import subprocess

import pytest

from poindexter.cli import backup as bk


@pytest.mark.parametrize(
    "endpoint,bucket,path,expected",
    [
        (
            "s3.us-west-002.backblazeb2.com",
            "my-bucket",
            "poindexter",
            "s3:https://s3.us-west-002.backblazeb2.com/my-bucket/poindexter",
        ),
        (
            "https://s3.us-west-002.backblazeb2.com/",
            "b",
            "p",
            "s3:https://s3.us-west-002.backblazeb2.com/b/p",
        ),
        ("s3.amazonaws.com", "b", "", "s3:https://s3.amazonaws.com/b"),
    ],
)
def test_build_repo_url(endpoint, bucket, path, expected):
    assert bk.build_repo_url(endpoint, bucket, path) == expected


def test_interpret_delete_probe_appendonly():
    # A 403/AccessDenied on DELETE of a nonexistent object ⇒ append-only (good).
    assert bk.interpret_delete_probe(403) == "append_only"
    assert bk.interpret_delete_probe(401) == "append_only"


def test_interpret_delete_probe_delete_capable():
    # 204/404 ⇒ the DELETE was authorized (object just didn't exist) ⇒ can delete.
    assert bk.interpret_delete_probe(404) == "delete_capable"
    assert bk.interpret_delete_probe(204) == "delete_capable"


def test_generate_restic_password_is_high_entropy():
    a = bk.generate_restic_password()
    b = bk.generate_restic_password()
    assert a != b
    assert len(a) >= 32


def test_persist_config_writes_secrets_and_tunables(monkeypatch):
    """persist_config routes the 3 secrets through set_secret and the repo
    URL through a plain settings upsert — never the reverse."""
    set_secret_calls = []
    set_setting_calls = []

    async def fake_set_secret(conn, key, value, description=""):
        set_secret_calls.append(key)

    async def fake_set_setting(conn, key, value):
        set_setting_calls.append((key, value))

    monkeypatch.setattr(bk, "_set_secret", fake_set_secret)
    monkeypatch.setattr(bk, "_set_setting", fake_set_setting)

    class _Conn:
        async def close(self):
            ...

    async def fake_connect(*a, **k):
        return _Conn()

    monkeypatch.setattr(bk, "_connect", fake_connect)

    asyncio.run(
        bk.persist_config(
            dsn="postgresql://x",
            repo_url="s3:https://h/b/p",
            region="us-east-005",
            restic_password="pw",
            access_key_id="akid",
            secret_access_key="sak",
        )
    )

    assert set(set_secret_calls) == {
        "offsite_backup_restic_password",
        "offsite_backup_s3_access_key_id",
        "offsite_backup_s3_secret_access_key",
    }
    # repo URL AND region are plaintext settings, not secrets.
    assert ("offsite_backup_repository", "s3:https://h/b/p") in set_setting_calls
    assert ("offsite_backup_s3_region", "us-east-005") in set_setting_calls


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("s3.us-east-005.backblazeb2.com", "us-east-005"),
        ("s3.us-west-002.backblazeb2.com", "us-west-002"),
        ("https://s3.us-east-005.backblazeb2.com/", "us-east-005"),
        ("s3.amazonaws.com", "us-east-1"),
        ("s3.eu-west-1.amazonaws.com", "eu-west-1"),
        ("abc123.r2.cloudflarestorage.com", "auto"),
        ("minio.local:9000", "us-east-1"),
    ],
)
def test_derive_s3_region(endpoint, expected):
    # The bug this guards: a B2 us-east-005 bucket needs AWS_DEFAULT_REGION or
    # restic signs with us-east-1 and B2 rejects the signature.
    assert bk._derive_s3_region(endpoint) == expected


def test_format_status_unconfigured():
    out = bk._format_status(repo="", last_success_age_s=None, last_verify_age_s=None)
    assert "not configured" in out.lower()


def test_format_status_configured_fresh():
    out = bk._format_status(
        repo="s3:https://h/b/p", last_success_age_s=3600, last_verify_age_s=7200,
    )
    assert "s3:https://h/b/p" in out
    assert "1.0h ago" in out  # last success


def test_fmt_age_buckets():
    assert bk._fmt_age(None) == "never"
    assert bk._fmt_age(3600) == "1.0h ago"
    # >= 48h rolls over to days
    assert bk._fmt_age(72 * 3600) == "3.0d ago"


# --- streaming acceptance gate (aligns the wizard onto the runner's path) ----
#
# Follow-up to PR #2317: the in-stack runner streams a fresh
# `pg_dump --format=custom -Z0 | restic backup --stdin --stdin-filename
# poindexter_brain.dump --host poindexter`. The wizard's first snapshot must
# share that (host, stdin-filename) parent key so the runner's first tick
# dedupes against it instead of re-ingesting the full dump once.


def test_pg_creds_from_dsn_parses_user_password_database():
    user, pw, db = bk._pg_creds_from_dsn(
        "postgresql://poindexter:s3cr3t@localhost:5432/poindexter_brain"
    )
    assert (user, pw, db) == ("poindexter", "s3cr3t", "poindexter_brain")


def test_pg_creds_from_dsn_url_decodes_password_and_ignores_query():
    # A password with reserved chars is percent-encoded in the DSN; the wizard
    # must hand the DECODED value to PGPASSWORD or pg_dump auth fails.
    user, pw, db = bk._pg_creds_from_dsn(
        "postgresql://poindexter:p%40ss%2Fword@host:5432/poindexter_brain?sslmode=disable"
    )
    assert user == "poindexter"
    assert pw == "p@ss/word"
    assert db == "poindexter_brain"


def test_pg_creds_from_dsn_defaults_when_absent():
    # Host-only DSN — fall back to the compose defaults the runner also uses.
    user, pw, db = bk._pg_creds_from_dsn("postgresql://host:5432")
    assert user == "poindexter"
    assert pw == ""
    assert db == "poindexter_brain"


def _sample_streaming_cmd():
    return bk._streaming_backup_cmd(
        image="poindexter-backup:latest",
        repo="s3:https://s3.us-east-005.backblazeb2.com/bkt/poindexter",
        network="glad-labs-website_default",
        pg_host="postgres-local",
        pg_port="5432",
        pg_user="poindexter",
        pg_database="poindexter_brain",
        restic_host="poindexter",
        source_tier="daily",
        stdin_filename="poindexter_brain.dump",
        env={
            "AWS_ACCESS_KEY_ID": "akid",
            "AWS_SECRET_ACCESS_KEY": "sak",
            "AWS_DEFAULT_REGION": "us-east-005",
            "RESTIC_PASSWORD": "rpw",
            "PGPASSWORD": "pgpw",
        },
    )


def test_streaming_backup_cmd_uses_backup_image_on_postgres_network():
    cmd = _sample_streaming_cmd()
    assert cmd[:3] == ["docker", "run", "--rm"]
    # Attaches to the discovered postgres network so pg_dump reaches postgres-local.
    assert "--network" in cmd
    assert cmd[cmd.index("--network") + 1] == "glad-labs-website_default"
    # Uses the image that actually has pg_dump (Dockerfile.backup), NOT restic/restic.
    assert "poindexter-backup:latest" in cmd
    assert bk._DEFAULT_RESTIC_IMAGE not in cmd
    # Entrypoint overridden to a shell so we can run the pg_dump | restic pipe.
    assert cmd[cmd.index("--entrypoint") + 1] == "sh"
    assert cmd[-2] == "-c"


def test_streaming_backup_cmd_mirrors_runner_parent_key():
    # The whole point of the follow-up: the wizard's snapshot must share the
    # runner's (host, stdin-filename) parent key. restic selects a parent by
    # (host, paths); for a --stdin backup, paths == the --stdin-filename.
    pipeline = _sample_streaming_cmd()[-1]
    assert "--stdin-filename poindexter_brain.dump" in pipeline
    assert "--host poindexter" in pipeline
    assert "restic -r" in pipeline
    assert "s3:https://s3.us-east-005.backblazeb2.com/bkt/poindexter" in pipeline
    assert "backup --stdin" in pipeline


def test_streaming_backup_cmd_streams_uncompressed_dump_with_pipefail():
    pipeline = _sample_streaming_cmd()[-1]
    assert "pg_dump" in pipeline
    # -Z0 = no pg_dump-side compression so restic can dedupe/compress (PR #2317).
    assert "--format=custom -Z0" in pipeline
    assert "--no-owner --no-acl" in pipeline
    # A shell pipe, guarded by pipefail so a half-streamed dump fails the gate
    # instead of silently saving a truncated snapshot.
    assert "|" in pipeline
    assert "pipefail" in pipeline
    # Matches the runner's tags (poindexter + source tier).
    assert "--tag poindexter --tag daily" in pipeline


def test_streaming_backup_cmd_passes_secrets_only_via_env_not_shell():
    cmd = _sample_streaming_cmd()
    pipeline = cmd[-1]
    # Secrets must never be interpolated into the shell string (they would leak
    # via `ps` inside the container); they ride in through `-e KEY=VALUE`.
    for secret in ("rpw", "sak", "pgpw"):
        assert secret not in pipeline
    joined = " ".join(cmd)
    assert "-e RESTIC_PASSWORD=rpw" in joined
    assert "-e PGPASSWORD=pgpw" in joined
    assert "-e AWS_SECRET_ACCESS_KEY=sak" in joined


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_postgres_network_returns_first_attached_network(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd[:2] == ["docker", "inspect"]
        return _FakeProc(returncode=0, stdout="glad-labs-website_default \n")

    monkeypatch.setattr(bk.subprocess, "run", fake_run)
    assert bk._postgres_network() == "glad-labs-website_default"


def test_postgres_network_none_when_container_absent(monkeypatch):
    monkeypatch.setattr(
        bk.subprocess,
        "run",
        lambda *a, **k: _FakeProc(returncode=1, stderr="No such object"),
    )
    assert bk._postgres_network() is None


def test_postgres_network_none_on_empty_output(monkeypatch):
    monkeypatch.setattr(
        bk.subprocess, "run", lambda *a, **k: _FakeProc(returncode=0, stdout="  \n")
    )
    assert bk._postgres_network() is None


def test_docker_image_present_true_on_zero_rc(monkeypatch):
    monkeypatch.setattr(
        bk.subprocess, "run", lambda *a, **k: _FakeProc(returncode=0, stdout="[]")
    )
    assert bk._docker_image_present("poindexter-backup:latest") is True


def test_docker_image_present_false_on_nonzero_rc(monkeypatch):
    monkeypatch.setattr(
        bk.subprocess,
        "run",
        lambda *a, **k: _FakeProc(returncode=1, stderr="No such image"),
    )
    assert bk._docker_image_present("poindexter-backup:latest") is False


class TestVerifyRecoveryDrill:
    """`backup verify-recovery` (poindexter#889).

    The offsite repo's credentials live as encrypted app_settings rows INSIDE
    the database the backup contains, decrypted by a key that exists only in
    bootstrap.toml — which the runner never backs up. So after a total loss the
    repository cannot be opened, while every monitoring signal stays green.

    This command is the drill that catches that, and its defining property is a
    negative one: it must read NOTHING from this install. These tests pin that.
    """

    def _invoke(self, monkeypatch, *, returncode=0, stdout="[]", stderr=""):
        from click.testing import CliRunner

        seen = {}

        def _fake_run_restic(image, repo, args, *, env, source_mount=None):
            seen["image"] = image
            seen["repo"] = repo
            seen["args"] = args
            seen["env"] = env
            return subprocess.CompletedProcess(
                args=[], returncode=returncode, stdout=stdout, stderr=stderr,
            )

        monkeypatch.setattr(bk, "_run_restic", _fake_run_restic)
        result = CliRunner().invoke(
            bk.backup_verify_recovery,
            [
                "--repository", "s3:https://s3.example.com/bucket",
                "--restic-password", "pw",
                "--s3-access-key-id", "akid",
                "--s3-secret-access-key", "secret",
                "--region", "us-east-005",
            ],
        )
        return result, seen

    def test_never_touches_the_database(self, monkeypatch):
        """The whole point: a drill that reads app_settings proves nothing,
        because app_settings is what you have lost."""
        def _boom(*_a, **_kw):
            raise AssertionError(
                "verify-recovery resolved a DSN — it must not read the DB"
            )

        monkeypatch.setattr("brain.bootstrap.resolve_database_url", _boom)
        monkeypatch.setattr(bk, "_get_setting", _boom)
        monkeypatch.setattr(bk, "_resolved_secret_env", _boom)
        monkeypatch.setattr(bk, "_run_or_die", _boom)

        result, _seen = self._invoke(monkeypatch, stdout='[{"id":"a"}]')

        assert result.exit_code == 0, result.output

    def test_passes_supplied_credentials_through_to_restic(self, monkeypatch):
        result, seen = self._invoke(monkeypatch, stdout='[{"id":"a"},{"id":"b"}]')

        assert result.exit_code == 0, result.output
        assert seen["env"]["RESTIC_PASSWORD"] == "pw"
        assert seen["env"]["AWS_ACCESS_KEY_ID"] == "akid"
        assert seen["env"]["AWS_SECRET_ACCESS_KEY"] == "secret"
        assert seen["env"]["AWS_DEFAULT_REGION"] == "us-east-005"
        assert seen["repo"] == "s3:https://s3.example.com/bucket"
        assert seen["args"][0] == "snapshots"
        assert "2 snapshot(s) readable" in result.output

    def test_failure_surfaces_restic_stderr(self, monkeypatch):
        """'wrong password' vs 'bucket not found' vs 'access denied' are three
        different recovery problems — the operator must be told which."""
        result, _seen = self._invoke(
            monkeypatch, returncode=1, stdout="",
            stderr="Fatal: wrong password or no key found",
        )

        assert result.exit_code != 0
        assert "RECOVERY DRILL FAILED" in result.output
        assert "wrong password" in result.output

    def test_zero_snapshots_warns_even_though_creds_work(self, monkeypatch):
        """Opening an empty repo means the credentials are right and the
        backups are not landing — a pass that still needs flagging."""
        result, _seen = self._invoke(monkeypatch, stdout="[]")

        assert result.exit_code == 0, result.output
        assert "RECOVERY DRILL PASSED" in result.output
        assert "ZERO snapshots" in result.output

    def test_unparseable_json_still_passes_without_inventing_a_count(
        self, monkeypatch
    ):
        result, _seen = self._invoke(monkeypatch, stdout="not json")

        assert result.exit_code == 0, result.output
        assert "RECOVERY DRILL PASSED" in result.output
        assert "unparsed" in result.output


@pytest.mark.parametrize(
    "stdout,expected",
    [
        ("[]", 0),
        ('[{"id":"a"}]', 1),
        ("", 0),            # empty stdout defaults to an empty list
        ("not json", None),  # unparseable -> decline to report a number
        ('{"id":"a"}', None),  # object, not a list
    ],
)
def test_snapshot_count(stdout, expected):
    assert bk._snapshot_count(stdout) == expected
