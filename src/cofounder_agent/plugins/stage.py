"""Stage — the pipeline-transformer Protocol.

A Stage takes a ``WorkflowContext`` (mutable shared state across the
content pipeline) and transforms it. Stages chain to form a workflow:
``research → draft → assess → refine → image → publish``.

Three specialized sub-protocols for specific transformations:

- :class:`Reviewer` — produces a quality score. Examples:
  ``programmatic_validator``, ``llm_critic``, ``seo_checker``,
  ``url_verifier``.
- :class:`Adapter` — publishes the finished post to an external platform.
  Examples: ``bluesky``, ``linkedin``, ``mastodon``, ``reddit``,
  ``youtube`` (already in ``services/social_adapters/``).
- :class:`Provider` — generates media (images, audio, video). Examples:
  ``pexels``, ``sdxl``, ``ai_generation``, future ``midjourney``,
  ``flux``.

This module promotes the existing ``services/phases/base_phase.py``
contract. Phases become Stages; the terminology migrates over Phase E.

Register via ``pyproject.toml``:

.. code:: toml

    [project.entry-points."poindexter.stages"]
    research = "poindexter.stages.research:ResearchStage"

    [project.entry-points."poindexter.reviewers"]
    llm_critic = "poindexter.reviewers.llm_critic:LLMCriticReviewer"

    [project.entry-points."poindexter.adapters"]
    bluesky = "poindexter.adapters.bluesky:BlueskyAdapter"

    [project.entry-points."poindexter.providers"]
    sdxl = "poindexter.providers.sdxl:SDXLProvider"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class StageResult:
    """What a Stage returns.

    ``context_updates`` is merged into the shared ``WorkflowContext``
    by the orchestrator. ``continue_workflow=False`` halts the chain
    (use for early-exit on QA failure or publish errors).
    """

    ok: bool
    detail: str
    context_updates: dict[str, Any] = field(default_factory=dict)
    continue_workflow: bool = True
    metrics: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Stage(Protocol):
    """A pipeline step that transforms ``WorkflowContext``.

    The exact ``WorkflowContext`` type is promoted from
    ``services/phases/base_phase.py`` during Phase E. For now Stages
    receive a dict-like context to keep Phase A compatible with the
    existing Phase registry.
    """

    name: str
    description: str

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        ...


@runtime_checkable
class Reviewer(Stage, Protocol):
    """A Stage that produces a quality score.

    Reviewers are composed via ``qa_workflow_*`` configs in
    ``app_settings``. Running multiple Reviewers and combining their
    scores is the orchestrator's job, not the individual Reviewer's.

    Attributes:
        score_dimensions: Names of the score dimensions this reviewer
            produces. Used for UI + aggregation. E.g.
            ``["factuality", "clarity", "engagement"]``.
    """

    score_dimensions: list[str]


@runtime_checkable
class Adapter(Stage, Protocol):
    """A Stage that publishes the finished post to an external platform.

    Attributes:
        platform: Human-readable platform name (``"Bluesky"``,
            ``"LinkedIn"``, ``"YouTube"``).
        requires_credentials: List of ``app_settings`` keys the Adapter
            needs to function (e.g. ``["bluesky_handle",
            "bluesky_app_password"]``). The orchestrator uses this for
            pre-flight checks — skip the Adapter if any credential is
            missing, surface a single warning instead of failing mid-post.
    """

    platform: str
    requires_credentials: list[str]


@runtime_checkable
class Provider(Stage, Protocol):
    """A Stage that generates media.

    Attributes:
        media_type: ``"image"``, ``"audio"``, ``"video"``.
        model: The specific backend model/service used (e.g.
            ``"sdxl_lightning"``, ``"pexels_search"``,
            ``"flux_schnell"``).
    """

    media_type: str
    model: str
