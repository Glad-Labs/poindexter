"""Unit tests for ``poindexter integrations youtube`` CLI.

Covers:

- ``setup`` happy path (file-based + raw client-id/secret variants)
- ``setup`` failure modes: missing input, bad JSON, OAuth flow raises,
  no refresh_token returned, channels.list verification failure, secret-
  write failure
- ``test`` happy path + the missing-secrets / adapter-failure branches
- ``--public`` flag flips privacy from unlisted to public on the adapter

Every Google API call is mocked at the import boundary —
``_run_consent_flow`` and ``_verify_channel`` get monkeypatched on the
``poindexter.cli.integrations`` module so no real HTTP / browser ever
fires. The DB write path is mocked the same way (``_write_secrets``,
``_read_secrets``, ``_set_enabled`` patched on the module).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def integrations_module():
    """Import the CLI module fresh per test to dodge mock leakage."""
    import importlib

    import poindexter.cli.integrations as _mod

    return importlib.reload(_mod)


@pytest.fixture
def stub_db_calls(monkeypatch, integrations_module):
    """Replace the 3 DB-touching helpers with awaitable no-ops + a
    recording shim, so tests don't need a live Postgres."""
    write_calls: list[dict[str, str]] = []
    set_enabled_calls: list[bool] = []
    read_returns: dict[str, str] = {
        "client_id": "stored-cid",
        "client_secret": "stored-csecret",
        "refresh_token": "stored-rtok",
    }

    async def fake_write(*, client_id: str, client_secret: str, refresh_token: str) -> None:
        write_calls.append(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            }
        )

    async def fake_read() -> dict[str, str]:
        return dict(read_returns)

    async def fake_set_enabled(value: bool) -> None:
        set_enabled_calls.append(value)

    monkeypatch.setattr(integrations_module, "_write_secrets", fake_write)
    monkeypatch.setattr(integrations_module, "_read_secrets", fake_read)
    monkeypatch.setattr(integrations_module, "_set_enabled", fake_set_enabled)

    return {
        "write_calls": write_calls,
        "set_enabled_calls": set_enabled_calls,
        "read_returns": read_returns,
    }


def _make_creds(refresh_token: str = "fresh-rtok") -> MagicMock:
    creds = MagicMock()
    creds.refresh_token = refresh_token
    return creds


def _patch_consent(monkeypatch, integrations_module, creds: Any) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def fake_consent(cid: str, csecret: str) -> Any:
        calls.append((cid, csecret))
        return creds

    monkeypatch.setattr(integrations_module, "_run_consent_flow", fake_consent)
    return calls


def _patch_verify(
    monkeypatch,
    integrations_module,
    *,
    channel_id: str = "UC-test-channel",
    channel_title: str = "Test Channel",
    raises: BaseException | None = None,
) -> None:
    if raises is not None:
        def fake_verify(_creds):
            raise raises

        monkeypatch.setattr(integrations_module, "_verify_channel", fake_verify)
        return

    def fake_verify(_creds):
        return {"channel_id": channel_id, "channel_title": channel_title}

    monkeypatch.setattr(integrations_module, "_verify_channel", fake_verify)


# ---------------------------------------------------------------------------
# setup — input resolution
# ---------------------------------------------------------------------------


