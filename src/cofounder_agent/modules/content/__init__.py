"""``content`` business module — blog publishing workflow.

Exposes the ``ContentModule`` class via ``content_module.ContentModule``.

Phase 3-lite (Glad-Labs/poindexter#490) proved the Module pattern with
the pipeline code left in substrate. The **incremental Phase 3** migration
(post Phase 5) now moves content-owned code into this package one piece at
a time, as the code is touched, rather than in one 100-file big-bang —
``content_validator.py`` is the first piece moved in. Generic pipeline
engine (``template_runner``, ``pipeline_architect``, ``prompt_manager``)
stays in substrate; the module *rents* it via the DB graph_def seam.
"""

from .content_module import ContentModule

__all__ = ["ContentModule"]
