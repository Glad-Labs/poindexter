"""Back-compat shim — the writer core moved to ``modules/content/writer_core.py``.

The ``GenerateContentStage`` writer orchestrator and its module-level helpers
were relocated to the module-root ``writer_core`` library (2026-07) so the
``content.generate_draft`` atom imports a module-root lib instead of a stage —
the last atom-independence burn-down. See ``writer_core``'s docstring for why
module root (it dispatches to the ``atoms.two_pass_writer`` sibling).

This shim re-exports the public surface so the legacy import sites keep
resolving without change:
- ``plugins/registry.py`` ``_SAMPLES`` (loads ``GenerateContentStage`` by the
  ``modules.content.stages.generate_content`` string path — a vestigial virtual
  atom nobody schedules)
- ``tests/unit/services/stages/test_generate_content.py`` + a handful of others
  that import the class or the pure helpers from this path.

New code should import from ``modules.content.writer_core`` directly.
"""
from __future__ import annotations

from modules.content.writer_core import (
    GenerateContentStage,
    _extract_caller_research,
    _min_draft_chars,
    _self_review_enabled,
    _strip_leaked_image_prompts,
)

__all__ = [
    "GenerateContentStage",
    "_extract_caller_research",
    "_min_draft_chars",
    "_self_review_enabled",
    "_strip_leaked_image_prompts",
]
