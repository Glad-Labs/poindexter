import importlib

# Avoid importing heavy Google libs during tests by pre-stubbing them
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


def test_orchestrator_initializes():
    # Import module and patch its dependencies via context managers
    if "agents.content_agent.orchestrator" in list(_sys.modules.keys()):
        del _sys.modules["agents.content_agent.orchestrator"]
    try:
        module = importlib.import_module("agents.content_agent.orchestrator")
    except ModuleNotFoundError:
        pytest.skip("agents.content_agent not importable in this environment")

    with (
        patch.object(module, "FirestoreClient") as MockFirestore,
        patch.object(module, "setup_logging") as mock_setup_logging,
        patch.object(module, "StrapiClient") as MockStrapi,
        patch.object(module, "LLMClient") as MockLLM,
        patch.object(module, "PexelsClient") as MockPexels,
        patch.object(module, "GCSClient") as MockGCS,
        patch.object(module, "ResearchAgent") as MockResearch,
        patch.object(module, "SummarizerAgent") as MockSummarizer,
        patch.object(module, "CreativeAgent") as MockCreative,
        patch.object(module, "ImageAgent") as MockImageAgent,
        patch.object(module, "QAAgent") as MockQAAgent,
        patch.object(module, "PublishingAgent") as MockPublishingAgent,
        patch.object(module, "PubSubClient") as MockPubSub,
        patch.object(module, "config") as mock_config,
        patch.object(
            module,
            "load_prompts_from_file",
            return_value={"summarize_research_data": "", "summarize_previous_draft": ""},
        ),
    ):
        mock_config.PROMPTS_PATH = "dummy/prompts.json"
        mock_config.GCP_PROJECT_ID = "proj"
        mock_config.PUBSUB_TOPIC = "topic"
        mock_config.PUBSUB_SUBSCRIPTION = "sub"

        orch = module.Orchestrator()

        # Assertions: clients and agents constructed
        MockFirestore.assert_called_once()
        mock_setup_logging.assert_called()
        MockStrapi.assert_called_once()
        MockLLM.assert_called_once()
        MockPexels.assert_called_once()
        MockGCS.assert_called_once()
        MockResearch.assert_called_once()
        MockSummarizer.assert_called_once()
        MockCreative.assert_called_once()
        MockImageAgent.assert_called_once()
        MockQAAgent.assert_called_once()
        MockPublishingAgent.assert_called_once()

        MockPubSub.assert_called()
        _, kwargs = MockPubSub.call_args
        assert kwargs.get("orchestrator") is orch


def test_start_pubsub_listener_starts_thread():
    if "agents.content_agent.orchestrator" in list(_sys.modules.keys()):
        del _sys.modules["agents.content_agent.orchestrator"]
    try:
        module = importlib.import_module("agents.content_agent.orchestrator")
    except ModuleNotFoundError:
        pytest.skip("agents.content_agent not importable in this environment")

    with patch.object(module, "config") as mock_config:
        mock_config.GCP_PROJECT_ID = "proj"
        mock_config.PUBSUB_TOPIC = "topic"
        mock_config.PUBSUB_SUBSCRIPTION = "sub"

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
            patch.object(module, "PubSubClient") as MockPubSub,
        ):
            orch = module.Orchestrator()

        with patch("agents.content_agent.orchestrator.threading.Thread") as MockThread:
            orch.start_pubsub_listener()
            MockThread.assert_called()
            _, kwargs = MockThread.call_args
            assert kwargs.get("daemon") is True
