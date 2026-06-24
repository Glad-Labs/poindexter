"""Operator-grade draft editing — body + images — for awaiting_approval tasks.

Single owner of draft mutations (``pipeline_versions`` + ``audit_log``).
Reached from the CLI / MCP / API adapters: the API routes construct it from
``app.state`` deps and are the real seam; the CLI and MCP tools are OAuth HTTP
clients of those routes. Drafts only — published ``posts.content`` editing is
out of scope (poindexter#523).
"""
from __future__ import annotations

import logging
import re
import uuid
from contextlib import suppress
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

logger = logging.getLogger(__name__)

_LATEST_VERSION_SQL = (
    "SELECT content, version FROM pipeline_versions "
    "WHERE task_id = $1 ORDER BY version DESC LIMIT 1"
)
_UPDATE_CONTENT_SQL = (
    "UPDATE pipeline_versions SET content = $1 "
    "WHERE task_id = $2 AND version = $3"
)
_UPDATE_FEATURED_SQL = (
    "UPDATE pipeline_versions SET featured_image_url = $1 "
    "WHERE task_id = $2 AND version = $3"
)
_CHECK_TASK_STATUS_SQL = (
    "SELECT status FROM pipeline_tasks WHERE task_id = $1"
)
_UPDATE_POST_FEATURED_SQL = (
    "UPDATE posts SET featured_image_url = $1, updated_at = NOW() "
    "WHERE metadata->>'pipeline_task_id' = $2"
)
_IMG_TAG_RE = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]*)(")', re.IGNORECASE)


def _as_dict(value: Any) -> dict:
    """Coerce a JSON-string-or-dict task field into a dict (mirrors the route helper)."""
    import json

    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


@dataclass
class EditResult:
    """Outcome of one draft edit, returned identically across all adapters."""

    task_id: str
    field: str            # "body" | "featured" | "inline:N"
    ok: bool
    detail: str
    warnings: list[str] = dc_field(default_factory=list)
    new_url: str | None = None


class PostEditService:
    """Edit an ``awaiting_approval`` draft's body and images. Drafts only.

    Constructed per-request by the API route with the live ``app.state`` deps;
    unit tests pass fakes. Only ``pool`` is required for body edits; image ops
    additionally need ``image_service`` (regen) and optionally ``db_service``
    (featured-image mirror into ``pipeline_tasks.result``/``task_metadata``).

    ``platform`` is the module→kernel handle (``app.state.kernel_platform``).
    The audit-trail row for each edit is written through ``platform.audit`` —
    the capability seam — rather than importing the kernel ``AuditLogger``
    directly, which the module-purity boundary forbids
    (``scripts/ci/module_purity_lint.py``). Optional + guarded: when no handle
    is wired (unit tests omit it) the audit row drops, mirroring the
    best-effort posture the pipeline atoms take with ``state['platform']``.
    """

    def __init__(
        self,
        *,
        pool: Any,
        site_config: Any = None,
        image_service: Any = None,
        db_service: Any = None,
        platform: Any = None,
    ) -> None:
        self._pool = pool
        self._site_config = site_config
        self._image_service = image_service
        self._db_service = db_service
        self._platform = platform

    # -- body ---------------------------------------------------------------

    async def edit_body(
        self,
        task_id: str,
        *,
        new_content: str | None = None,
        find: str | None = None,
        replace: str | None = None,
    ) -> EditResult:
        """Overwrite (``new_content``) or surgically patch (``find``/``replace``)
        the latest ``pipeline_versions.content``. Validation is warn-only — the
        operator is the human approval gate, so a flagged edit still persists.
        """
        content, version = await self._latest(task_id)
        if new_content is not None:
            body = new_content
        elif find is not None:
            if find not in content:
                raise ValueError("find string not present in draft body")
            body = content.replace(find, replace or "")
        else:
            raise ValueError("edit_body requires new_content or find/replace")

        warnings = self._validate_warn_only(body)
        await self._pool.execute(_UPDATE_CONTENT_SQL, body, task_id, version)
        await self._audit(
            "post_edit_body", task_id,
            {"version": version, "before_len": len(content),
             "after_len": len(body), "warnings": warnings},
        )
        return EditResult(
            task_id, "body", True,
            f"edited body (v{version}, {len(body)} chars)", warnings=warnings,
        )

    # -- images -------------------------------------------------------------

    async def replace_image(self, task_id: str, *, which: str, url: str) -> EditResult:
        """Swap a draft image URL. ``which`` = ``featured`` or ``inline:N`` (1-based).

        ``featured`` updates ``pipeline_versions.featured_image_url`` (canonical)
        and best-effort mirrors it into ``pipeline_tasks.result``/``task_metadata``.
        ``inline:N`` rewrites the ``src`` of the N-th ``<img>`` in the body.
        """
        norm = which.strip().lower()
        if norm == "featured":
            _, version = await self._latest(task_id)
            await self._pool.execute(_UPDATE_FEATURED_SQL, url, task_id, version)
            await self._sync_task_featured(task_id, url)
            warnings = await self._sync_published_post_featured(task_id, url)
            await self._audit("post_image_replace", task_id, {"which": "featured", "url": url})
            return EditResult(task_id, "featured", True, f"featured image → {url}", new_url=url, warnings=warnings)

        if norm.startswith("inline:"):
            try:
                n = int(norm.split(":", 1)[1])
            except ValueError as e:
                raise ValueError(f"bad inline index in {which!r}") from e
            content, version = await self._latest(task_id)
            new_content, found = self._replace_nth_img_src(content, n, url)
            if not found:
                raise ValueError(f"inline image #{n} not found in draft body")
            await self._pool.execute(_UPDATE_CONTENT_SQL, new_content, task_id, version)
            await self._audit("post_image_replace", task_id, {"which": norm, "url": url})
            return EditResult(
                task_id, f"inline:{n}", True, f"inline image #{n} → {url}", new_url=url,
            )

        raise ValueError(f"--which must be 'featured' or 'inline:N', got {which!r}")

    async def regen_image(self, task_id: str, *, which: str, prompt: str) -> EditResult:
        """Generate a fresh image via the image capability and swap it into the draft.

        ``which`` = ``featured`` or ``inline:N``. Honors the configured
        no-humans/on-topic negative prompt. Requires a wired image service.
        """
        if self._image_service is None:
            raise RuntimeError("image service not available for regen")
        import os
        import tempfile

        negative = ""
        if self._site_config is not None:
            negative = self._site_config.get("image_negative_prompt", "") or ""

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            out_path = tmp.name
        try:
            ok = await self._image_service.generate_image(
                prompt=prompt, output_path=out_path, negative_prompt=negative,
            )
            if not ok or not os.path.exists(out_path):
                raise RuntimeError("image generation produced no output")
            url = await self._upload_image(out_path, task_id)
        finally:
            with suppress(OSError):
                os.remove(out_path)

        res = await self.replace_image(task_id, which=which, url=url)
        await self._audit(
            "post_image_regen", task_id,
            {"which": res.field, "prompt": prompt[:200], "url": url},
        )
        return EditResult(
            task_id, res.field, True, f"regenerated {res.field} image",
            new_url=url, warnings=res.warnings,
        )

    # -- helpers ------------------------------------------------------------

    async def _latest(self, task_id: str) -> tuple[str, int]:
        """Return (content, version) for the highest-version draft row."""
        row = await self._pool.fetchrow(_LATEST_VERSION_SQL, task_id)
        if not row:
            raise ValueError(f"no pipeline_versions row for task {task_id}")
        return (row["content"] or ""), int(row["version"])

    @staticmethod
    def _replace_nth_img_src(content: str, n: int, url: str) -> tuple[str, bool]:
        """Rewrite the ``src`` of the n-th ``<img>`` (1-based). Returns (new, found)."""
        counter = {"i": 0}

        def _sub(match: re.Match[str]) -> str:
            counter["i"] += 1
            if counter["i"] == n:
                return f"{match.group(1)}{url}{match.group(3)}"
            return match.group(0)

        new = _IMG_TAG_RE.sub(_sub, content)
        return new, counter["i"] >= n

    async def _sync_task_featured(self, task_id: str, url: str) -> None:
        """Best-effort mirror of the featured URL into ``pipeline_tasks.result`` /
        ``task_metadata`` (matches the generate-image route). Advisory only — the
        canonical field is ``pipeline_versions.featured_image_url``, already written."""
        if self._db_service is None:
            return
        import json

        try:
            task = await self._db_service.get_task(task_id)
            result = _as_dict(task.get("result"))
            meta = _as_dict(task.get("task_metadata"))
            result["featured_image_url"] = url
            meta["featured_image_url"] = url
            await self._db_service.update_task(
                task_id,
                {"result": json.dumps(result), "task_metadata": json.dumps(meta)},
            )
        except Exception as e:  # noqa: BLE001 — advisory mirror; canonical field already persisted
            # warning (not debug) so the failed mirror is operator-visible in
            # Loki — the canonical featured_image_url is already saved, so this
            # never blocks the edit, but a silent drop would hide DB trouble.
            logger.warning(
                "featured-image task mirror skipped for %s (canonical field "
                "already saved): %s", task_id, e,
            )

    async def _sync_published_post_featured(self, task_id: str, url: str) -> list[str]:
        """Update posts.featured_image_url and trigger a static rebuild for published tasks.

        posts.featured_image_url is what the static-export JSON reads; pipeline_versions
        is the canonical draft store but not what the live site serves. Skips silently
        for non-published tasks (drafts, approved-but-not-live, etc.).
        """
        warnings: list[str] = []
        try:
            status = await self._pool.fetchval(_CHECK_TASK_STATUS_SQL, task_id)
            if status != "published":
                return warnings
            await self._pool.execute(_UPDATE_POST_FEATURED_SQL, url, task_id)
            logger.info("posts.featured_image_url updated for published task %s", task_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "posts.featured_image_url sync failed for %s: %s — "
                "run rebuild_static_export manually",
                task_id, e,
            )
            warnings.append(
                f"posts.featured_image_url update failed ({e!s})"
                " — run rebuild_static_export manually"
            )
            return warnings
        if self._site_config is None:
            warnings.append(
                "no site_config wired — run rebuild_static_export manually to update the live site"
            )
            return warnings
        try:
            from services.static_export_service import export_full_rebuild

            result = await export_full_rebuild(self._pool, site_config=self._site_config)
            if result.get("success"):
                warnings.append("static export rebuild triggered — live site will reflect the new image")
            else:
                warnings.append(
                    "static export rebuild did not fully succeed"
                    " — verify on Grafana or run rebuild_static_export manually"
                )
        except Exception as e:  # noqa: BLE001 — export failure must not block the image swap
            logger.warning(
                "static export rebuild failed after image update for %s: %s", task_id, e,
            )
            warnings.append(
                f"static export rebuild failed ({e!s}) — run rebuild_static_export manually"
            )
        return warnings

    def _validate_warn_only(self, body: str) -> list[str]:
        """Re-run the programmatic validator; never block. Returns warning strings.

        Skips silently when no SiteConfig is wired (validator requires it) or if
        the validator raises — advisory only, must never fail an operator edit.
        """
        if self._site_config is None:
            return []
        try:
            from modules.content.api import validate_content

            result = validate_content(
                title="", content=body, site_config=self._site_config,
            )
            return [f"{i.severity}: {i.description}" for i in result.issues]
        except Exception as e:  # noqa: BLE001 — validation is advisory, never blocks an edit
            # Surface the validator crash (warning → Loki) instead of dropping
            # it: the edit still applies (warn-only gate), but the operator
            # should see that QA failed to run on this draft.
            logger.warning("post-edit validator failed (edit still applies): %s", e)
            return []

    async def _upload_image(self, path: str, task_id: str) -> str:
        """Upload a generated image to R2 and return its servable URL.

        Mirrors the pipeline's featured path (``source_featured_image``'s
        ``_upload_featured_to_r2``): ``R2UploadService`` converts PNG→WebP and
        returns the public URL. ``task_id`` seeds a stable-ish object key.
        """
        from services.r2_upload_service import R2UploadService

        if self._site_config is None:
            raise RuntimeError("site_config required to upload generated image")
        svc = R2UploadService(site_config=self._site_config)
        key = f"images/featured/{task_id[:8]}-{uuid.uuid4().hex[:8]}.jpg"
        url = await svc.upload_to_r2(path, key, content_type="image/jpeg")
        if not url:
            raise RuntimeError("image upload returned no URL")
        return url

    async def _audit(self, event_type: str, task_id: str, details: dict) -> None:
        """Append this edit's ``audit_log`` row through the Platform handle.

        Routed via ``platform.audit.write`` (capability seam, awaited for
        durability — an audit-of-mutation should persist before we return)
        rather than the kernel ``AuditLogger``. Dropped when no handle is wired
        (see ``__init__``); the route always supplies ``app.state.kernel_platform``."""
        if self._platform is None:
            return
        await self._platform.audit.write(
            event_type,
            source="post_edit_service",
            details={"task_id": task_id, **details},
            task_id=task_id,
            severity="info",
        )
