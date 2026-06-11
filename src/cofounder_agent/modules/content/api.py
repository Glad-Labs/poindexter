"""Public surface of the ``content`` module.

Substrate code imports content symbols from **here only** — never directly
from internal submodules (``modules.content.stages.*``,
``modules.content.atoms.*``, etc.).  This single file is the planned
thin-adapter boundary described in CLAUDE.md and tracked as
Glad-Labs/poindexter#712.

Rerouted imports (10 executable imports across 8 substrate files):

1. ``main.py`` → ``UnifiedQualityService`` from ``quality_service``
2. ``services/post_pipeline_actions.py`` → ``evaluate`` (gate evaluate) from
   ``auto_publish_gate``
3. ``services/post_pipeline_actions.py`` → ``auto_publish_task``,
   ``get_auto_publish_threshold`` from ``auto_publish``
4. ``services/post_pipeline_actions.py`` → ``MultiModelQA`` from
   ``multi_model_qa``
5. ``services/publish_service.py`` → ``record_post_approve_metrics`` from
   ``auto_publish_gate``
6. ``services/deepeval_rails.py`` → ``content_validator`` module (whole module,
   aliased as ``cv``); also uses pattern constants directly on the module object
7. ``services/guardrails_rails.py`` → ``content_validator`` module (whole module,
   aliased as ``cv``); also uses pattern constants directly on the module object
8. ``services/research_context.py`` → ``InternalLinkCoherenceFilter``,
   ``LinkCandidate`` from ``internal_link_coherence``
9. ``services/topic_proposal_service.py`` → ``build_topic_decision_artifact``
   from ``stages.topic_decision_gate``
10. ``services/pipeline_templates/__init__.py`` → ``narrate_bundle`` module
    (whole module, aliased as ``_narrate_atom``)

Out-of-scope — 3 string-path registries that reference content paths as
**strings** for ``importlib.import_module``/walk-root discovery.  These
cannot be rerouted through ``api.py`` because they use dynamic imports at
runtime, not Python import statements:

- ``plugins/registry.py`` ``_SAMPLES`` → 14 ``modules.content.stages.*`` paths
- ``services/atom_registry.py`` → ``modules.content.atoms`` walk-root
- ``services/http_client.py`` ``WIRED_HTTP_CLIENT_MODULES`` → 2 content paths

Rerouting those would require every content stage/atom to have a canonical
alias re-exported here AND the registries to be updated to use string paths
pointing into this file — a larger refactor tracked separately.

IMPORTANT: Do NOT import from substrate (``services.*``, ``plugins.*``, etc.)
in this file.  Content-to-substrate calls happen through the DI seams already
in place (constructor injection, context dict, etc.).  Circular imports are
prevented by keeping this file a pure re-export shim.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# quality_service
# ---------------------------------------------------------------------------
from modules.content.quality_service import (
    UnifiedQualityService as UnifiedQualityService,
)

# ---------------------------------------------------------------------------
# auto_publish_gate
# ---------------------------------------------------------------------------
from modules.content.auto_publish_gate import (
    AutoPublishDecision as AutoPublishDecision,
    evaluate as evaluate_auto_publish_gate,
    record_post_approve_metrics as record_post_approve_metrics,
)

# ---------------------------------------------------------------------------
# auto_publish
# ---------------------------------------------------------------------------
from modules.content.auto_publish import (
    auto_publish_task as auto_publish_task,
    get_auto_publish_threshold as get_auto_publish_threshold,
)

# ---------------------------------------------------------------------------
# multi_model_qa
# ---------------------------------------------------------------------------
from modules.content.multi_model_qa import (
    MultiModelQA as MultiModelQA,
    ReviewerResult as ReviewerResult,
    MultiModelResult as MultiModelResult,
    format_qa_feedback_from_reviews as format_qa_feedback_from_reviews,
)

# ---------------------------------------------------------------------------
# content_validator (also used as a whole-module alias ``cv``)
# ---------------------------------------------------------------------------
from modules.content import content_validator  # noqa: F401 — whole-module alias

from modules.content.content_validator import (
    validate_content as validate_content,
    ValidationResult as ValidationResult,
    ValidationIssue as ValidationIssue,
)

# ---------------------------------------------------------------------------
# internal_link_coherence
# ---------------------------------------------------------------------------
from modules.content.internal_link_coherence import (
    InternalLinkCoherenceFilter as InternalLinkCoherenceFilter,
    LinkCandidate as LinkCandidate,
)

# ---------------------------------------------------------------------------
# stages.topic_decision_gate
# ---------------------------------------------------------------------------
from modules.content.stages.topic_decision_gate import (
    build_topic_decision_artifact as build_topic_decision_artifact,
)

# ---------------------------------------------------------------------------
# atoms.narrate_bundle (also used as a whole-module alias ``_narrate_atom``)
# ---------------------------------------------------------------------------
from modules.content.atoms import narrate_bundle  # noqa: F401 — whole-module alias

__all__ = [
    # quality_service
    "UnifiedQualityService",
    # auto_publish_gate
    "AutoPublishDecision",
    "evaluate_auto_publish_gate",
    "record_post_approve_metrics",
    # auto_publish
    "auto_publish_task",
    "get_auto_publish_threshold",
    # multi_model_qa
    "MultiModelQA",
    "ReviewerResult",
    "MultiModelResult",
    "format_qa_feedback_from_reviews",
    # content_validator
    "content_validator",
    "validate_content",
    "ValidationResult",
    "ValidationIssue",
    # internal_link_coherence
    "InternalLinkCoherenceFilter",
    "LinkCandidate",
    # stages.topic_decision_gate
    "build_topic_decision_artifact",
    # atoms.narrate_bundle
    "narrate_bundle",
]
