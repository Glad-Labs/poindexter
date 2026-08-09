"""Pipeline templates — the DB graph_def reader, and an empty factory registry.

**There are no hand-coded template factories left.** Every pipeline is a
``graph_def`` row in ``pipeline_templates``, authored as a pure-data spec under
``services/<name>_spec.py`` and compiled by
``services.pipeline_architect.build_graph_from_spec``. That is what makes a
pipeline a composition of shared, individually-improvable atoms rather than a
bespoke Python graph: fix an atom once and every graph referencing it improves.

History, because the empty ``TEMPLATES`` below looks like an oversight otherwise:

- ``canonical_blog``'s factory was deleted at the #355/#362 atom cutover.
- ``dev_diary``'s factory was deleted once its graph_def was reseeded onto the
  shared canonical atoms (``services/dev_diary_spec.py``). It had been dead code
  for some time before that — ``pipeline_use_graph_def=true`` plus an active
  graph_def row wins in ``TemplateRunner.run`` — and its deadness actively
  misled: a 2026-06-02 SEO fix was applied to the factory, never reached the
  stored spec, and dev_diary posts shipped for three months without a
  ``<meta description>`` behind a green unit test that asserted the factory had
  the node.

``TEMPLATES`` is kept as an empty dict rather than removed because it is the
injection seam several tests use (``monkeypatch.setitem(TEMPLATES, ...)``) to run
the runner against a synthetic graph. An unknown slug with no graph_def row now
raises ``KeyError`` from ``TemplateRunner.run`` — the intended fail-loud, per
``feedback_no_silent_defaults``: silently degrading to a stale hand-coded graph
is precisely the failure this module's history is made of.

Implements: Glad-Labs/poindexter#358.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from langgraph.graph import StateGraph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB reader: load_active_graph_def
# ---------------------------------------------------------------------------


async def load_active_graph_def(pool: Any, slug: str) -> dict[str, Any] | None:
    """Return the active ``graph_def`` for ``slug`` from ``pipeline_templates``,
    or ``None`` when there's no active row, the row is unreadable, or the
    spec is empty/node-less (the column default ``'{}'``).

    Best-effort: a DB error degrades to ``None`` (the runner falls back to the
    legacy Python factory) rather than failing the run.
    """
    if pool is None or not slug:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT graph_def FROM pipeline_templates "
                "WHERE slug = $1 AND active = true "
                "ORDER BY version DESC LIMIT 1",
                slug,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[pipeline_templates] load_active_graph_def(%r) failed: %s", slug, exc)
        return None
    if not row:
        return None
    raw = row["graph_def"]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[pipeline_templates] graph_def for %r is not valid JSON", slug)
            return None
    if not isinstance(raw, dict) or not raw.get("nodes"):
        return None
    return raw


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


# Intentionally empty — see the module docstring. Every pipeline is a graph_def
# row; this dict survives only as a test injection seam. Adding a factory here
# re-creates the "improvements land on a graph nobody runs" trap.
TEMPLATES: dict[str, Callable[..., StateGraph]] = {}
