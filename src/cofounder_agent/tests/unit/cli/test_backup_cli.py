"""Unit tests for poindexter/cli/backup.py (poindexter#386)."""
from __future__ import annotations

import asyncio

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
    assert ("offsite_backup_repository", "s3:https://h/b/p") in set_setting_calls


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
