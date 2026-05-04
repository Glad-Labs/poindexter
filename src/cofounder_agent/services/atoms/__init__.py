"""Atoms — atomic, single-action building blocks for pipeline templates.

Each atom is one observable outcome, one failure mode, one model/tool
call (or one programmatic computation), no internal conditional branches.
Branching becomes graph edges in the templates that compose them, not
internal logic.

Phase 3 of the dynamic-pipeline-composition spec — see
``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``.

What lives here:

- Per-atom modules (e.g. ``narrate_bundle.py``) — pure async functions
  that match the LangGraph node signature: ``async def(state: dict)
  -> dict``. Return only the diff to merge into state, never the
  full state.
- ``AtomMeta`` dataclass instances exposed as module-level ``ATOM_META``
  — declares I/O schema, capability tier, retry policy, version, etc.
  Used by the architect-LLM (Phase 4) to reason about composition.

What stays out:

- Coarse stages — those still live under ``services/stages/`` and run
  via the identity adapter (``template_runner.make_stage_node``) until
  Phase 3 decomposes each one into atoms here. Migration is incremental.
- Provider implementations — those stay under their existing Protocol
  homes (``plugins/llm_provider.py``, ``plugins/image_provider.py``,
  etc.). Atoms call into providers via the capability router; they
  don't bake in concrete model names.
"""

from __future__ import annotations

__all__ = [
    "narrate_bundle",
]
