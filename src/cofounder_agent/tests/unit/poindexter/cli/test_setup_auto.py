"""``poindexter setup --auto`` provisions the stack's OWN database.

Before this, ``--auto`` started a standalone ``poindexter-postgres-auto``
container on host port 5434 and pointed bootstrap.toml at it, while every
container in the stack connected to the compose service ``postgres-local``.
The CLI queued tasks — and registered its OAuth client — in a database the
pipeline never read, so the README quick start could not produce a post.

These tests pin the replacement contract:

* ``--auto`` starts ``postgres-local`` from the same compose file and compose
  project ``scripts/start-stack.sh`` launches, and bootstrap.toml's
  ``database_url`` is that service's host-side DSN;
* every ``${VAR:?...}`` sentinel in the public stack's compose file has a
  generator, because compose refuses to load a file while ANY sentinel is
  unset (derived from the file, not hand-listed);
* a re-run keeps the secrets bootstrap.toml already holds;
* the CLI's OAuth client is written with the secret key the stack will use.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest
from click.testing import CliRunner

import poindexter
from poindexter.cli import setup as setup_mod

_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "scripts" / "start-stack.sh").is_file()
)
_PUBLIC_COMPOSE = _REPO_ROOT / "docker-compose.consumer.yml"
_SENTINEL = re.compile(r"\$\{([A-Z0-9_]+):\?")


def _compose_sentinels(path: Path) -> set[str]:
    """``${VAR:?...}`` names in a compose file's YAML, comments excluded."""
    live = [
        ln for ln in path.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    ]
    return set(_SENTINEL.findall("\n".join(live)))


def _make_checkout(root: Path, *, operator: bool = False) -> Path:
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "start-stack.sh").write_text("#!/usr/bin/env bash\n")
    (root / "docker-compose.consumer.yml").write_text("services: {}\n")
    if operator:
        (root / "docker-compose.local.yml").write_text("services: {}\n")
    return root


class TestSecretsCoverTheStack:
    def test_every_public_compose_sentinel_has_a_generator(self):
        """A sentinel with no generator makes the whole file refuse to load.

        That is how the consumer stack failed on every fresh install: it
        required API_TOKEN, POINDEXTER_SECRET_KEY and the (opt-in!) Postiz
        pair, and setup generated none of them.
        """
        sentinels = _compose_sentinels(_PUBLIC_COMPOSE)
        assert sentinels, "no ${VAR:?} sentinels parsed — the pattern or the file moved"
        generated = {k.upper() for k in setup_mod._generate_secrets()}
        missing = sorted(sentinels - generated)
        assert not missing, (
            f"docker-compose.consumer.yml requires {missing} but "
            "cli/setup.py::_generate_secrets() never generates them, so the "
            "public stack cannot load on a fresh install. Generate them, or "
            "make the compose value optional (${VAR:-})."
        )

    def test_api_token_is_not_required_by_the_public_stack(self):
        """The static-Bearer token was retired (#249) and is never generated."""
        text = _PUBLIC_COMPOSE.read_text(encoding="utf-8")
        assert "API_TOKEN:?" not in text


class TestStackSecrets:
    def test_existing_values_win_and_missing_ones_are_generated(self, monkeypatch):
        monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
        monkeypatch.delenv("POSTGRES_HOST_PORT", raising=False)
        existing = {
            "local_postgres_password": "volume-was-initialised-with-this",
            "poindexter_secret_key": "encrypts-existing-rows",
            "telegram_chat_id": "123",
        }
        values = setup_mod._stack_secrets(existing)
        assert values["local_postgres_password"] == "volume-was-initialised-with-this"
        assert values["poindexter_secret_key"] == "encrypts-existing-rows"
        assert values["telegram_chat_id"] == "123"
        # Keys the file lacked are generated.
        assert values["grafana_password"]
        assert values["postiz_jwt_secret"]
        assert values["compose_project_name"] == setup_mod._DEFAULT_COMPOSE_PROJECT

    def test_fresh_install_generates_a_secret_key(self, monkeypatch):
        monkeypatch.delenv("POSTGRES_HOST_PORT", raising=False)
        values = setup_mod._stack_secrets({})
        assert len(values["poindexter_secret_key"]) >= 32
        assert "postgres_host_port" not in values

    def test_compose_project_name_follows_the_shell_when_unset(self, monkeypatch):
        monkeypatch.setenv("COMPOSE_PROJECT_NAME", "my-stack")
        assert setup_mod._stack_secrets({})["compose_project_name"] == "my-stack"
        # ...but a value already in bootstrap.toml is never replaced.
        kept = setup_mod._stack_secrets({"compose_project_name": "original"})
        assert kept["compose_project_name"] == "original"

    def test_host_port_override_is_persisted(self, monkeypatch):
        """start-stack.sh exports bootstrap.toml, so the override must live there."""
        monkeypatch.setenv("POSTGRES_HOST_PORT", "15433")
        values = setup_mod._stack_secrets({})
        assert values["postgres_host_port"] == "15433"
        assert "@localhost:15433/" in setup_mod.stack_database_url(values)


