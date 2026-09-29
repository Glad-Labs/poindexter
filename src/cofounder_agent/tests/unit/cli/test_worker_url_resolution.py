"""How the CLI finds the worker, and waits for it on a fresh stack.

The README quick start ends with ``poindexter tasks create``. On a fresh
install that command died twice over:

1. ``WorkerClient`` required ``POINDEXTER_API_URL`` and nothing in the quick
   start sets it. The stack already records the worker's URL in
   ``app_settings.api_base_url`` (seeded ``http://worker:8002``, a compose
   name only containers resolve), so the CLI now reads that and rewrites a
   compose-internal host to ``localhost``.
2. ``start-stack.sh up -d`` returns before the worker finishes its ~1 minute
   of lifespan startup, so a pasted block reached ``tasks create`` early and
   hit an uncaught ``httpx.ConnectError``. ``wait_for_worker`` polls, bounded.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from click.testing import CliRunner

from poindexter.cli import _api_client as api
from poindexter.cli import tasks as tasks_cli


@pytest.fixture(autouse=True)
def _no_url_env(monkeypatch):
    monkeypatch.delenv("POINDEXTER_API_URL", raising=False)
    monkeypatch.delenv("WORKER_API_URL", raising=False)


class TestResolveBaseUrl:
    def test_env_wins(self, monkeypatch):
        monkeypatch.setenv("POINDEXTER_API_URL", "http://box:8002/")
        assert api._resolve_base_url(None) == "http://box:8002"

    def test_unset_means_ask_the_database(self):
        assert api._resolve_base_url(None) is None

    @pytest.mark.parametrize(
        ("stored", "expected"),
        [
            ("http://worker:8002", "http://localhost:8002"),
            ("http://poindexter-worker:8002", "http://localhost:8002"),
            ("http://host.docker.internal:8002", "http://localhost:8002"),
            # A real host the operator configured is used exactly as given.
            ("http://gpu-box.lan:8002", "http://gpu-box.lan:8002"),
            ("https://api.example.com", "https://api.example.com"),
        ],
    )
    def test_host_reachable_url(self, stored, expected):
        assert api.host_reachable_url(stored) == expected


class _FakeConn:
    async def close(self):
        return None


class TestBaseUrlFromSettings:
    def test_reads_api_base_url_and_rewrites_the_compose_host(self, monkeypatch):
        monkeypatch.setattr(api, "_dsn_or_none", lambda: "postgresql://u:p@localhost:5433/db")
        with patch("asyncpg.connect", AsyncMock(return_value=_FakeConn())), \
             patch("poindexter.plugins.secrets.get_secret", AsyncMock(return_value="http://worker:8002")) as gs:
            url = asyncio.run(api._base_url_from_settings())
        assert url == "http://localhost:8002"
        assert gs.await_args.args[1] == api.API_BASE_URL_KEY

    def test_no_database_names_both_remedies(self, monkeypatch):
        monkeypatch.setattr(api, "_dsn_or_none", lambda: "")
        with pytest.raises(RuntimeError, match="POINDEXTER_API_URL") as exc:
            asyncio.run(api._base_url_from_settings())
        assert "api_base_url" in str(exc.value)

    def test_empty_row_is_loud(self, monkeypatch):
        monkeypatch.setattr(api, "_dsn_or_none", lambda: "postgresql://u:p@localhost:5433/db")
        with patch("asyncpg.connect", AsyncMock(return_value=_FakeConn())), \
             patch("poindexter.plugins.secrets.get_secret", AsyncMock(return_value="")):
            with pytest.raises(RuntimeError, match="empty"):
                asyncio.run(api._base_url_from_settings())

    def test_unreachable_database_is_a_connectivity_error(self, monkeypatch):
        monkeypatch.setattr(api, "_dsn_or_none", lambda: "postgresql://u:p@localhost:5433/db")
        with patch("asyncpg.connect", AsyncMock(side_effect=ConnectionRefusedError("refused"))):
            with pytest.raises(api.CredentialStoreUnreachable, match="docker ps"):
                asyncio.run(api._base_url_from_settings())


def _client_factory(handler):
    """``httpx.AsyncClient`` stand-in that routes through ``handler``."""
    real = httpx.AsyncClient

    def make(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    return make


class TestWaitForWorker:
    def test_waits_through_refused_connections(self, monkeypatch, capsys):
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(200, json={"status": "healthy"})

        monkeypatch.setattr(api.httpx, "AsyncClient", _client_factory(handler))
        url = asyncio.run(api.wait_for_worker(30, base_url="http://localhost:8002", poll_s=0))
        assert url == "http://localhost:8002"
        assert attempts["n"] == 3
        # Announced once, on stderr (stdout of a CLI can be data).
        err = capsys.readouterr().err
        assert err.count("Waiting for the worker") == 1

    def test_any_http_answer_counts(self, monkeypatch):
        """A 503 mid-startup is still an answer: the real call reports it."""
        monkeypatch.setattr(
            api.httpx, "AsyncClient",
            _client_factory(lambda request: httpx.Response(503)),
        )
        assert asyncio.run(api.wait_for_worker(0, base_url="http://w:1", poll_s=0)) == "http://w:1"

    def test_gives_up_with_a_pointer(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        monkeypatch.setattr(api.httpx, "AsyncClient", _client_factory(handler))
        with pytest.raises(RuntimeError, match="docker logs poindexter-worker"):
            asyncio.run(api.wait_for_worker(0, base_url="http://localhost:8002", poll_s=0))

    def test_resolves_from_settings_when_env_is_unset(self, monkeypatch):
        monkeypatch.setattr(api, "_base_url_from_settings", AsyncMock(return_value="http://localhost:8002"))
        monkeypatch.setattr(
            api.httpx, "AsyncClient",
            _client_factory(lambda request: httpx.Response(200)),
        )
        assert asyncio.run(api.wait_for_worker(5, poll_s=0)) == "http://localhost:8002"


class _FakeWorkerClient:
    seen_base_url: str | None = None

    def __init__(self, base_url=None, **_kw):
        type(self).seen_base_url = base_url

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def post(self, path, json=None):
        return httpx.Response(201, json={"id": "0123456789abcdef", "status": "pending"})

    async def json_or_raise(self, resp):
        return resp.json()


class TestTasksCreateWaitsForTheWorker:
    def test_create_uses_the_waited_url(self, monkeypatch):
        waited = AsyncMock(return_value="http://localhost:8002")
        monkeypatch.setattr(tasks_cli, "wait_for_worker", waited)
        monkeypatch.setattr(tasks_cli, "WorkerClient", _FakeWorkerClient)

        result = CliRunner().invoke(tasks_cli.tasks_group, ["create", "Why Docker changed everything"])

        assert result.exit_code == 0, result.output
        assert "Created: 0123456789abcdef" in result.output
        assert waited.await_args.args[0] == 180  # the default grace
        assert _FakeWorkerClient.seen_base_url == "http://localhost:8002"

    def test_wait_can_be_disabled(self, monkeypatch):
        waited = AsyncMock(return_value="http://localhost:8002")
        monkeypatch.setattr(tasks_cli, "wait_for_worker", waited)
        monkeypatch.setattr(tasks_cli, "WorkerClient", _FakeWorkerClient)
        result = CliRunner().invoke(
            tasks_cli.tasks_group, ["create", "t", "--wait-for-worker", "0"],
        )
        assert result.exit_code == 0, result.output
        assert waited.await_args.args[0] == 0

    def test_a_transport_error_is_a_message_not_a_traceback(self, monkeypatch):
        monkeypatch.setattr(tasks_cli, "wait_for_worker", AsyncMock(return_value="http://localhost:8002"))

        class _Refusing(_FakeWorkerClient):
            async def post(self, path, json=None):
                raise httpx.ConnectError("refused")

        monkeypatch.setattr(tasks_cli, "WorkerClient", _Refusing)
        result = CliRunner().invoke(tasks_cli.tasks_group, ["create", "t"])
        assert result.exit_code == 1
        assert "Error:" in result.output
        assert "Traceback" not in result.output
