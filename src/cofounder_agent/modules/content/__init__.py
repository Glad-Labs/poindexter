"""``content`` business module — blog publishing workflow.

Phase 3-lite (Glad-Labs/poindexter#490). Exposes the
``ContentModule`` class via ``content_module.ContentModule``. The
actual pipeline code (``services/content_router_service.py``,
``services/stages/*``, ``services/multi_model_qa.py``,
``services/content_validator.py``) stays in substrate for now —
Phase 3-lite proves the Module pattern without the 100-file
import-path move that the full Phase 3 would require.
"""

from .content_module import ContentModule

__all__ = ["ContentModule"]