class TestFindStackRoot:
    def test_found_from_a_subdirectory(self, tmp_path):
        root = _make_checkout(tmp_path / "poindexter")
        sub = root / "src" / "cofounder_agent"
        sub.mkdir(parents=True)
        assert setup_mod.find_stack_root(sub) == root.resolve()

    def test_none_outside_a_checkout(self, tmp_path, monkeypatch):
        """A PyPI install carries no compose file — --auto has nothing to start."""
        monkeypatch.setattr(poindexter, "__file__", str(tmp_path / "site" / "poindexter" / "__init__.py"))
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        assert setup_mod.find_stack_root(elsewhere) is None

    def test_falls_back_to_the_package_location(self, tmp_path, monkeypatch):
        """An editable install finds its checkout whatever the working directory."""
        root = _make_checkout(tmp_path / "poindexter")
        pkg = root / "src" / "cofounder_agent" / "poindexter"
        pkg.mkdir(parents=True)
        monkeypatch.setattr(poindexter, "__file__", str(pkg / "__init__.py"))
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        assert setup_mod.find_stack_root(elsewhere) == root.resolve()

    def test_compose_file_prefers_the_operator_stack(self, tmp_path):
        public = _make_checkout(tmp_path / "public")
        operator = _make_checkout(tmp_path / "operator", operator=True)
        assert setup_mod.compose_file_for(public).name == "docker-compose.consumer.yml"
        assert setup_mod.compose_file_for(operator).name == "docker-compose.local.yml"


class TestProvisionStackDb:
    @pytest.fixture
    def values(self):
        return {
            "local_postgres_password": "p@ss/word",
            "grafana_password": "g",
            "poindexter_secret_key": "k",
            "compose_project_name": "poindexter",
        }

    def _patch(self, monkeypatch, *, wait_result=(True, "PostgreSQL 16.4")):
        calls: list[dict] = []

        def fake_run(cmd, **kwargs):
            calls.append({"cmd": cmd, **kwargs})
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(setup_mod, "_docker_available", lambda: (True, "27.0"))
        monkeypatch.setattr(setup_mod, "_legacy_auto_container_exists", lambda: False)
        monkeypatch.setattr(setup_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(setup_mod, "_wait_for_postgres", lambda dsn, timeout: wait_result)
        return calls

    def test_starts_postgres_local_in_the_stack_project(self, tmp_path, monkeypatch, values):
        root = _make_checkout(tmp_path / "poindexter")
        monkeypatch.delenv("POSTGRES_HOST_PORT", raising=False)
        calls = self._patch(monkeypatch)

        dsn = setup_mod._provision_stack_db(root, values)

        up = calls[0]
        assert up["cmd"] == [
            "docker", "compose", "-p", "poindexter",
            "-f", str(root / "docker-compose.consumer.yml"),
            "up", "-d", "postgres-local",
        ]
        assert up["cwd"] == root
        # Same env start-stack.sh exports: every bootstrap key, uppercased.
        assert up["env"]["LOCAL_POSTGRES_PASSWORD"] == "p@ss/word"
        assert up["env"]["POINDEXTER_SECRET_KEY"] == "k"
        assert up["env"]["COMPOSE_PROJECT_NAME"] == "poindexter"
        # The DSN is the stack's port, with the password URL-quoted.
        assert dsn == "postgresql://poindexter:p%40ss%2Fword@localhost:5433/poindexter_brain"

    def test_password_mismatch_names_the_way_out(self, tmp_path, monkeypatch, values):
        """An existing volume keeps the password it was initialised with."""
        root = _make_checkout(tmp_path / "poindexter")
        self._patch(
            monkeypatch,
            wait_result=(False, "InvalidPasswordError: password authentication failed for user"),
        )
        with pytest.raises(click.ClickException) as exc:
            setup_mod._provision_stack_db(root, values)
        msg = exc.value.message
        assert "bootstrap.toml" in msg
        assert "down -v" in msg

    def test_compose_failure_is_loud(self, tmp_path, monkeypatch, values):
        root = _make_checkout(tmp_path / "poindexter")
        self._patch(monkeypatch)
        monkeypatch.setattr(
            setup_mod.subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr=""),
        )
        with pytest.raises(click.ClickException, match="failed"):
            setup_mod._provision_stack_db(root, values)

    def test_no_docker_is_loud(self, tmp_path, monkeypatch, values):
        monkeypatch.setattr(setup_mod, "_docker_available", lambda: (False, "docker binary not on PATH"))
        with pytest.raises(click.ClickException, match="Docker is not available"):
            setup_mod._provision_stack_db(_make_checkout(tmp_path / "p"), values)


