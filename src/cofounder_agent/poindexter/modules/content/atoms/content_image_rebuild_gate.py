"""content.image_rebuild_gate — fail-loud slot gate for the image_rebuild graph.

Faithful port of ``ImageRebuildService._gate`` semantics:

- Any slot that produced NOTHING (inline url None / featured '') always
  fails — an empty slot cannot be persisted, even with allow_stock.
- Otherwise, any slot that fell back to stock (Pexels) fails unless the
  operator opted in with ``--allow-stock`` (state ``allow_stock``, seeded
  from the rebuild task's metadata by the entry atom).

"Fell back to stock" is the inverse of an INTENDED-SOURCE allowlist, not
``source != "image_gen"``. A ``[SCREENSHOT: …]`` slot resolves to
``source="screenshot"`` (poindexter#1002) and is exactly what the operator
asked for — the marker names a specific dashboard, and no amount of diffusion
would satisfy it. Gating it as though image-gen had been unavailable would
abort every rebuild of a draft containing a screenshot marker, and
``--allow-stock`` would then "accept" it while logging a stock downgrade that
never happened.

On violation it RAISES, which fails the rebuild task with the exact
operator-facing detail (`poindexter tasks get <id>` surfaces it) and — the
load-bearing property — leaves the target draft byte-for-byte unchanged,
because ``content.persist_draft_images`` (the only writer) never runs.
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

#: Sources that mean "the slot got what it asked for". Everything else is a
#: stock fallback and needs ``--allow-stock``. ``screenshot`` is here because
#: a [SCREENSHOT: target] marker names one specific surface — capturing it is
#: success, not a degraded substitute for a diffusion render.
_INTENDED_SOURCES = frozenset({"image_gen", "screenshot"})

ATOM_META = AtomMeta(
    name="content.image_rebuild_gate",
    type="atom",
    version="1.0.0",
    description=(
        "Fail-loud gate over rebuilt image slots: empty slots always fail; "
        "stock-fallback slots fail unless allow_stock (image_gen + screenshot "
        "both count as intended). Raising here halts the "
        "graph before any write, so the target draft stays unchanged."
    ),
    inputs=(
        FieldSpec(name="image_results", type="list", description="[{num, url, alt_text, source}, ...]"),
        FieldSpec(name="featured_image_url", type="str", description="rebuilt featured URL ('' = none)"),
        FieldSpec(name="featured_source", type="str", description="image_gen | screenshot | pexels | none"),
        FieldSpec(name="allow_stock", type="bool", description="operator opt-in to stock fallback", required=False),
    ),
    outputs=(),
    requires=("image_results",),
    produces=(),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=(),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    image_results = state.get("image_results") or []
    featured_url = state.get("featured_image_url") or ""
    featured_source = state.get("featured_source") or "none"
    allow_stock = bool(state.get("allow_stock", False))

    empty = [f"inline:{r.get('num')}" for r in image_results if not r.get("url")]
    if not featured_url:
        empty.append("featured")
    if empty:
        raise RuntimeError(
            f"image generation produced nothing for {len(empty)} slot(s): "
            f"{', '.join(empty)}. Check the image-gen server "
            "(image_gen_server_url); draft unchanged."
        )

    stock = [
        f"inline:{r.get('num')}"
        for r in image_results
        if r.get("source") not in _INTENDED_SOURCES
    ]
    if featured_source not in _INTENDED_SOURCES:
        stock.append("featured")
    if stock and not allow_stock:
        raise RuntimeError(
            f"image-gen unavailable for {len(stock)} slot(s): "
            f"{', '.join(stock)}. Refusing Pexels stock fallback "
            "(pass --allow-stock to accept). Draft unchanged."
        )

    logger.info(
        "[content.image_rebuild_gate] pass — %d inline + featured (%s), stock_slots=%s",
        len(image_results), featured_source, stock or "none",
    )
    return {}


__all__ = ["ATOM_META", "run"]
