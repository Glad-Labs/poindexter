"""Poindexter plugin contracts — the six Protocols that every feature's
implementations hang off of.

Every plugin registers via ``setuptools`` entry_points and is discovered
at runtime via ``importlib.metadata.entry_points``. There is no custom
registry, no pkgutil scan, no decorators — the same pattern pytest,
click, and flask use.

## The six Protocols

- :class:`Tap <.tap.Tap>` — data ingestion (yields ``Document`` instances).
- :class:`Probe <.probe.Probe>` — state checking (returns ``ProbeResult``).
- :class:`Job <.job.Job>` — scheduled maintenance (returns ``JobResult``).
- :class:`Stage <.stage.Stage>` — pipeline transformer. Specializations:
  :class:`Reviewer <.stage.Reviewer>`, :class:`Adapter <.stage.Adapter>`,
  :class:`Provider <.stage.Provider>`.
- :class:`Pack <.pack.Pack>` — bundled prompts / styles / configs.
  Distributed as pypi packages; loaded into the DB on install.
- :class:`LLMProvider <.llm_provider.LLMProvider>` — inference backend.
  Core ships Ollama + OpenAI-compat; community wraps paid vendors.

## Registering a plugin

Each plugin package declares its contribution in ``pyproject.toml``:

.. code:: toml

    [project.entry-points."poindexter.taps"]
    gitea = "poindexter_tap_gitea:GiteaTap"

    [project.entry-points."poindexter.llm_providers"]
    openai_compat = "poindexter_llm_openai_compat:OpenAICompatProvider"

Poindexter discovers them with:

.. code:: python

    from poindexter.plugins.registry import get_taps
    for tap in get_taps():
        await tap.extract(pool, config)

## Per-install configuration

Plugin-specific config lives in ``app_settings`` under
``plugin.<type>.<name>`` as a JSON blob. See
:class:`PluginConfig <.config.PluginConfig>`.

See also ``docs/architecture/plugin-architecture.md`` for the full
design.
"""

from .audio_gen_provider import AudioGenProvider, AudioGenResult, AudioKind
from .caption_provider import CaptionProvider, CaptionResult, CaptionSegment
from .config import PluginConfig
from .image_provider import ImageProvider, ImageResult
from .job import Job, JobResult
from .llm_provider import Completion, LLMProvider, Token
from .media_compositor import (
    CompositionRequest,
    CompositionResult,
    CompositionScene,
    MediaCompositor,
)
from .pack import Pack
from .probe import Probe, ProbeResult
from .publish_adapter import PublishAdapter, PublishResult
from .topic_source import DiscoveredTopic, TopicSource
from .tts_provider import TTSProvider, TTSResult
from .video_provider import VideoProvider, VideoResult
from .registry import (
    ENTRY_POINT_GROUPS,
    get_adapters,
    get_audio_gen_providers,
    get_caption_providers,
    get_core_samples,
    get_image_providers,
    get_jobs,
    get_llm_providers,
    get_media_compositors,
    get_packs,
    get_probes,
    get_providers,
    get_publish_adapters,
    get_reviewers,
    get_stages,
    get_taps,
    get_topic_sources,
    get_tts_providers,
    get_video_providers,
)
from .scheduler import PluginScheduler
from .secrets import (
    SecretsError,
    demote_secret,
    ensure_pgcrypto,
    get_secret,
    is_encrypted,
    migrate_plaintext_secrets,
    rotate_key,
    set_secret,
)
from .stage import Adapter, Provider, Reviewer, Stage, StageResult
from .stage_runner import (
    DEFAULT_STAGE_ORDER,
    StageRunner,
    StageRunRecord,
    StageRunSummary,
    load_stage_order,
)
from .tap import Document, Tap

__all__ = [
    # Protocols
    "Tap",
    "Probe",
    "Job",
    "Stage",
    "Reviewer",
    "Adapter",
    "Provider",
    "Pack",
    "LLMProvider",
    "AudioGenProvider",
    "CaptionProvider",
    "ImageProvider",
    "MediaCompositor",
    "PublishAdapter",
    "TopicSource",
    "TTSProvider",
    "VideoProvider",
    # Dataclasses
    "Document",
    "ProbeResult",
    "JobResult",
    "StageResult",
    "Completion",
    "Token",
    "AudioGenResult",
    "AudioKind",
    "CaptionResult",
    "CaptionSegment",
    "CompositionRequest",
    "CompositionResult",
    "CompositionScene",
    "DiscoveredTopic",
    "ImageResult",
    "PublishResult",
    "TTSResult",
    "VideoResult",
    # Config + registry
    "PluginConfig",
    "PluginScheduler",
    "StageRunner",
    "StageRunRecord",
    "StageRunSummary",
    "DEFAULT_STAGE_ORDER",
    "load_stage_order",
    # Secrets
    "SecretsError",
    "demote_secret",
    "ensure_pgcrypto",
    "get_secret",
    "is_encrypted",
    "migrate_plaintext_secrets",
    "rotate_key",
    "set_secret",
    "ENTRY_POINT_GROUPS",
    "get_taps",
    "get_probes",
    "get_jobs",
    "get_stages",
    "get_reviewers",
    "get_adapters",
    "get_providers",
    "get_packs",
    "get_llm_providers",
    "get_topic_sources",
    "get_image_providers",
    "get_audio_gen_providers",
    "get_video_providers",
    "get_tts_providers",
    "get_caption_providers",
    "get_publish_adapters",
    "get_media_compositors",
    "get_core_samples",
]