class TestSetupAutoCommand:
    def _fake_bootstrap(self, *, exists=False, existing=None):
        written: dict = {}

        def write(values):
            written.update(values)
            written["_order"] = list(values)
            return Path("/tmp/bootstrap.toml")

        return SimpleNamespace(
            BOOTSTRAP_FILE=Path("/tmp/bootstrap.toml"),
            bootstrap_file_exists=lambda: exists,
            get_all_bootstrap_values=lambda: dict(existing or {}),
            write_bootstrap_toml=write,
        ), written

    def test_auto_writes_the_stack_dsn_and_provisions_oauth_with_the_stack_key(
        self, tmp_path, monkeypatch,
    ):
        root = _make_checkout(tmp_path / "poindexter")
        fake_bootstrap, written = self._fake_bootstrap()
        seen_key: dict = {}

        async def fake_oauth(dsn):
            seen_key["key"] = os.environ.get("POINDEXTER_SECRET_KEY")
            seen_key["dsn"] = dsn
            return "pdx_client", "secret"

        monkeypatch.delenv("POINDEXTER_SECRET_KEY", raising=False)
        with patch.object(setup_mod, "_import_bootstrap", return_value=fake_bootstrap), \
             patch.object(setup_mod, "find_stack_root", return_value=root), \
             patch.object(setup_mod, "_provision_stack_db", return_value="postgresql://poindexter:x@localhost:5433/poindexter_brain") as prov, \
             patch.object(setup_mod, "_test_db_connection", AsyncMock(return_value=(True, "PostgreSQL 16"))), \
             patch.object(setup_mod, "_run_migrations", AsyncMock(return_value=(True, "applied 80"))), \
             patch.object(setup_mod, "_sync_compose_project_setting", AsyncMock(return_value=False)) as sync, \
             patch.object(setup_mod, "_configured_pull_command", AsyncMock(return_value="ollama pull a:1 && ollama pull b")), \
             patch.object(setup_mod, "_provision_initial_oauth_client", fake_oauth):
            result = CliRunner().invoke(setup_mod.setup_command, ["--auto"])

        assert result.exit_code == 0, result.output
        prov.assert_called_once()
        assert written["database_url"] == "postgresql://poindexter:x@localhost:5433/poindexter_brain"
        assert written["_order"][0] == "database_url"
        assert written["poindexter_secret_key"]
        assert written["compose_project_name"]
        # The OAuth client is encrypted with the key the stack will read.
        assert seen_key["key"] == written["poindexter_secret_key"]
        assert seen_key["dsn"] == written["database_url"]
        assert "start-stack.sh up -d" in result.output
        # The pull list comes from the database's own settings.
        assert "ollama pull a:1 && ollama pull b" in result.output
        # The brain's drift probe reads the project name from app_settings.
        assert sync.await_args.args == (written["database_url"], written["compose_project_name"])

    def test_force_keeps_the_existing_secrets(self, tmp_path, monkeypatch):
        monkeypatch.delenv("POSTGRES_HOST_PORT", raising=False)
        root = _make_checkout(tmp_path / "poindexter")
        existing = {
            "database_url": "postgresql://poindexter:old@localhost:5434/poindexter_brain",
            "local_postgres_password": "old",
            "poindexter_secret_key": "keep-me",
        }
        fake_bootstrap, written = self._fake_bootstrap(exists=True, existing=existing)
        with patch.object(setup_mod, "_import_bootstrap", return_value=fake_bootstrap), \
             patch.object(setup_mod, "find_stack_root", return_value=root), \
             patch.object(setup_mod, "_provision_stack_db", side_effect=lambda r, v: setup_mod.stack_database_url(v)), \
             patch.object(setup_mod, "_test_db_connection", AsyncMock(return_value=(True, "ok"))), \
             patch.object(setup_mod, "_run_migrations", AsyncMock(return_value=(True, "ok"))), \
             patch.object(setup_mod, "_sync_compose_project_setting", AsyncMock(return_value=False)), \
             patch.object(setup_mod, "_configured_pull_command", AsyncMock(return_value="")), \
             patch.object(setup_mod, "_provision_initial_oauth_client", AsyncMock(return_value=("a", "b"))):
            result = CliRunner().invoke(setup_mod.setup_command, ["--auto", "--force"])

        assert result.exit_code == 0, result.output
        assert written["local_postgres_password"] == "old"
        assert written["poindexter_secret_key"] == "keep-me"
        # The legacy 5434 DSN is replaced by the stack's own.
        assert "@localhost:5433/" in written["database_url"]

    def test_existing_file_without_force_changes_nothing(self):
        fake_bootstrap, written = self._fake_bootstrap(exists=True)
        with patch.object(setup_mod, "_import_bootstrap", return_value=fake_bootstrap), \
             patch.object(setup_mod, "_provision_stack_db") as prov:
            result = CliRunner().invoke(setup_mod.setup_command, ["--auto"])
        assert result.exit_code == 1
        prov.assert_not_called()
        assert not written

    def test_auto_outside_a_checkout_names_the_alternatives(self):
        fake_bootstrap, _ = self._fake_bootstrap()
        with patch.object(setup_mod, "_import_bootstrap", return_value=fake_bootstrap), \
             patch.object(setup_mod, "find_stack_root", return_value=None):
            result = CliRunner().invoke(setup_mod.setup_command, ["--auto"])
        assert result.exit_code != 0
        assert "--db-url" in result.output
        assert "git clone" in result.output


