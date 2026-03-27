"""
Unit tests for agents/content_agent/services/image_gen_client.py — ImageGenClient

Tests focus on (torch/diffusers fully mocked — not installed in CI):
- _initialize_model(): CUDA available path, CUDA unavailable path, load failure
- generate_images(): successful generation + save, pipe=None (CUDA unavailable) path,
  exception during generation handled gracefully
"""

import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-placeholder")


# ---------------------------------------------------------------------------
# Module-level mocks for torch and diffusers (not installed)
# ---------------------------------------------------------------------------


def _patch_heavy_imports():
    """Return a context manager that stubs out torch and diffusers."""
    mock_torch = MagicMock()
    mock_diffusers = MagicMock()
    mock_pipeline_cls = MagicMock()
    mock_diffusers.StableDiffusionXLPipeline = mock_pipeline_cls

    return {
        "torch": mock_torch,
        "diffusers": mock_diffusers,
        "diffusers.StableDiffusionXLPipeline": mock_pipeline_cls,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _import_image_gen_client(torch_mock, cuda_available: bool):
    """Import ImageGenClient with mocked torch and diffusers."""
    torch_mock.cuda.is_available.return_value = cuda_available
    torch_mock.float16 = "float16"

    mock_pipeline_cls = MagicMock()
    mock_pipeline_instance = MagicMock()
    mock_pipeline_cls.from_pretrained.return_value.to.return_value = mock_pipeline_instance

    mock_diffusers = MagicMock()
    mock_diffusers.StableDiffusionXLPipeline = mock_pipeline_cls

    mock_config = MagicMock()
    mock_config.SD_NEGATIVE_PROMPT = "ugly, blurry"

    # Patch all three dependencies before importing
    with patch.dict(
        sys.modules,
        {
            "torch": torch_mock,
            "diffusers": mock_diffusers,
            "config": mock_config,
        },
    ):
        # Force reimport by removing cached module
        if "agents.content_agent.services.image_gen_client" in sys.modules:
            del sys.modules["agents.content_agent.services.image_gen_client"]

        from agents.content_agent.services.image_gen_client import ImageGenClient

        client = ImageGenClient()

    return client, mock_pipeline_cls, mock_pipeline_instance, mock_config


# ---------------------------------------------------------------------------
# _initialize_model
# ---------------------------------------------------------------------------


class TestInitializeModel:
    def test_loads_model_when_cuda_available(self):
        torch_mock = MagicMock()
        torch_mock.cuda.is_available.return_value = True
        torch_mock.float16 = "float16"

        mock_pipeline_cls = MagicMock()
        mock_pipeline_instance = MagicMock()
        mock_pipeline_cls.from_pretrained.return_value.to.return_value = mock_pipeline_instance

        mock_diffusers = MagicMock()
        mock_diffusers.StableDiffusionXLPipeline = mock_pipeline_cls

        mock_config = MagicMock()
        mock_config.SD_NEGATIVE_PROMPT = "ugly"

        if "agents.content_agent.services.image_gen_client" in sys.modules:
            del sys.modules["agents.content_agent.services.image_gen_client"]

        with patch.dict(
            sys.modules,
            {"torch": torch_mock, "diffusers": mock_diffusers, "config": mock_config},
        ):
            from agents.content_agent.services.image_gen_client import ImageGenClient

            client = ImageGenClient()

        # Pipeline should have been loaded and moved to cuda
        mock_pipeline_cls.from_pretrained.assert_called_once()
        assert client.pipe is mock_pipeline_instance

    def test_pipe_is_none_when_cuda_unavailable(self):
        torch_mock = MagicMock()
        torch_mock.cuda.is_available.return_value = False

        mock_diffusers = MagicMock()
        mock_config = MagicMock()

        if "agents.content_agent.services.image_gen_client" in sys.modules:
            del sys.modules["agents.content_agent.services.image_gen_client"]

        with patch.dict(
            sys.modules,
            {"torch": torch_mock, "diffusers": mock_diffusers, "config": mock_config},
        ):
            from agents.content_agent.services.image_gen_client import ImageGenClient

            client = ImageGenClient()

        assert client.pipe is None

    def test_pipe_is_none_when_model_load_raises(self):
        torch_mock = MagicMock()
        torch_mock.cuda.is_available.return_value = True
        torch_mock.float16 = "float16"

        mock_pipeline_cls = MagicMock()
        mock_pipeline_cls.from_pretrained.side_effect = RuntimeError("Model download failed")

        mock_diffusers = MagicMock()
        mock_diffusers.StableDiffusionXLPipeline = mock_pipeline_cls

        mock_config = MagicMock()
        mock_config.SD_NEGATIVE_PROMPT = "ugly"

        if "agents.content_agent.services.image_gen_client" in sys.modules:
            del sys.modules["agents.content_agent.services.image_gen_client"]

        with patch.dict(
            sys.modules,
            {"torch": torch_mock, "diffusers": mock_diffusers, "config": mock_config},
        ):
            from agents.content_agent.services.image_gen_client import ImageGenClient

            client = ImageGenClient()

        assert client.pipe is None


# ---------------------------------------------------------------------------
# generate_images
# ---------------------------------------------------------------------------


class TestGenerateImages:
    def _make_client_with_pipe(self):
        """Build a client with a working mock pipeline."""
        torch_mock = MagicMock()
        torch_mock.cuda.is_available.return_value = True
        torch_mock.float16 = "float16"

        mock_image = MagicMock()
        mock_pipeline_instance = MagicMock()
        mock_pipeline_instance.return_value.images = [mock_image]

        mock_pipeline_cls = MagicMock()
        mock_pipeline_cls.from_pretrained.return_value.to.return_value = mock_pipeline_instance

        mock_diffusers = MagicMock()
        mock_diffusers.StableDiffusionXLPipeline = mock_pipeline_cls

        mock_config = MagicMock()
        mock_config.SD_NEGATIVE_PROMPT = "ugly, blurry"

        if "agents.content_agent.services.image_gen_client" in sys.modules:
            del sys.modules["agents.content_agent.services.image_gen_client"]

        with patch.dict(
            sys.modules,
            {"torch": torch_mock, "diffusers": mock_diffusers, "config": mock_config},
        ):
            from agents.content_agent.services.image_gen_client import ImageGenClient

            client = ImageGenClient()

        return client, mock_image, mock_config

    def test_generates_and_saves_image_when_pipe_available(self, tmp_path):
        client, mock_image, mock_config = self._make_client_with_pipe()
        output_path = str(tmp_path / "output.jpg")

        client.generate_images("A beautiful mountain", output_path)

        mock_image.save.assert_called_once_with(output_path)

    def test_does_not_raise_when_pipe_is_none(self, tmp_path):
        """When CUDA unavailable, pipe is None — generate_images should not raise."""
        torch_mock = MagicMock()
        torch_mock.cuda.is_available.return_value = False

        mock_diffusers = MagicMock()
        mock_config = MagicMock()
        mock_config.SD_NEGATIVE_PROMPT = "ugly"

        if "agents.content_agent.services.image_gen_client" in sys.modules:
            del sys.modules["agents.content_agent.services.image_gen_client"]

        with patch.dict(
            sys.modules,
            {"torch": torch_mock, "diffusers": mock_diffusers, "config": mock_config},
        ):
            from agents.content_agent.services.image_gen_client import ImageGenClient

            client = ImageGenClient()

        output_path = str(tmp_path / "output.jpg")
        # Should handle TypeError (NoneType not callable) gracefully via except block
        client.generate_images("Any prompt", output_path)

    def test_handles_pipeline_exception_gracefully(self, tmp_path):
        """Pipeline call raises RuntimeError — should be caught, not propagated."""
        client, _, mock_config = self._make_client_with_pipe()
        # Replace pipe with one that raises during call
        client.pipe = MagicMock(side_effect=RuntimeError("CUDA OOM"))

        output_path = str(tmp_path / "output.jpg")
        # Should not raise
        client.generate_images("Prompt", output_path)