class TestSetupInputResolution:
    def test_missing_inputs_fails_loudly(self, runner, integrations_module):
        """Neither --client-secret-file nor --client-id/--client-secret."""
        result = runner.invoke(
            integrations_module.integrations_group,
            ["youtube", "setup"],
        )
        assert result.exit_code != 0
        assert "Provide --client-secret-file" in result.output

    def test_client_secret_file_missing_path_fails(
        self, runner, integrations_module, tmp_path,
    ):
        result = runner.invoke(
            integrations_module.integrations_group,
            [
                "youtube", "setup",
                "--client-secret-file", str(tmp_path / "nope.json"),
            ],
        )
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_client_secret_file_invalid_json_fails(
        self, runner, integrations_module, tmp_path,
    ):
        bad = tmp_path / "client.json"
        bad.write_text("{ not json", encoding="utf-8")
        result = runner.invoke(
            integrations_module.integrations_group,
            ["youtube", "setup", "--client-secret-file", str(bad)],
        )
        assert result.exit_code != 0
        assert "not valid JSON" in result.output

    def test_client_secret_file_missing_installed_block_fails(
        self, runner, integrations_module, tmp_path,
    ):
        bad = tmp_path / "client.json"
        bad.write_text(json.dumps({"unrelated": {}}), encoding="utf-8")
        result = runner.invoke(
            integrations_module.integrations_group,
            ["youtube", "setup", "--client-secret-file", str(bad)],
        )
        assert result.exit_code != 0
        assert "missing client_id" in result.output

    def test_load_client_config_accepts_web_block(self, integrations_module, tmp_path):
        f = tmp_path / "web.json"
        f.write_text(
            json.dumps({"web": {"client_id": "wcid", "client_secret": "wsec"}}),
            encoding="utf-8",
        )
        cid, csec = integrations_module._load_client_config(
            client_id=None, client_secret_file=str(f), client_secret=None,
        )
        assert (cid, csec) == ("wcid", "wsec")

    def test_load_client_config_accepts_installed_block(
        self, integrations_module, tmp_path,
    ):
        f = tmp_path / "installed.json"
        f.write_text(
            json.dumps({"installed": {"client_id": "icid", "client_secret": "isec"}}),
            encoding="utf-8",
        )
        cid, csec = integrations_module._load_client_config(
            client_id=None, client_secret_file=str(f), client_secret=None,
        )
        assert (cid, csec) == ("icid", "isec")

    def test_load_client_config_raw_args(self, integrations_module):
        cid, csec = integrations_module._load_client_config(
            client_id="rcid", client_secret_file=None, client_secret="rsec",
        )
        assert (cid, csec) == ("rcid", "rsec")


# ---------------------------------------------------------------------------
# setup — happy path
# ---------------------------------------------------------------------------


class TestSetupHappyPath:
    def test_full_setup_writes_3_secrets(
        self, runner, integrations_module, stub_db_calls, monkeypatch,
    ):
        creds = _make_creds(refresh_token="my-rtok")
        _patch_consent(monkeypatch, integrations_module, creds)
        _patch_verify(
            monkeypatch, integrations_module,
            channel_id="UCabcd", channel_title="My Channel",
        )

        result = runner.invoke(
            integrations_module.integrations_group,
            ["youtube", "setup", "--client-id", "cid", "--client-secret", "csec"],
            input="n\n",  # decline the enable flip
        )

        assert result.exit_code == 0, result.output
        assert "My Channel" in result.output
        assert "UCabcd" in result.output
        assert "setup complete" in result.output.lower()

        # Exactly one write call with the right fields.
        assert len(stub_db_calls["write_calls"]) == 1
        call = stub_db_calls["write_calls"][0]
        assert call == {
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "my-rtok",
        }

        # User declined → enabled flag not flipped.
        assert stub_db_calls["set_enabled_calls"] == []

    def test_setup_via_client_secret_file(
        self, runner, integrations_module, stub_db_calls, monkeypatch, tmp_path,
    ):
        client_file = tmp_path / "client_secret_xxx.json"
        client_file.write_text(
            json.dumps(
                {"installed": {"client_id": "file-cid", "client_secret": "file-sec"}},
            ),
            encoding="utf-8",
        )
        _patch_consent(monkeypatch, integrations_module, _make_creds("r"))
        _patch_verify(monkeypatch, integrations_module)

        result = runner.invoke(
            integrations_module.integrations_group,
            ["youtube", "setup", "--client-secret-file", str(client_file)],
            input="n\n",
        )

        assert result.exit_code == 0, result.output
        # client_id propagated from JSON
        assert stub_db_calls["write_calls"][0]["client_id"] == "file-cid"
        assert stub_db_calls["write_calls"][0]["client_secret"] == "file-sec"

    def test_setup_yes_flag_skips_prompt_and_does_not_auto_enable(
        self, runner, integrations_module, stub_db_calls, monkeypatch,
    ):
        """--yes is "skip the prompt", NOT "auto-enable". Operator
        must still flip the switch manually per the runbook."""
        _patch_consent(monkeypatch, integrations_module, _make_creds("r"))
        _patch_verify(monkeypatch, integrations_module)

        result = runner.invoke(
            integrations_module.integrations_group,
            [
                "youtube", "setup",
                "--client-id", "c", "--client-secret", "s", "--yes",
            ],
        )
        assert result.exit_code == 0, result.output
        # No prompt was issued (no "Enable" confirm wired in input=)
        # AND enabled flag was not flipped.
        assert stub_db_calls["set_enabled_calls"] == []
        assert "enabled flag left at its current value" in result.output

    def test_setup_accept_enable_prompt_flips_flag(
        self, runner, integrations_module, stub_db_calls, monkeypatch,
    ):
        _patch_consent(monkeypatch, integrations_module, _make_creds("r"))
        _patch_verify(monkeypatch, integrations_module)

        result = runner.invoke(
            integrations_module.integrations_group,
            ["youtube", "setup", "--client-id", "c", "--client-secret", "s"],
            input="y\n",  # accept enable
        )
        assert result.exit_code == 0, result.output
        assert stub_db_calls["set_enabled_calls"] == [True]


