"""ProbePromptCatalogDriftJob — surface Langfuse prompts the code no longer reads.

The prompt catalog is declared in ``skills/<pack>/<skill>/SKILL.md``
frontmatter and resolved Langfuse-first by ``services/prompt_manager.py``
(``get_prompt(name=key, label="production")`` → SKILL.md default). Nothing
auto-creates Langfuse entries — population is a manual bulk import
(``scripts/import_prompts_to_langfuse.py``) — so when a key is renamed or
deleted in the repo, its Langfuse prompt silently goes dead: the operator
can keep editing it in the UI and nothing changes in production. That
exact drift accumulated 8 dead prompts between 2026-05-14 and 2026-07-03.

This probe compares the two catalogs daily and emits ONE advisory
``prompt_catalog_drift`` finding (severity ``warn``, stable ``dedup_key``)
listing Langfuse prompt names with zero repo references — delete or
re-key them.

The INVERSE gap (repo keys absent from Langfuse) is NOT drift and is not
alerted: per the documented policy (docs/architecture/prompt-management.md
§Catalog ↔ Langfuse drift), Langfuse holds only intentional operator
overrides; repo-only keys serving their SKILL.md default is the healthy
state. The finding body reports the count for context only.

Quiet on the OSS default: when Langfuse isn't configured (no host /
public key / secret in app_settings) the probe skips without noise.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# app_settings keys (seeded in settings_defaults.py).
_ENABLED_KEY = "prompt_catalog_drift_probe_enabled"

_FINDING_KIND = "prompt_catalog_drift"

# Langfuse list endpoint pages at most 100 per call; 50 pages = 5,000
# prompts, far past any plausible catalog size — a runaway-pagination guard,
# not a tunable.
_PAGE_LIMIT = 100
_MAX_PAGES = 50


def _cfg_bool(site_config: Any, key: str, default: bool) -> bool:
    return site_config.get_bool(key, default) if site_config is not None else default


def _repo_prompt_keys() -> set[str]:
    """Every prompt key the running code can resolve — the SKILL.md catalog.

    ``UnifiedPromptManager`` registers exactly the keys declared in
    ``skills/*/*/SKILL.md`` frontmatter (plus any legacy YAML, of which the
    repo ships none), so its ``prompts`` dict IS the authoritative inventory.
    """
    from services.prompt_manager import get_prompt_manager

    return set(get_prompt_manager().prompts.keys())


async def _fetch_langfuse_prompt_names(
    host: str, public_key: str, secret_key: str
) -> set[str]:
    """Return every prompt name in the Langfuse project (all versions collapse
    to one name). Raises on transport/auth errors — the caller converts that
    into a non-ok JobResult rather than a crash."""
    import httpx

    names: set[str] = set()
    async with httpx.AsyncClient(
        auth=(public_key, secret_key), timeout=30
    ) as client:
        page = 1
        while page <= _MAX_PAGES:
            resp = await client.get(
                f"{host}/api/public/v2/prompts",
                params={"page": page, "limit": _PAGE_LIMIT},
            )
            resp.raise_for_status()
            payload = resp.json()
            for item in payload.get("data", []):
                name = item.get("name")
                if name:
                    names.add(name)
            total_pages = (payload.get("meta") or {}).get("totalPages", 1)
            if page >= int(total_pages or 1):
                break
            page += 1
    return names


def _build_body(orphans: list[str], repo_only_count: int, in_both: int) -> str:
    lines = [
        f"{len(orphans)} Langfuse prompt(s) have ZERO references in the repo "
        f"prompt catalog (skills/*/*/SKILL.md frontmatter) — the key was "
        f"renamed or deleted, so editing these in the Langfuse UI silently "
        f"does nothing. Delete them from Langfuse (or re-key the edit onto "
        f"the live name):",
        "",
    ]
    lines.extend(f"- `{name}`" for name in orphans)
    lines.extend(
        [
            "",
            f"Context: {in_both} key(s) exist in both catalogs; "
            f"{repo_only_count} repo key(s) are repo-only, which is the "
            f"healthy state per the drift policy (Langfuse holds intentional "
            f"overrides only — see docs/architecture/prompt-management.md).",
        ]
    )
    return "\n".join(lines)


class ProbePromptCatalogDriftJob:
    """Emit a finding listing Langfuse prompts absent from the repo catalog."""

    name = "probe_prompt_catalog_drift"
    description = (
        "Compare the SKILL.md prompt catalog against Langfuse prompt names and "
        "surface orphaned Langfuse prompts (dead edit surfaces)"
    )
    schedule = "every 24 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        del pool  # unused — compares the in-process catalog vs the Langfuse API
        site_config = config.get("_site_config")
        if not _cfg_bool(site_config, _ENABLED_KEY, True):
            return JobResult(ok=True, detail="probe disabled", changes_made=0)

        if site_config is None:
            return JobResult(ok=False, detail="no site_config available", changes_made=0)

        host = (site_config.get("langfuse_host", "") or "").strip().rstrip("/")
        public_key = (site_config.get("langfuse_public_key", "") or "").strip()
        secret_key = (
            await site_config.get_secret("langfuse_secret_key", "") or ""
        ).strip()
        if not (host and public_key and secret_key):
            # OSS default — Langfuse not provisioned; there is no second
            # catalog to drift against. Skip quietly.
            return JobResult(
                ok=True, detail="langfuse not configured — skipped", changes_made=0
            )

        try:
            repo_keys = _repo_prompt_keys()
        except Exception as e:  # noqa: BLE001 — a probe must never crash a cycle
            logger.warning("[probe_prompt_catalog_drift] catalog load failed: %s", e)
            return JobResult(ok=False, detail=f"catalog load failed: {e}", changes_made=0)

        try:
            langfuse_names = await _fetch_langfuse_prompt_names(
                host, public_key, secret_key
            )
        except Exception as e:  # noqa: BLE001 — a probe must never crash a cycle
            logger.warning("[probe_prompt_catalog_drift] langfuse list failed: %s", e)
            return JobResult(
                ok=False, detail=f"langfuse list failed: {e}", changes_made=0
            )

        orphans = sorted(langfuse_names - repo_keys)
        repo_only_count = len(repo_keys - langfuse_names)
        in_both = len(repo_keys & langfuse_names)

        if not orphans:
            return JobResult(
                ok=True,
                detail=(
                    f"no drift: {in_both} in both, {repo_only_count} repo-only "
                    f"(healthy), 0 orphaned in Langfuse"
                ),
                changes_made=0,
            )

        emit_finding(
            source="prompt_catalog_drift_probe",
            kind=_FINDING_KIND,
            title=f"{len(orphans)} orphaned Langfuse prompt(s) — edits do nothing",
            body=_build_body(orphans, repo_only_count, in_both),
            severity="warn",
            dedup_key=_FINDING_KIND,
            extra={
                "orphaned": orphans,
                "orphaned_count": len(orphans),
                "repo_only_count": repo_only_count,
                "in_both_count": in_both,
            },
        )
        logger.info(
            "[probe_prompt_catalog_drift] emitted finding for %d orphaned "
            "Langfuse prompt(s)",
            len(orphans),
        )
        return JobResult(
            ok=True,
            detail=f"emitted finding for {len(orphans)} orphaned Langfuse prompt(s)",
            changes_made=0,
        )
