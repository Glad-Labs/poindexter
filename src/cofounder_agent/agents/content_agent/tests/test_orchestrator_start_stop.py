import importlib

# Pre-stub google modules to avoid optional deps
import sys as _sys
import types as _types
from unittest.mock import patch

import pytest

if "google" not in _sys.modules:
    _sys.modules["google"] = _types.ModuleType("google")
if "google.cloud" not in _sys.modules:
    _sys.modules["google.cloud"] = _types.ModuleType("google.cloud")
if "google.cloud.firestore" not in _sys.modules:
    _sys.modules["google.cloud.firestore"] = _types.ModuleType("google.cloud.firestore")


def test_start_and_stop_polling():
    # Ensure a fresh import context
    if "agents.content_agent.orchestrator" in list(_sys.modules.keys()):
        del _sys.modules["agents.content_agent.orchestrator"]
    try:
        module = importlib.import_module("agents.content_agent.orchestrator")
    except ModuleNotFoundError:
        pytest.skip("agents.content_agent not importable in this environment")

    with patch.object(module, "config") as mock_config:
        mock_config.GCP_PROJECT_ID = None
        mock_config.PUBSUB_TOPIC = None
        mock_config.PUBSUB_SUBSCRIPTION = None
        mock_config.PROMPTS_PATH = "dummy/prompts.json"

        with (
            patch.object(module, "FirestoreClient") as MockFS,
            patch.object(module, "setup_logging"),
            patch.object(module, "StrapiClient"),
            patch.object(module, "LLMClient"),
            patch.object(module, "PexelsClient"),
            patch.object(module, "GCSClient"),
            patch.object(module, "ResearchAgent"),
            patch.object(module, "SummarizerAgent"),
            patch.object(module, "CreativeAgent"),
            patch.object(module, "ImageAgent"),
            patch.object(module, "QAAgent"),
            patch.object(module, "PublishingAgent"),
            patch.object(
                module,
                "load_prompts_from_file",
                return_value={"summarize_research_data": "", "summarize_previous_draft": ""},
            ),
        ):
            orch = module.Orchestrator()

    # Speed up: tiny interval
    with patch.object(module, "threading") as mock_threading:
        # Fake thread that immediately returns
        class DummyThread:
            def __init__(self, target=None, args=(), daemon=False):
                self._target = target
                self._args = args
                self._daemon = daemon
                self._alive = False

            def start(self):
                self._alive = True
                # Run target once to exercise path; stop event will end quickly
                if self._target:
                    self._target(*self._args)
                self._alive = False

            def is_alive(self):
                return self._alive

            def join(self, timeout=None):
                self._alive = False

        mock_threading.Thread = DummyThread

        # Start background polling and then stop
        orch.start(poll_interval=0.01)
        orch.stop(timeout=1.0)

        # No assertions beyond not raising; ensure stop sets the event
        assert orch._stop_event.is_set()