# ---------------------------------------------------------------------------
# setup — failure modes
# ---------------------------------------------------------------------------


class TestSetupFailures:
    def test_consent_flow_exception_bails(
        self, runner, integrations_module, stub_db_calls, monkeypatch,
    ):
        def boom(_cid, _csec):
            raise RuntimeError("user closed browser")

        monkeypatch.setattr(integrations_module, "_run_consent_flow", boom)

        result = runner.invoke(
            integrations_module.integrations_group,
            ["youtube", "setup", "--client-id", "c", "--client-secret", "s"],
        )
        assert result.exit_code != 0
        assert "OAuth consent flow failed" in result.output
        # No write happened
        assert stub_db_calls["write_calls"] == []

    def test_no_refresh_token_bails(
        self, runner, integrations_module, stub_db_calls, monkeypatch,
    ):
        # Google sometimes returns access_token without refresh_token
        # when re-consenting the same client.
        creds = _make_creds(refresh_token=None)
        _patch_consent(monkeypatch, integrations_module, creds)

        result = runner.invoke(
            integrations_module.integrations_group,
            ["youtube", "setup", "--client-id", "c", "--client-secret", "s"],
        )
        assert result.exit_code != 0
        assert "no refresh_token" in result.output
        assert "Revoke the app" in result.output
        assert stub_db_calls["write_calls"] == []

    def test_verify_channel_failure_is_best_effort(
        self, runner, integrations_module, stub_db_calls, monkeypatch,
    ):
        # channels.list(mine=True) 403s under an upload-only token — that
        # is expected, NOT a failure. A successful consent + refresh-token
        # exchange already proves the youtube.upload scope was granted, so
        # the setup flow skips the read-back, writes secrets, and exits 0.
        # End-to-end proof comes from `youtube test` (an actual upload).
        import click as _click

        _patch_consent(monkeypatch, integrations_module, _make_creds("r"))
        _patch_verify(
            monkeypatch, integrations_module,
            raises=_click.ClickException(
                "channels.list(mine=True) failed: youtube.upload scope missing",
            ),
        )

        result = runner.invoke(
            integrations_module.integrations_group,
            ["youtube", "setup", "--client-id", "c", "--client-secret", "s"],
            input="n\n",  # decline enable prompt (Click 8.2+ aborts on EOF)
        )
        assert result.exit_code == 0
        assert "Channel read-back skipped" in result.output
        # Best-effort skip still proceeds to persist the granted token.
        assert stub_db_calls["write_calls"] != []

    def test_write_secrets_failure_bails(
        self, runner, integrations_module, monkeypatch,
    ):
        _patch_consent(monkeypatch, integrations_module, _make_creds("r"))
        _patch_verify(monkeypatch, integrations_module)

        async def fake_write(**_kwargs):
            raise RuntimeError("pgcrypto key missing")

        monkeypatch.setattr(integrations_module, "_write_secrets", fake_write)
        # _set_enabled would only be called after success — stub it anyway
        async def fake_set_enabled(_value):
            return None

        monkeypatch.setattr(integrations_module, "_set_enabled", fake_set_enabled)

        result = runner.invoke(
            integrations_module.integrations_group,
            ["youtube", "setup", "--client-id", "c", "--client-secret", "s"],
        )
        assert result.exit_code != 0
        assert "Failed to write secrets" in result.output


