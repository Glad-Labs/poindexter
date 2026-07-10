"""Pure mapping from a template node record → (step label, honest progress %).

pct is NODE POSITION (node_index / total), never wall-clock time — the console
labels it as such. Kept pure + dependency-free so it unit-tests without the
LangGraph run and can be reused by any producer that knows (seq, total).
"""
from __future__ import annotations

from typing import Any


def content_step_pct(rec: Any, seq: int, total: int) -> tuple[str | None, int | None]:
    step = getattr(rec, "name", None) or getattr(rec, "node", None)  # TemplateRunRecord.name is the node label
    if not total or total <= 0:
        return step, None
    pct = round(100 * (seq + 1) / total)
    return step, min(99, max(1, pct))  # 1..99 while running; finish() flips to done
