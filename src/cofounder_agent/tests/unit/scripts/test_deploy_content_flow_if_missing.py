"""``deploy_content_flow.py --if-missing`` — the fresh-install dispatcher bootstrap.

Prefect is the only dispatcher, and until this flag nothing created its
``content_generation/content-generation`` deployment on a fresh install: the
prefect-worker polled an empty queue and every queued task stayed ``pending``.
docker-compose.consumer.yml now runs this before ``prefect worker start``.

The contract that matters on a LIVE install: ``--if-missing`` changes nothing
once the deployment exists, so an operator's re-tuned cron / concurrency /
paused deployment survives every container restart; and "could not ask" is
never read as "absent" (or "present").
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from prefect.exceptions import ObjectNotFound

from scripts import deploy_content_flow as dcf

_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "scripts" / "start-stack.sh").is_file()
)


class _FakeClient:
    def __init__(self, behaviour):
        self._behaviour = behaviour
        self.asked: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def read_deployment_by_name(self, name):
        self.asked.append(name)
        if isinstance(self._behaviour, BaseException):
            raise self._behaviour
        return SimpleNamespace(name=name)


def _not_found() -> ObjectNotFound:
    return ObjectNotFound(http_exc=Exception("404"))


def test_deployment_ref_comes_from_the_flow_object():
    assert dcf.deployment_ref() == f"{dcf.content_generation_flow.name}/{dcf.DEPLOYMENT_NAME}"
    assert dcf.deployment_ref() == "content_generation/content-generation"


class TestDeploymentExists:
    def test_present(self):
        client = _FakeClient(None)
        with patch.object(dcf, "get_client", return_value=client):
            assert asyncio.run(dcf.deployment_exists()) is True
        assert client.asked == [dcf.deployment_ref()]

    def test_absent(self):
        with patch.object(dcf, "get_client", return_value=_FakeClient(_not_found())):
            assert asyncio.run(dcf.deployment_exists()) is False

    def test_could_not_ask_is_not_an_answer(self):
        """A down server must not read as 'absent' (re-register over a tuned
        deployment) nor 'present' (start a worker that never gets a run)."""
        with patch.object(dcf, "get_client", return_value=_FakeClient(ConnectionError("refused"))):
            with pytest.raises(ConnectionError):
                asyncio.run(dcf.deployment_exists())


class TestMainIfMissing:
    def _patches(self, *, exists: bool):
        deployment = SimpleNamespace(apply=AsyncMock(return_value="dep-id"))
        return (
            patch.object(dcf, "deployment_exists", AsyncMock(return_value=exists)),
            patch.object(dcf, "_resolve_setting", AsyncMock(side_effect=lambda key, default: default)),
            patch.object(dcf, "_ensure_work_pool", AsyncMock()),
            patch.object(
                dcf.content_generation_flow, "to_deployment",
                AsyncMock(return_value=deployment),
            ),
            deployment,
        )

    def test_existing_deployment_is_left_alone(self):
        exists, resolve, pool, to_dep, deployment = self._patches(exists=True)
        with exists, resolve as r, pool as p, to_dep as t:
            asyncio.run(dcf.main(if_missing=True))
        r.assert_not_awaited()
        p.assert_not_awaited()
        t.assert_not_awaited()
        deployment.apply.assert_not_awaited()

    def test_missing_deployment_is_registered(self):
        exists, resolve, pool, to_dep, deployment = self._patches(exists=False)
        with exists, resolve, pool as p, to_dep as t:
            asyncio.run(dcf.main(if_missing=True))
        p.assert_awaited_once_with(dcf.DEFAULT_WORK_POOL, dcf.DEFAULT_CONCURRENCY)
        assert t.await_args.kwargs["name"] == dcf.DEPLOYMENT_NAME
        deployment.apply.assert_awaited_once()

    def test_plain_run_always_reapplies(self):
        """Without the flag the script is the tuning roll-out: it never skips."""
        exists, resolve, pool, to_dep, deployment = self._patches(exists=True)
        with exists as e, resolve, pool, to_dep:
            asyncio.run(dcf.main())
        e.assert_not_awaited()
        deployment.apply.assert_awaited_once()


def test_cli_flag_parses():
    assert dcf._parse_args(["--if-missing"]).if_missing is True
    assert dcf._parse_args([]).if_missing is False


def test_public_stack_registers_before_polling():
    """The prefect-worker must bootstrap its deployment, then poll.

    Pinned against docker-compose.consumer.yml itself: the fresh-install
    stack is the one that had no deployment, and the order matters — polling
    first leaves the worker idle until the next restart.
    """
    compose = yaml.safe_load((_REPO_ROOT / "docker-compose.consumer.yml").read_text(encoding="utf-8"))
    command = compose["services"]["prefect-worker"]["command"]
    script = command[-1] if isinstance(command, list) else command
    register = script.find("deploy_content_flow.py --if-missing")
    poll = script.find("prefect worker start")
    assert register != -1, f"prefect-worker no longer registers its deployment: {command!r}"
    assert poll != -1, f"prefect-worker no longer starts polling: {command!r}"
    assert register < poll
    # A failed registration must not fall through to polling (&&, not ;).
    assert "&&" in script[register:poll]
