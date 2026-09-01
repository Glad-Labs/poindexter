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

import poindexter.cli.app as _app_mod
import poindexter.cli.integrations as _integrations_mod

# Pristine state, captured at IMPORT time — collection, before any test in any
# file has run. ``TestModuleIsNeverLeftReloaded`` asserts against these.
# Capturing inside a test would be worthless: by then a reload may already have
# replaced them, and the guard would compare a leaked object to itself.
_ORIGINAL_NAMESPACE = dict(_integrations_mod.__dict__)
_ORIGINAL_GROUP = _integrations_mod.integrations_group
# ``poindexter/cli/app.py`` does ``from .integrations import integrations_group``
# at import time and registers THAT object as the ``integrations`` subcommand.
# It is the live consumer this module's identity has to keep agreeing with.
_ORIGINAL_APP_REGISTERED = _app_mod.main.commands["integrations"]

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _restore_integrations_module():
    """Keep any module mutation from escaping this file.

    Nothing here reloads today (see ``integrations_module``), so on a normal run
    this fixture restores a namespace nobody touched. It earns its place by
    closing the gap the guard class cannot: the guards run at a fixed point in
    the file, so a mutation introduced in a test defined *after* them — or one
    made without ``monkeypatch`` — would otherwise sail straight out.

    The restoration has to be a hard swap. ``importlib.reload`` re-executes the
    module in its EXISTING ``__dict__``, so re-reloading to "clean up" mints a
    third set of objects, equal to neither. Only putting the originals back
    works. Same pattern as ``test_litellm_provider_sdk_guard.py`` (stack#3155)
    and ``test_oauth_helper.py`` (stack#3164).
    """
    snapshot = dict(_integrations_mod.__dict__)
    try:
        yield
    finally:
        _integrations_mod.__dict__.clear()
        _integrations_mod.__dict__.update(snapshot)


@pytest.fixture
def integrations_module():
    """The CLI module under test.

    This used to ``importlib.reload`` the module on every test "to dodge mock
    leakage". It never needed to: every mock in this file goes through
    ``monkeypatch.setattr``, which restores at teardown on its own. The reload
    bought no isolation and cost real damage — see
    ``TestModuleIsNeverLeftReloaded`` for what it broke.
    """
    return _integrations_mod


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


def _patch_consent(monkeypatch, integrations_module, creds: Any) -> list[tuple[str, str, bool]]:
    """Record (client_id, client_secret, with_update) per consent call.

    ``with_update`` is what selects the wider youtube.force-ssl scope needed by
    videos.update, so the tests assert on it rather than on the scope list the
    real flow builds internally.
    """
    calls: list[tuple[str, str, bool]] = []

    def fake_consent(cid: str, csecret: str, *, with_update: bool = False) -> Any:
        calls.append((cid, csecret, with_update))
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
    def test_missing_inputs_fails_loudly(
        self, runner, integrations_module, monkeypatch,
    ):
        """Nothing on the CLI AND nothing stored — the fresh-install case.

        Setup now falls back to the OAuth client already in app_settings (so a
        re-consent needs no flags), which means "no inputs" alone is no longer
        a failure. The stub represents an install that has never been set up.
        """

        async def no_stored_secrets() -> dict[str, str]:
            return {}

        monkeypatch.setattr(
            integrations_module, "_read_secrets", no_stored_secrets,
        )
        result = runner.invoke(
            integrations_module.integrations_group,
            ["youtube", "setup"],
        )
        assert result.exit_code != 0
        assert "Provide --client-secret-file" in result.output
        assert "nothing to reuse" in result.output

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


# ---------------------------------------------------------------------------
# the module must never be left reloaded (stack#3155 / stack#3164 hazard class)
# ---------------------------------------------------------------------------