def test_legacy_container_is_only_advised_about(tmp_path, monkeypatch):
    """The old 5434 container is pointed at, never removed — it may hold data."""
    root = _make_checkout(tmp_path / "poindexter")
    commands: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(setup_mod, "_docker_available", lambda: (True, "27.0"))
    monkeypatch.setattr(setup_mod, "_legacy_auto_container_exists", lambda: True)
    monkeypatch.setattr(setup_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(setup_mod, "_wait_for_postgres", lambda dsn, timeout: (True, "ok"))
    echoed = MagicMock()
    monkeypatch.setattr(setup_mod.click, "secho", echoed)

    setup_mod._provision_stack_db(root, {
        "local_postgres_password": "p", "compose_project_name": "poindexter",
    })

    assert not any("rm" in c for c in commands)
    assert any(setup_mod._LEGACY_AUTO_CONTAINER in str(call) for call in echoed.call_args_list)


class _RecordingConn:
    def __init__(self, status: str):
        self.status = status
        self.calls: list[tuple] = []

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return self.status

    async def close(self):
        return None


class TestComposeProjectSettingSync:
    """bootstrap.toml and the brain's drift probe must name the same project."""

    def test_updates_a_differing_row(self):
        import asyncio

        conn = _RecordingConn("UPDATE 1")
        with patch("asyncpg.connect", AsyncMock(return_value=conn)):
            changed = asyncio.run(setup_mod._sync_compose_project_setting("dsn", "my-stack"))
        assert changed is True
        sql, args = conn.calls[0]
        assert "compose_project_name" in sql and "IS DISTINCT FROM" in sql
        assert args == ("my-stack",)

    def test_matching_row_is_a_no_op(self):
        import asyncio

        with patch("asyncpg.connect", AsyncMock(return_value=_RecordingConn("UPDATE 0"))):
            assert asyncio.run(setup_mod._sync_compose_project_setting("dsn", "poindexter")) is False

    def test_bootstrap_and_app_setting_defaults_agree(self):
        """Both default to the same name, so a default install needs no sync."""
        from poindexter.services.settings_defaults import DEFAULTS

        assert DEFAULTS["compose_project_name"] == setup_mod._DEFAULT_COMPOSE_PROJECT


class TestConfiguredPullCommand:
    def test_reads_the_pipeline_roles_from_the_database(self):
        import asyncio

        from poindexter.services.required_models import PIPELINE_MODEL_KEYS

        class _Conn:
            def __init__(self):
                self.args = None

            async def fetch(self, sql, keys):
                self.args = keys
                return [
                    {"key": "pipeline_writer_model", "value": "ollama/my-writer:7b"},
                    {"key": "pipeline_critic_model", "value": "ollama/phi4:14b"},
                    {"key": "embedding_model", "value": "nomic-embed-text"},
                ]

            async def close(self):
                return None

        conn = _Conn()
        with patch("asyncpg.connect", AsyncMock(return_value=conn)):
            cmd = asyncio.run(setup_mod._configured_pull_command("dsn"))
        assert conn.args == list(PIPELINE_MODEL_KEYS)
        assert cmd == (
            "ollama pull my-writer:7b && ollama pull phi4:14b && ollama pull nomic-embed-text"
        )
