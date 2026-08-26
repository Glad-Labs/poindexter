"""SyncPromptCatalogToLangfuseJob — keep Langfuse a read-only mirror of the
SKILL.md prompt catalog.

The prompt catalog is declared in ``skills/<pack>/<skill>/SKILL.md``
frontmatter, edited in the repo (PR + contract tests), and served
authoritatively by ``services/prompt_manager.py``. Langfuse exists so the
operator can REVIEW every production prompt (with version history) in one
UI — it is not an edit surface. (The legacy Langfuse-first override lookup
sits behind ``langfuse_prompt_overrides_enabled``, default off; it produced
the masking trap where a one-time bulk import shadowed every later SKILL.md
edit.)

Every sync cycle this job makes the mirror true:

- **Missing key** → created in Langfuse with the ``production`` label and a
  ``config.source = "skill_sync"`` provenance marker.
- **Default changed** → a new version is pushed (label moves with it), so
  the mirror always shows what production actually serves.
- **Hand-edited version found** (production version without the sync/import
  provenance marker) → still replaced with the current default, and a warn
  finding tells the operator Langfuse is a mirror — edit the SKILL.md.
- **Orphaned name** (in Langfuse, gone from the catalog — renamed/deleted
  key) → warn ``prompt_catalog_drift`` finding; deletion stays a human
  action.

Quiet on the OSS default: when Langfuse isn't configured (no host / public
key / secret in app_settings) the job skips without noise.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.exception_format import describe_exception
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# app_settings keys (seeded in settings_defaults.py).
_ENABLED_KEY = "langfuse_prompt_mirror_enabled"

_FINDING_KIND = "prompt_catalog_drift"

# Provenance markers identifying versions this pipeline owns. ``skill_sync``
# is stamped on everything we push; ``imported_by`` is the legacy marker the
# retired bulk-import script (scripts/import_prompts_to_langfuse.py) left on
# every pre-mirror version — treat those as sync-owned too so the first run
# upgrades them in place instead of flagging them as hand edits.
_SYNC_SOURCE = "skill_sync"
_LEGACY_IMPORT_MARKER = "import_prompts_to_langfuse.py"

# Langfuse list endpoint pages at most 100 per call; 50 pages = 5,000
# prompts, far past any plausible catalog size — a runaway-pagination guard,
# not a tunable.
_PAGE_LIMIT = 100
_MAX_PAGES = 50


def _cfg_bool(site_config: Any, key: str, default: bool) -> bool:
    return site_config.get_bool(key, default) if site_config is not None else default


def _repo_prompt_catalog() -> dict[str, dict[str, str]]:
    """The authoritative catalog: ``{key: {template, category}}``.

    ``UnifiedPromptManager`` registers exactly the keys declared in
    ``skills/*/*/SKILL.md`` frontmatter (plus any legacy YAML, of which the
    repo ships none), so its ``prompts`` dict IS the authoritative inventory.
    """
    from services.prompt_manager import get_prompt_manager

    pm = get_prompt_manager()
    catalog: dict[str, dict[str, str]] = {}
    for key, entry in pm.prompts.items():
        meta = pm.metadata.get(key)
        catalog[key] = {
            "template": entry.get("template", ""),
            "category": meta.category.value if meta else "uncategorized",
        }
    return catalog


async def _list_langfuse_prompt_names(client: Any, host: str) -> set[str]:
    """Every prompt name in the Langfuse project (versions collapse to one
    name). Raises on transport/auth errors — the caller converts that into a
    non-ok JobResult rather than a crash."""
    names: set[str] = set()
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


async def _get_production_version(
    client: Any, host: str, name: str
) -> dict[str, Any] | None:
    """The ``production``-labeled version's ``{prompt, config}``, or None
    when the name has no production version (or doesn't exist)."""
    from urllib.parse import quote

    resp = await client.get(
        f"{host}/api/public/v2/prompts/{quote(name, safe='')}",
        params={"label": "production"},
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    payload = resp.json()
    return {
        "prompt": payload.get("prompt", ""),
        "config": payload.get("config") or {},
    }


async def _push_prompt_version(
    client: Any, host: str, name: str, template: str, category: str
) -> None:
    """Create a new version of ``name`` carrying the sync provenance marker,
    labeled ``production`` so the mirror serves it immediately."""
    resp = await client.post(
        f"{host}/api/public/v2/prompts",
        json={
            "type": "text",
            "name": name,
            "prompt": template,
            "labels": ["production"],
            "tags": [category],
            "config": {
                "source": _SYNC_SOURCE,
                "category": category,
            },
        },
    )
    resp.raise_for_status()


def _is_sync_owned(config: dict[str, Any]) -> bool:
    """True when the production version was created by this job or the
    retired bulk-import script — versions we may replace without ceremony."""
    return (
        config.get("source") == _SYNC_SOURCE
        or config.get("imported_by") == _LEGACY_IMPORT_MARKER
    )


class SyncPromptCatalogToLangfuseJob:
    """Mirror the SKILL.md prompt catalog into Langfuse (read-only review UI)."""

    name = "sync_prompt_catalog_to_langfuse"
    description = (
        "Push SKILL.md prompt-catalog defaults into Langfuse so the UI mirrors "
        "every production prompt; flag orphaned names and hand edits"
    )
    schedule = "every 6 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        del pool  # unused — mirrors the in-process catalog to the Langfuse API
        site_config = config.get("_site_config")
        if not _cfg_bool(site_config, _ENABLED_KEY, True):
            return JobResult(ok=True, detail="mirror disabled", changes_made=0)

        if site_config is None:
            return JobResult(ok=False, detail="no site_config available", changes_made=0)

        host = (site_config.get("langfuse_host", "") or "").strip().rstrip("/")
        public_key = (site_config.get("langfuse_public_key", "") or "").strip()
        secret_key = (
            await site_config.get_secret("langfuse_secret_key", "") or ""
        ).strip()
        if not (host and public_key and secret_key):
            # OSS default — Langfuse not provisioned; nothing to mirror into.
            return JobResult(
                ok=True, detail="langfuse not configured — skipped", changes_made=0
            )

        try:
            catalog = _repo_prompt_catalog()
        except Exception as e:  # noqa: BLE001 — a sync job must never crash a cycle
            logger.warning("[sync_prompt_catalog] catalog load failed: %s", describe_exception(e))
            return JobResult(ok=False, detail=f"catalog load failed: {describe_exception(e)}", changes_made=0)

        import httpx

        created: list[str] = []
        updated: list[str] = []
        hand_edits_replaced: list[str] = []
        up_to_date = 0
        try:
            async with httpx.AsyncClient(
                auth=(public_key, secret_key), timeout=30
            ) as client:
                langfuse_names = await _list_langfuse_prompt_names(client, host)

                for key, entry in sorted(catalog.items()):
                    live = (
                        await _get_production_version(client, host, key)
                        if key in langfuse_names
                        else None
                    )
                    if live is None:
                        await _push_prompt_version(
                            client, host, key, entry["template"], entry["category"]
                        )
                        created.append(key)
                        continue
                    if live["prompt"] == entry["template"]:
                        up_to_date += 1
                        continue
                    hand_edit = not _is_sync_owned(live["config"])
                    await _push_prompt_version(
                        client, host, key, entry["template"], entry["category"]
                    )
                    (hand_edits_replaced if hand_edit else updated).append(key)
        except Exception as e:  # noqa: BLE001 — a sync job must never crash a cycle
            logger.warning("[sync_prompt_catalog] langfuse sync failed: %s", describe_exception(e))
            return JobResult(ok=False, detail=f"langfuse sync failed: {describe_exception(e)}", changes_made=0)

        orphans = sorted(langfuse_names - set(catalog))
        if orphans:
            emit_finding(
                source="prompt_catalog_sync",
                kind=_FINDING_KIND,
                title=f"{len(orphans)} orphaned Langfuse prompt(s) — key gone from catalog",
                body=(
                    f"{len(orphans)} Langfuse prompt name(s) no longer exist in the "
                    f"SKILL.md catalog (renamed or deleted keys) — they are dead "
                    f"entries the mirror will never update. Delete them from "
                    f"Langfuse (or re-key):\n\n"
                    + "\n".join(f"- `{n}`" for n in orphans)
                ),
                severity="warn",
                dedup_key=_FINDING_KIND,
                extra={"orphaned": orphans, "orphaned_count": len(orphans)},
            )
        if hand_edits_replaced:
            emit_finding(
                source="prompt_catalog_sync",
                kind=_FINDING_KIND,
                title=(
                    f"{len(hand_edits_replaced)} hand-edited Langfuse prompt(s) "
                    f"replaced by the mirror"
                ),
                body=(
                    "Langfuse is a read-only mirror of the SKILL.md prompt "
                    "catalog — the runtime serves the SKILL.md default, so a "
                    "UI edit never takes effect (unless "
                    "`langfuse_prompt_overrides_enabled` is on). The following "
                    "hand-edited version(s) were replaced with the current "
                    "default (the edited body remains in Langfuse version "
                    "history). To change a prompt, edit its SKILL.md section:\n\n"
                    + "\n".join(f"- `{n}`" for n in hand_edits_replaced)
                ),
                severity="warn",
                dedup_key="prompt-mirror-hand-edit-replaced",
                extra={"keys": hand_edits_replaced},
            )

        changes = len(created) + len(updated) + len(hand_edits_replaced)
        detail = (
            f"mirror synced: {len(created)} created, {len(updated)} updated, "
            f"{len(hand_edits_replaced)} hand-edit(s) replaced, "
            f"{up_to_date} up-to-date, {len(orphans)} orphaned"
        )
        logger.info("[sync_prompt_catalog] %s", detail)
        return JobResult(ok=True, detail=detail, changes_made=changes)