class TestModuleIsNeverLeftReloaded:
    """Regression guard for the per-test ``importlib.reload`` this file used to do.

    ``importlib.reload`` re-executes a module in its EXISTING ``__dict__``, so
    every class and function it defines is replaced by a new object with the
    same name. Anything that bound one of those names earlier keeps the old
    object, and the two are no longer interchangeable — ``isinstance`` is False
    across them, and so is ``is``.

    Here that had a live consumer, not just a theoretical one: ``cli/app.py``
    does ``from .integrations import integrations_group`` at import time and
    registers that object as the ``integrations`` subcommand. Reloading left the
    CLI operators actually run holding a *different* group object than
    ``poindexter.cli.integrations`` exposed — verified by probe before the fix.

    The guarantee is *between* tests, not within one, so these assert against
    ``_ORIGINAL_*`` captured at this file's import time (collection, before any
    test in any file has run).

    **Order matters here — do not sort these methods.** ``test_a_reload_...``
    deliberately damages the module and runs FIRST; the pristine checks that
    follow are therefore verifying that the autouse fixture actually put it
    back, rather than passing over a namespace nobody touched. Delete the
    fixture and the three checks below fail. Definition order is what pytest
    runs, and CI's ``--dist loadfile`` keeps a whole file on one xdist worker,
    so this holds in CI too.
    """

    def test_a_reload_would_break_identity(self):
        """Proves the guards below are not theatre, and documents the damage.

        The only deliberate reload left in this file. It shows exactly what the
        removed fixture was doing on every single test — including splitting the
        CLI from its own module.
        """
        import importlib

        reloaded = importlib.reload(_integrations_mod)

        assert reloaded.integrations_group is not _ORIGINAL_GROUP
        assert reloaded._load_client_config is not _ORIGINAL_NAMESPACE["_load_client_config"]
        # The damage that actually reaches an operator surface.
        assert _app_mod.main.commands["integrations"] is not reloaded.integrations_group

    def test_group_identity_is_pristine_at_test_start(self):
        assert _integrations_mod.integrations_group is _ORIGINAL_GROUP, (
            "integrations_group is not the object it was at import time — "
            "something reloaded poindexter.cli.integrations without restoring "
            "it. If a reload is genuinely needed, snapshot mod.__dict__ and "
            "hard-restore it; see test_oauth_helper.py (stack#3164)."
        )

    def test_app_registration_still_matches_the_module(self):
        """The operator-facing CLI and the module must not drift apart.

        This is the assertion with teeth: it fails the moment a reload leaves
        ``poindexter.cli.app`` wired to a group object the module no longer
        exposes.
        """
        assert _app_mod.main.commands["integrations"] is _integrations_mod.integrations_group, (
            "poindexter.cli.app has a different integrations group object than "
            "poindexter.cli.integrations now exposes — a leaked reload split "
            "the CLI from its own module."
        )
        assert _ORIGINAL_APP_REGISTERED is _ORIGINAL_GROUP

    def test_whole_namespace_is_pristine_at_test_start(self):
        """Catch-all, so symbols added later inherit the guard for free."""
        current = _integrations_mod.__dict__
        assert set(current) == set(_ORIGINAL_NAMESPACE), (
            "module namespace gained/lost keys since import: "
            f"added={sorted(set(current) - set(_ORIGINAL_NAMESPACE))} "
            f"removed={sorted(set(_ORIGINAL_NAMESPACE) - set(current))}"
        )
        rebound = sorted(
            name for name, obj in _ORIGINAL_NAMESPACE.items()
            if current[name] is not obj
        )
        assert not rebound, (
            f"module attributes were rebound since import: {rebound}. A test "
            "mutated or reloaded poindexter.cli.integrations without restoring it."
        )