# ---------------------------------------------------------------------------
# test — happy path + failures
# ---------------------------------------------------------------------------


class TestSmokeTest:
    def test_missing_secrets_bails_with_runbook_pointer(
        self, runner, integrations_module, monkeypatch, tmp_path,
    ):
        media = tmp_path / "v.mp4"
        media.write_bytes(b"x" * 100)

        async def fake_read():
            return {"client_id": "", "client_secret": "", "refresh_token": ""}

        monkeypatch.setattr(integrations_module, "_read_secrets", fake_read)

        result = runner.invoke(
            integrations_module.integrations_group,
            ["youtube", "test", "--media-path", str(media)],
        )
        assert result.exit_code != 0
        assert "Missing YouTube secrets" in result.output
        assert "setup" in result.output

    def test_happy_path_uploads_unlisted_by_default(
        self, runner, integrations_module, stub_db_calls, monkeypatch, tmp_path,
    ):
        media = tmp_path / "v.mp4"
        media.write_bytes(b"x" * 1024)

        captured: dict[str, Any] = {}

        # Patch the adapter's actual upload helper so no Google API hits.
        from services.publish_adapters import youtube as yt_mod

        def fake_upload(*, credentials, media_path, body):
            captured["body"] = body
            captured["media_path"] = media_path
            return {
                "id": "smoke-vid-id",
                "snippet": {
                    "channelId": "UCabcd",
                    "publishedAt": "2026-01-01T00:00:00Z",
                },
                "status": {"uploadStatus": "uploaded", "privacyStatus": "unlisted"},
            }

        monkeypatch.setattr(
            yt_mod.YouTubePublishAdapter,
            "_build_credentials",
            staticmethod(lambda secrets: MagicMock()),
        )
        monkeypatch.setattr(
            yt_mod.YouTubePublishAdapter,
            "_do_resumable_upload_blocking",
            staticmethod(fake_upload),
        )

        result = runner.invoke(
            integrations_module.integrations_group,
            ["youtube", "test", "--media-path", str(media)],
        )

        assert result.exit_code == 0, result.output
        assert "UPLOAD SUCCEEDED" in result.output
        assert "smoke-vid-id" in result.output
        # Default privacy = unlisted
        assert captured["body"]["status"]["privacyStatus"] == "unlisted"

    def test_public_flag_flips_privacy(
        self, runner, integrations_module, stub_db_calls, monkeypatch, tmp_path,
    ):
        media = tmp_path / "v.mp4"
        media.write_bytes(b"x" * 1024)

        captured: dict[str, Any] = {}

        from services.publish_adapters import youtube as yt_mod

        def fake_upload(*, credentials, media_path, body):
            captured["body"] = body
            return {
                "id": "p",
                "snippet": {},
                "status": {"uploadStatus": "uploaded", "privacyStatus": "public"},
            }

        monkeypatch.setattr(
            yt_mod.YouTubePublishAdapter,
            "_build_credentials",
            staticmethod(lambda secrets: MagicMock()),
        )
        monkeypatch.setattr(
            yt_mod.YouTubePublishAdapter,
            "_do_resumable_upload_blocking",
            staticmethod(fake_upload),
        )

        result = runner.invoke(
            integrations_module.integrations_group,
            ["youtube", "test", "--media-path", str(media), "--public"],
        )
        assert result.exit_code == 0, result.output
        assert captured["body"]["status"]["privacyStatus"] == "public"

    def test_adapter_failure_exits_nonzero(
        self, runner, integrations_module, stub_db_calls, monkeypatch, tmp_path,
    ):
        media = tmp_path / "v.mp4"
        media.write_bytes(b"x" * 1024)

        from services.publish_adapters import youtube as yt_mod

        def boom(*, credentials, media_path, body):
            raise RuntimeError("403 quota exceeded")

        monkeypatch.setattr(
            yt_mod.YouTubePublishAdapter,
            "_build_credentials",
            staticmethod(lambda secrets: MagicMock()),
        )
        monkeypatch.setattr(
            yt_mod.YouTubePublishAdapter,
            "_do_resumable_upload_blocking",
            staticmethod(boom),
        )

        result = runner.invoke(
            integrations_module.integrations_group,
            ["youtube", "test", "--media-path", str(media)],
        )
        assert result.exit_code != 0
        assert "FAILED" in result.output
        assert "quota exceeded" in result.output

    def test_missing_media_path_rejected_by_click(
        self, runner, integrations_module,
    ):
        result = runner.invoke(
            integrations_module.integrations_group,
            ["youtube", "test"],
        )
        assert result.exit_code != 0
        # Click flags --media-path missing
        assert "--media-path" in result.output

    def test_nonexistent_media_path_rejected_by_click(
        self, runner, integrations_module, tmp_path,
    ):
        result = runner.invoke(
            integrations_module.integrations_group,
            [
                "youtube", "test",
                "--media-path", str(tmp_path / "missing.mp4"),
            ],
        )
        assert result.exit_code != 0
        # click.Path(exists=True) emits "does not exist"
        assert "does not exist" in result.output.lower()


