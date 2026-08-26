"""content.inject_affiliate_links — inject curated affiliate links into the draft.

Keyword-matched, first-mention-only, capped per post. Placed in the writer block
BEFORE the QA rails so injected links flow through url_validation / qa.citations
like any other link. No-op (returns {}) when affiliate_injection_enabled=false,
there's no DB pool, no active links, or nothing matched. Real referral rows are
DB-only (poindexter affiliate add).
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec
from utils.exception_format import describe_exception
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.inject_affiliate_links",
    type="atom",
    version="1.0.0",
    description=(
        "Inject curated affiliate links (keyword match, first-mention, capped) "
        "as [text](/go/<code>) markdown links. No-op when "
        "affiliate_injection_enabled=false. Deterministic — no LLM."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft body to link"),
    ),
    outputs=(
        FieldSpec(name="content", type="str", description="body with affiliate links"),
    ),
    requires=("content",),
    produces=("content",),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=(),
    parallelizable=True,
)


def _resolve_pool(state: dict[str, Any]) -> Any:
    db = state.get("database_service")
    pool = getattr(db, "pool", None) if db is not None else None
    return pool if pool is not None else state.get("pool")


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = state.get("content") or ""
    if not content.strip():
        return {}

    sc = state.get("site_config")
    if sc is None:
        return {}
    try:
        if not sc.get_bool("affiliate_injection_enabled", False):
            return {}
        base = sc.get("affiliate_redirect_base_url", "/go") or "/go"
        cap = sc.get_int("affiliate_max_links_per_post", 3)
    except Exception:  # noqa: BLE001  # silent-ok: optional feature config read — a stub/broken SiteConfig yields no injection, never a pipeline failure
        return {}

    pool = _resolve_pool(state)
    if pool is None:
        return {}

    from modules.content.affiliate_links import (
        inject_affiliate_links,
        list_active,
        load_link_last_used,
    )

    try:
        links = await list_active(pool)
    except Exception as exc:  # noqa: BLE001 — DB blip → pass content through
        emit_finding(
            source="content.inject_affiliate_links",
            kind="affiliate_links_read_failed",
            title="affiliate list_active read failed — no links injected this run",
            body=(
                f"list_active failed: {describe_exception(exc)}. The atom passes the content through "
                "unmodified (no affiliate links injected for this post)."
            ),
            dedup_key="affiliate_links_read_failed:list_active",
        )
        return {}
    if not links:
        return {}

    last_used: dict[str, str] = {}
    if len(links) >= 2:
        try:
            last_used = await load_link_last_used(pool)
        except Exception as exc:  # noqa: BLE001  # silent-ok: LRU signal is best-effort — a fetch failure just skips tie-breaking, never fails the atom
            logger.debug("[content.inject_affiliate_links] load_link_last_used failed: %s", exc)

    new_content, injected = inject_affiliate_links(
        content, links, base=base, cap=cap, last_used=last_used,
    )
    if not injected:
        return {}
    logger.info(
        "[content.inject_affiliate_links] injected %d link(s) (task=%s): %s",
        len(injected), str(state.get("task_id") or "?")[:8], ", ".join(injected),
    )
    return {"content": new_content}


__all__ = ["ATOM_META", "run"]