class TestSetupUpdateScope:
    """``--with-update`` is what makes `youtube sync-metadata` possible.

    The default grant (``youtube.upload``) is INSERT-ONLY — verified against
    the live token 2026-08-31, which came back with exactly that one scope —
    so editing a published video's metadata needs a deliberate re-consent.
    """

    def test_default_setup_requests_upload_only(
        self, runner, integrations_module, stub_db_calls, monkeypatch,
    ):
        calls = _patch_consent(monkeypatch, integrations_module, _make_creds())
        _patch_verify(monkeypatch, integrations_module)

        result = runner.invoke(
            integrations_module.integrations_group,
            ["youtube", "setup", "--client-id", "cid", "--client-secret", "csec"],
            input="n\n",
        )
        assert result.exit_code == 0, result.output
        assert calls[0][2] is False
        # And the operator is told up front why sync-metadata will refuse.
        assert "INSERT-ONLY" in result.output

    def test_with_update_flag_requests_the_wider_scope(
        self, runner, integrations_module, stub_db_calls, monkeypatch,
    ):
        calls = _patch_consent(monkeypatch, integrations_module, _make_creds())
        _patch_verify(monkeypatch, integrations_module)

        result = runner.invoke(
            integrations_module.integrations_group,
            [
                "youtube", "setup", "--client-id", "cid",
                "--client-secret", "csec", "--with-update",
            ],
            input="n\n",
        )
        assert result.exit_code == 0, result.output
        assert calls[0][2] is True
        assert "force-ssl" in result.output

    def test_scope_lists_are_a_superset_not_a_swap(self):
        """The update grant must still cover uploading, or re-consenting to
        edit metadata would break the upload path."""
        from services.publish_adapters.youtube import _SCOPES, _SCOPES_WITH_UPDATE

        assert set(_SCOPES).issubset(set(_SCOPES_WITH_UPDATE))
        assert "youtube.force-ssl" in " ".join(_SCOPES_WITH_UPDATE)


class TestSetupReusesStoredClient:
    """Re-consent must not require re-supplying the OAuth client.

    The client_id/client_secret are already in app_settings from the first
    setup; only the SCOPES differ on a `--with-update` re-consent. Demanding
    them again sent the operator hunting for the original client-secret JSON
    and pushed a long-lived secret onto the shell command line.
    """

    def test_falls_back_to_the_stored_client(self, integrations_module):
        cid, csec = integrations_module._load_client_config(
            client_id=None,
            client_secret_file=None,
            client_secret=None,
            stored={"client_id": "stored-id", "client_secret": "stored-secret"},
        )
        assert (cid, csec) == ("stored-id", "stored-secret")

    def test_explicit_flags_win_over_stored(self, integrations_module):
        """An operator naming a client means it — swapping to a NEW OAuth
        client must not be silently overridden by the old stored one."""
        cid, csec = integrations_module._load_client_config(
            client_id="cli-id",
            client_secret="cli-secret",
            client_secret_file=None,
            stored={"client_id": "stored-id", "client_secret": "stored-secret"},
        )
        assert (cid, csec) == ("cli-id", "cli-secret")

    def test_partial_stored_pair_does_not_resolve(self, integrations_module):
        """Half a credential is not a credential — fail with the actionable
        message rather than starting a flow that cannot complete."""
        with pytest.raises(Exception) as exc:
            integrations_module._load_client_config(
                client_id=None,
                client_secret_file=None,
                client_secret=None,
                stored={"client_id": "stored-id", "client_secret": ""},
            )
        assert "nothing to reuse" in str(exc.value)

    def test_no_input_and_nothing_stored_says_so(self, integrations_module):
        with pytest.raises(Exception) as exc:
            integrations_module._load_client_config(
                client_id=None, client_secret_file=None, client_secret=None, stored={}
            )
        assert "nothing to reuse" in str(exc.value)

    def test_setup_end_to_end_with_only_the_with_update_flag(
        self, runner, integrations_module, stub_db_calls, monkeypatch,
    ):
        """The whole point: `setup --with-update` and nothing else."""
        calls = _patch_consent(monkeypatch, integrations_module, _make_creds())
        _patch_verify(monkeypatch, integrations_module)
        stub_db_calls["read_returns"].update(
            client_id="stored-id", client_secret="stored-secret"
        )

        result = runner.invoke(
            integrations_module.integrations_group,
            ["youtube", "setup", "--with-update"],
            input="n\n",
        )
        assert result.exit_code == 0, result.output
        assert calls[0][:2] == ("stored-id", "stored-secret")
        assert calls[0][2] is True  # wider scope requested
        assert "Reusing the OAuth client" in result.output