# ---------------------------------------------------------------------------
# _force escape hatch is wired in the adapter
# ---------------------------------------------------------------------------


class TestForceEscapeHatch:
    """Verify the _force kwarg on the adapter — added by this PR — lets
    the smoke test bypass the enabled flag while still requiring all
    three OAuth secrets."""

    @pytest.mark.asyncio
    async def test_force_true_bypasses_disabled_check(self):
        from services.publish_adapters.youtube import YouTubePublishAdapter

        class _SC:
            def get(self, key, default=None):
                # enabled=False — would normally block.
                if key == "plugin.publish_adapter.youtube.enabled":
                    return False
                return default

            async def get_secret(self, key, default=""):
                # All three secrets present
                return {
                    "plugin.publish_adapter.youtube.client_id": "x",
                    "plugin.publish_adapter.youtube.client_secret": "y",
                    "plugin.publish_adapter.youtube.refresh_token": "z",
                }.get(key, default)

        adapter = YouTubePublishAdapter(site_config=_SC())
        ready, error, secrets = await adapter._check_gating(force=True)
        assert ready is True
        assert error is None
        assert secrets == {"client_id": "x", "client_secret": "y", "refresh_token": "z"}

    @pytest.mark.asyncio
    async def test_force_true_still_requires_secrets(self):
        from services.publish_adapters.youtube import YouTubePublishAdapter

        class _SC:
            def get(self, key, default=None):
                return default

            async def get_secret(self, key, default=""):
                return ""  # nothing configured

        adapter = YouTubePublishAdapter(site_config=_SC())
        ready, error, _secrets = await adapter._check_gating(force=True)
        assert ready is False
        # Same "not configured" diagnostic
        assert "OAuth secrets not configured" in (error or "")

    @pytest.mark.asyncio
    async def test_force_false_default_still_blocks_when_disabled(self):
        """Sanity check — without force=True the existing disabled-gate
        behaviour is unchanged."""
        from services.publish_adapters.youtube import YouTubePublishAdapter

        class _SC:
            def get(self, key, default=None):
                if key == "plugin.publish_adapter.youtube.enabled":
                    return False
                return default

            async def get_secret(self, key, default=""):
                return "x"  # secrets present, but enabled=False

        adapter = YouTubePublishAdapter(site_config=_SC())
        ready, error, _ = await adapter._check_gating()
        assert ready is False
        assert "disabled" in (error or "")
