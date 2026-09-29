"""The ``pipeline-bot`` service keeps the shape its idle-until-configured design needs.

``scripts/telegram-bot.py`` waits for ``telegram_bot_token`` and
``telegram_chat_id`` instead of exiting when they are unset (see
``test_telegram_bot_credential_wait.py`` for why). That only works while the
compose service around it stays as it is, and every way of "simplifying" it
brings the restart loop back or moves it somewhere quieter:

* ``restart: unless-stopped`` stays. ``on-failure`` would leave an unconfigured
  bot ``Exited (0)``, which the brain's compose-drift probe reads as drift (it
  warns, and brings the bot back up where auto-recover is on), and it would not
  bring the bot back after a Docker daemon restart. ``no`` would never bring it
  back at all.
* No compose ``profiles``. A profile is a second copy of "is Telegram
  configured?" that lives outside ``app_settings``, and ``poindexter setup``
  cannot set it (it never writes a Telegram token). The operator stack must keep
  the bot running; the consumer stack advertises it as part of the default set.
* The healthcheck stays a liveness check (``pgrep``). An idle, unconfigured bot
  has to read healthy: a check that needed a working Telegram connection would
  leave every install without Telegram ``unhealthy``, and the brain pages a
  container that stays unhealthy.
* No Telegram credential in the service's ``environment``. Config is DB-first:
  the bot reads both settings from ``app_settings``, and an env var here would be
  a second, silently-stale source for them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

CONSUMER = "docker-compose.consumer.yml"
OPERATOR = "docker-compose.local.yml"
COMPOSE_FILES = (CONSUMER, OPERATOR)
SERVICE = "pipeline-bot"


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / CONSUMER).is_file():
            return parent
    pytest.skip("compose files not reachable from the test location")


def _service(filename: str) -> dict:
    path = _repo_root() / filename
    if not path.is_file():
        # The public mirror ships the consumer stack only.
        pytest.skip(f"{filename} is not part of this tree")
    compose = yaml.safe_load(path.read_text(encoding="utf-8"))
    service = compose["services"].get(SERVICE)
    assert service, f"{filename} has no {SERVICE!r} service"
    return service


@pytest.mark.parametrize("filename", COMPOSE_FILES)
def test_runs_the_telegram_bot_script(filename: str) -> None:
    assert _service(filename)["command"] == ["python", "-u", "/opt/scripts/telegram-bot.py"]


@pytest.mark.parametrize("filename", COMPOSE_FILES)
def test_restarts_unless_stopped(filename: str) -> None:
    restart = _service(filename).get("restart")
    assert restart == "unless-stopped", (
        f"{filename}: {SERVICE} has restart={restart!r}. The bot idles until Telegram is "
        f"configured and never exits on purpose, so the policy only ever fires on a "
        f"crash or a daemon restart, and only unless-stopped covers both. on-failure "
        f"forfeits the daemon restart and turns a clean exit into a stopped container "
        f"that the drift probe flags."
    )


@pytest.mark.parametrize("filename", COMPOSE_FILES)
def test_is_not_behind_a_compose_profile(filename: str) -> None:
    profiles = _service(filename).get("profiles")
    assert not profiles, (
        f"{filename}: {SERVICE} is behind profiles={profiles!r}. Whether Telegram is "
        f"configured is a fact about app_settings, and a profile would keep a second "
        f"copy of it that `poindexter setup` cannot set. The script waits for the "
        f"settings instead, so the service stays in the default set."
    )


@pytest.mark.parametrize("filename", COMPOSE_FILES)
def test_healthcheck_is_a_liveness_probe(filename: str) -> None:
    healthcheck = _service(filename).get("healthcheck") or {}
    test = healthcheck.get("test")
    assert test == ["CMD", "pgrep", "-f", "telegram-bot.py"], (
        f"{filename}: {SERVICE} healthcheck is {test!r}. It must stay a process-liveness "
        f"check so an idle bot with no Telegram configured reports healthy."
    )


@pytest.mark.parametrize("filename", COMPOSE_FILES)
def test_telegram_credentials_are_not_environment_variables(filename: str) -> None:
    env = _service(filename).get("environment") or {}
    if isinstance(env, list):
        names = [item.split("=", 1)[0] for item in env]
    else:
        names = [str(name) for name in env]
    assert not [name for name in names if "TELEGRAM" in name.upper()], (
        f"{filename}: {SERVICE} passes a Telegram setting as an environment variable "
        f"({names}). The bot reads telegram_bot_token and telegram_chat_id from "
        f"app_settings only."
    )


def test_both_stacks_run_the_bot_the_same_way() -> None:
    """The stacks share one script, so they share its runtime contract."""
    consumer, operator = _service(CONSUMER), _service(OPERATOR)
    for field in ("command", "restart", "profiles"):
        assert consumer.get(field) == operator.get(field), (
            f"{SERVICE} {field!r} differs between {CONSUMER} and {OPERATOR}"
        )
    assert (consumer.get("healthcheck") or {}).get("test") == (
        operator.get("healthcheck") or {}
    ).get("test")
