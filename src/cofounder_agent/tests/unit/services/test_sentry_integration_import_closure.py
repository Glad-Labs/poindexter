"""Initialising Sentry must not import torch or the LLM-framework stacks.

sentry-sdk 2.x enables an integration for every installed library it
recognises, on top of the ``integrations=`` list, unless
``auto_enabling_integrations=False``. In the worker image the LangChain one
imports ``langchain_classic.agents`` → ``langchain_core.language_models.base``
→ transformers → torch, and sentence_transformers arrives through
langchain_classic's agent toolkits. Measured in the prefect-worker image on
2026-09-28, after importing the content flow's own closure (torch-free since
stack#4157): ``sentry_sdk.init`` took 6.0-6.3 s and raised peak RSS from
172 MB to 701 MB. With auto-enabling off it took 0.01 s, 172 → 174 MB. Every
Prefect content_generation flow run initialises Sentry, and a run starts about
every two minutes even on an empty queue. Listing the integration under
``disabled_integrations`` does not help: the SDK imports it to build the list.

``SentryIntegration.initialize`` runs in a FRESH interpreter so this process's
own ``sys.modules`` cannot mask a leak. The recorder on ``sys.meta_path`` is
armed before sentry_sdk is imported and sees import ATTEMPTS, so the check
still means something where torch is not installed (CI and the host venv): the
auto-enabling path reaches for these packages whether they exist or not.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("sentry_sdk")

BACKEND_DIR = Path(__file__).resolve().parents[3]

# The first packages the SDK's AI integrations reach for, and the ML stack
# behind them. None has any business in error tracking.
_FORBIDDEN = (
    "torch",
    "transformers",
    "sentence_transformers",
    "langchain",
    "langchain_core",
    "langchain_classic",
    "langgraph",
    "huggingface_hub",
    "openai",
    "anthropic",
)
# Imported by sentry_integration at module level, so seeing it proves the
# recorder was armed before the SDK loaded.
_POSITIVE_CONTROL = "sentry_sdk"
# SDK defaults that must stay on. AtexitIntegration flushes queued events when
# a short-lived flow-run subprocess exits; excepthook reports a crash; dedupe
# stops one exception being sent twice.
_REQUIRED_DEFAULTS = {"atexit", "excepthook", "dedupe"}


def _initialise_in_fresh_interpreter() -> dict:
    watched = [*_FORBIDDEN, _POSITIVE_CONTROL]
    code = (
        "import importlib.abc, json, sys\n"
        f"WATCHED = set({watched!r})\n"
        "seen = set()\n"
        "class _Recorder(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in WATCHED:\n"
        "            seen.add(name.split('.')[0])\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Recorder())\n"
        "class _Cfg:\n"
        "    # No live GlitchTip: nothing is captured, and init sends nothing.\n"
        "    values = {'sentry_dsn': 'http://key@127.0.0.1:9/1', 'sentry_enabled': 'true',\n"
        "              'environment': 'test'}\n"
        "    def get(self, key, default=None):\n"
        "        return self.values.get(key, default)\n"
        "    def get_float(self, key, default=0.0):\n"
        "        try:\n"
        "            return float(self.values.get(key, default))\n"
        "        except (TypeError, ValueError):\n"
        "            return default\n"
        "    def get_bool(self, key, default=False):\n"
        "        return str(self.values.get(key, default)).lower() in ('true', '1', 'yes', 'on')\n"
        "from poindexter.services.sentry_integration import SentryIntegration\n"
        "ok = SentryIntegration.initialize(None, _Cfg(), service_name='import-closure-test')\n"
        "import sentry_sdk, sentry_sdk.integrations as integrations\n"
        "auto = sorted({p.rsplit('.', 2)[-2] for p in integrations._AUTO_ENABLING_INTEGRATIONS})\n"
        "print('RESULT=' + json.dumps({'ok': ok, 'seen': sorted(seen),\n"
        "    'enabled': sorted(sentry_sdk.get_client().integrations), 'auto': auto}))\n"
    )
    env = {**os.environ, "PYTHONPATH": str(BACKEND_DIR)}
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
        env=env,
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"initialising Sentry in a fresh interpreter failed:\n{proc.stderr[-2000:]}"
    )
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT="))
    return json.loads(line[len("RESULT=") :])


@pytest.fixture(scope="module")
def result() -> dict:
    return _initialise_in_fresh_interpreter()


@pytest.mark.unit
def test_the_sdk_really_initialised(result: dict) -> None:
    assert result["ok"] is True, (
        "SentryIntegration.initialize returned False, so sentry_sdk.init never ran "
        "and the import checks below prove nothing."
    )
    assert _POSITIVE_CONTROL in result["seen"], (
        "the import recorder never saw sentry_sdk, so it was armed too late and is blind."
    )


@pytest.mark.unit
def test_initialise_attempts_no_torch_or_llm_framework_imports(result: dict) -> None:
    leaked = sorted(set(result["seen"]) & set(_FORBIDDEN))
    assert not leaked, (
        f"initialising Sentry now tries to import {leaked}. In the worker image the "
        "LangChain integration alone costs ~6 s and ~520 MB per Prefect flow run. "
        "Keep auto_enabling_integrations=False in SentryIntegration.initialize; "
        "an integration worth its cost is opted into via sentry_extra_integrations."
    )


@pytest.mark.unit
def test_no_auto_enabling_integration_is_enabled_beyond_the_core_list(result: dict) -> None:
    from poindexter.services.sentry_integration import SentryIntegration

    auto = set(result["auto"])
    # The derivation must work, or the comparison below is vacuous.
    assert {"langchain", "asyncpg", "httpx"} <= auto
    unexpected = sorted((set(result["enabled"]) & auto) - SentryIntegration.CORE_INTEGRATIONS)
    assert not unexpected, f"auto-enabled integrations slipped in: {unexpected}"


@pytest.mark.unit
def test_core_and_sdk_defaults_stay_enabled(result: dict) -> None:
    from poindexter.services.sentry_integration import SentryIntegration

    missing = sorted(
        (SentryIntegration.CORE_INTEGRATIONS | _REQUIRED_DEFAULTS) - set(result["enabled"])
    )
    assert not missing, f"integrations that must stay on are missing: {missing}"
