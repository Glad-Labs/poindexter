"""Operator-grade post editing — body + images — for awaiting_approval tasks.

Single owner of draft mutations (``pipeline_versions`` + ``audit_log``).
Reached from the CLI / MCP / API adapters: the API routes construct it from
``app.state`` deps and are the real seam; the CLI and MCP tools are OAuth HTTP
clients of those routes.

Scope is decided by WHAT each edit writes — there is no status guard here or on
the routes. Body edits and ``inline:N`` image edits write
``pipeline_versions.content``; the live site serves ``posts.content``, so on a
published task they succeed against the draft store and change nothing publicly
visible. ``which="featured"`` is the deliberate exception: it mirrors into
``posts.featured_image_url`` and rebuilds the static export for published tasks
(see ``_sync_published_post_featured``), making it the one edit that reaches a
live post. Editing published ``posts.content`` remains out of scope
(poindexter#523).
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
_IMG_TAG_FULL_RE = re.compile(r"<img\b[^>]*?/?>", re.IGNORECASE)
# Mirrors modules/content/atoms/_image_helpers.py's heading matchers. Kept as
# a local copy rather than imported — PostEditService is a plain service, not
# a pipeline atom, and the atoms/ tree is the pipeline-time layer; importing
# from it here would run the atom->service dependency direction backwards.
_HEADING_RE = re.compile(r"^#{2,4}\s+(.+)$", re.MULTILINE)
_BOLD_HEADING_RE = re.compile(r"^\*\*(.{1,80}?)\*\*\s*$", re.MULTILINE)


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
    """Edit a task's body and images — drafts, plus the featured image of a
    published post (see the module docstring for the scope split).

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
        """Swap an image URL. ``which`` = ``featured`` or ``inline:N`` (1-based).

        ``featured`` updates ``pipeline_versions.featured_image_url`` (canonical)
        and best-effort mirrors it into ``pipeline_tasks.result``/``task_metadata``;
        for a published task it additionally syncs ``posts.featured_image_url``
        and rebuilds the static export, so this path reaches the live site.
        ``inline:N`` rewrites the ``src`` of the N-th ``<img>`` in the body —
        draft store only, invisible to a published post.
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

    async def remove_image(self, task_id: str, *, which: str) -> EditResult:
        """Remove an image. ``which`` = ``featured`` or ``inline:N`` (1-based).

        ``featured`` clears ``pipeline_versions.featured_image_url`` to NULL
        (nullable column — no promote-an-inline-image magic; the draft simply
        has no featured image until an operator sets one), and for a published
        task clears ``posts.featured_image_url`` + rebuilds the static export.
        ``inline:N`` strips the whole ``<img>`` tag from the body — draft store
        only, invisible to a published post. Removal doesn't renumber
        anything on disk — ``inline:N`` is always counted live off the
        current body, so the next command naturally sees one fewer image.
        """
        norm = which.strip().lower()
        if norm == "featured":
            _, version = await self._latest(task_id)
            await self._pool.execute(_UPDATE_FEATURED_SQL, None, task_id, version)
            await self._sync_task_featured(task_id, None)
            warnings = await self._sync_published_post_featured(task_id, None)
            await self._audit("post_image_remove", task_id, {"which": "featured"})
            return EditResult(task_id, "featured", True, "featured image removed", warnings=warnings)

        if norm.startswith("inline:"):
            try:
                n = int(norm.split(":", 1)[1])
            except ValueError as e:
                raise ValueError(f"bad inline index in {which!r}") from e
            content, version = await self._latest(task_id)
            new_content, found = self._remove_nth_img(content, n)
            if not found:
                raise ValueError(f"inline image #{n} not found in draft body")
            await self._pool.execute(_UPDATE_CONTENT_SQL, new_content, task_id, version)
            await self._audit("post_image_remove", task_id, {"which": norm})
            return EditResult(task_id, f"inline:{n}", True, f"inline image #{n} removed")

        raise ValueError(f"--which must be 'featured' or 'inline:N', got {which!r}")

    async def add_image(
        self,
        task_id: str,
        *,
        after: str | None = None,
        section: str | None = None,
        prompt: str | None = None,
    ) -> EditResult:
        """Generate a new image and insert it into a draft — the missing
        counterpart to ``replace_image``/``regen_image``, which can only
        operate on a slot that already exists (poindexter#2233).

        Exactly one of ``after`` (``inline:N`` — insert right after that
        existing inline image) or ``section`` (an H2-H4 heading, fuzzy
        substring match) positions the new image. ``prompt`` is optional:
        if omitted, it's derived from the target section's own heading text
        (operator preference: image prompts come from headings, not body
        prose) — the section the operator named directly for ``section``
        mode, or the nearest heading before the anchor image for ``after``
        mode. Raises if neither a prompt nor a derivable heading is
        available.
        """
        if bool(after) == bool(section):
            raise ValueError("add_image requires exactly one of 'after' or 'section'")
        if self._image_service is None:
            raise RuntimeError("image service not available for add-image")

        content, version = await self._latest(task_id)

        if section is not None:
            pos, resolved_heading = self._find_section_insert_point(content, section)
            if pos is None:
                raise ValueError(f"no section heading matching {section!r} found in draft body")
            anchor_detail = f"section {resolved_heading!r}"
        else:
            norm = (after or "").strip().lower()
            if not norm.startswith("inline:"):
                raise ValueError(f"--after must be 'inline:N', got {after!r}")
            try:
                n = int(norm.split(":", 1)[1])
            except ValueError as e:
                raise ValueError(f"bad inline index in {after!r}") from e
            pos = self._find_nth_img_end(content, n)
            if pos is None:
                raise ValueError(f"inline image #{n} not found in draft body")
            resolved_heading = self._find_preceding_heading(content, pos)
            anchor_detail = f"inline:{n}"

        final_prompt = prompt or resolved_heading
        if not final_prompt:
            raise ValueError(
                "no --prompt given and no section heading could be found to "
                "derive one from — pass --prompt explicitly"
            )

        url = await self._generate_and_upload(task_id, final_prompt)

        tag = (
            f'\n\n<img src="{url}" alt="{final_prompt[:200]}" '
            f'width="1024" height="1024" loading="lazy" />\n\n'
        )
        new_content = content[:pos] + tag + content[pos:]
        new_content = re.sub(r"\n{3,}", "\n\n", new_content)
        await self._pool.execute(_UPDATE_CONTENT_SQL, new_content, task_id, version)
        await self._audit(
            "post_image_add", task_id,
            {"after": after, "section": section, "prompt": final_prompt[:200], "url": url},
        )
        return EditResult(
            task_id, "inline:new", True, f"added image after {anchor_detail}",
            new_url=url,
        )

    async def regen_image(self, task_id: str, *, which: str, prompt: str) -> EditResult:
        """Generate a fresh image via the image capability and swap it into the draft.

        ``which`` = ``featured`` or ``inline:N``. Honors the configured
        no-humans/on-topic negative prompt. Requires a wired image service.
        """
        if self._image_service is None:
            raise RuntimeError("image service not available for regen")

        url = await self._generate_and_upload(task_id, prompt)

        res = await self.replace_image(task_id, which=which, url=url)
        await self._audit(
            "post_image_regen", task_id,
            {"which": res.field, "prompt": prompt[:200], "url": url},
        )
        return EditResult(
            task_id, res.field, True, f"regenerated {res.field} image",
            new_url=url, warnings=res.warnings,
        )

    async def brand_hero(
        self, task_id: str, *, tagline: str | None = None, title: str | None = None,
    ) -> EditResult:
        """Compose an on-brand hero from the brand tokens and set it as featured.

        Deterministic sibling of :meth:`regen_image`: same upload + swap + audit
        path, but the image is RENDERED from HTML rather than generated. A hero
        wants the brand mark, a mark means type, and diffusion cannot set type —
        asked for one it produced mangled letterforms over six-fingered hands.
        This costs no GPU and cannot trip the OCR text gate, because the text is
        intentional. See ``services/brand_hero.py``.
        """
        import os
        import tempfile

        from services.brand_hero import HeroSpec, render_hero_png

        overrides: dict[str, str] = {}
        if tagline:
            overrides["tagline"] = tagline
        if title:
            overrides["title"] = title
        png = await render_hero_png(HeroSpec(**overrides))
        if not png:
            raise RuntimeError("brand hero render produced no output")

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(png)
            out_path = tmp.name
        try:
            url = await self._upload_image(out_path, task_id)
        finally:
            with suppress(OSError):
                os.remove(out_path)

        res = await self.replace_image(task_id, which="featured", url=url)
        await self._audit(
            "post_brand_hero", task_id,
            {"which": res.field, "url": url, "tagline": (tagline or "")[:200]},
        )
        return EditResult(
            task_id, res.field, True, "composed brand hero",
            new_url=url, warnings=res.warnings,
        )

    # -- helpers ------------------------------------------------------------

    async def _generate_and_upload(self, task_id: str, prompt: str) -> str:
        """Render one image and return its uploaded URL. Raises on failure.

        The single generate path for both ``regen_image`` and ``add_image``,
        which had drifted into two copies of the same temp-file dance.

        Failure carries the reason. ``ImageService`` renders under
        ``gpu.lock("image_gen")`` and reports WHY it failed via
        ``generate_image_result`` (poindexter#1005) — a CUDA OOM, a degraded
        server, a GPU busy beyond the operator wait budget. The route maps the
        RuntimeError raised here onto a 503, so whatever we put in the message
        is what the operator reads; the old fixed "produced no output" told
        them nothing and, for the OOM case, actively misdirected — the render
        never started, so there was no output to produce.

        Falls back to the bool ``generate_image`` when the injected service
        predates the detailed API (duck-typed seam — tests inject fakes).
        """
        import os
        import tempfile

        negative = ""
        if self._site_config is not None:
            negative = self._site_config.get("image_negative_prompt", "") or ""

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            out_path = tmp.name
        try:
            detailed = getattr(self._image_service, "generate_image_result", None)
            if detailed is not None:
                outcome = await detailed(
                    prompt=prompt, output_path=out_path, negative_prompt=negative,
                )
                ok, why = bool(outcome.ok), outcome.message
            else:
                ok = await self._image_service.generate_image(
                    prompt=prompt, output_path=out_path, negative_prompt=negative,
                )
                why = "image generation produced no output"
            if not ok:
                raise RuntimeError(why)
            if not os.path.exists(out_path):
                # Backend claimed success but wrote nothing — a real defect
                # rather than a capacity problem, so say that plainly instead
                # of reusing the generic message and hiding it among OOMs.
                raise RuntimeError(
                    "image generation reported success but wrote no file "
                    f"to {out_path}"
                )
            return await self._upload_image(out_path, task_id)
        finally:
            with suppress(OSError):
                os.remove(out_path)

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

    @staticmethod
    def _remove_nth_img(content: str, n: int) -> tuple[str, bool]:
        """Strip the whole n-th ``<img>`` tag (1-based). Returns (new, found).

        Uses ``_IMG_TAG_FULL_RE`` (matches the whole tag) rather than
        ``_IMG_TAG_RE`` (matches up to ``src="..."`` only) — both match the
        same set of tags in the same order for every body this codebase
        generates (every ``<img>`` always carries ``src``), so "n-th image"
        stays consistent between replace/remove/add.
        """
        counter = {"i": 0}
        removed = {"ok": False}

        def _sub(match: re.Match[str]) -> str:
            counter["i"] += 1
            if counter["i"] == n:
                removed["ok"] = True
                return ""
            return match.group(0)

        new = _IMG_TAG_FULL_RE.sub(_sub, content)
        if removed["ok"]:
            new = re.sub(r"\n{3,}", "\n\n", new)
        return new, removed["ok"]

    @staticmethod
    def _find_nth_img_end(content: str, n: int) -> int | None:
        """Return the end offset of the n-th (1-based) ``<img>`` tag, or None."""
        matches = list(_IMG_TAG_FULL_RE.finditer(content))
        if n < 1 or n > len(matches):
            return None
        return matches[n - 1].end()

    @staticmethod
    def _find_section_insert_point(content: str, section: str) -> tuple[int | None, str | None]:
        """Fuzzy-match ``section`` against H2-H4 (or bold pseudo-heading) text;
        anchor at the next paragraph break after the match — the same
        placement convention ``_plan_and_inject_placeholders`` uses at
        generation time. Returns (offset, matched_heading_text) or (None, None).
        """
        # Compare case-insensitively but return the heading in its original
        # casing — it doubles as the auto-derived prompt text, and natural
        # casing ("Cooling Systems") reads better there than a forced-lower
        # comparison key would. Must match _find_preceding_heading's casing.
        needle = section.strip().lower()
        for m in _HEADING_RE.finditer(content):
            raw_text = re.sub(r"^#+\s*", "", m.group()).strip()
            if needle in raw_text.lower() or raw_text.lower() in needle:
                para_end = content.find("\n\n", m.end())
                return (para_end if para_end >= 0 else len(content)), raw_text
        for m in _BOLD_HEADING_RE.finditer(content):
            raw_text = m.group(1).strip()
            if needle in raw_text.lower() or raw_text.lower() in needle:
                para_end = content.find("\n\n", m.end())
                return (para_end if para_end >= 0 else len(content)), raw_text
        return None, None

    @staticmethod
    def _find_preceding_heading(content: str, pos: int) -> str | None:
        """Return the text of the nearest H2-H4 (or bold pseudo-heading) before ``pos``."""
        best_end = -1
        best_text: str | None = None
        for pattern, group in ((_HEADING_RE, 0), (_BOLD_HEADING_RE, 1)):
            for m in pattern.finditer(content, 0, pos):
                if m.end() > best_end:
                    best_end = m.end()
                    text = m.group() if group == 0 else m.group(1)
                    best_text = re.sub(r"^#+\s*", "", text).strip()
        return best_text

    async def _sync_task_featured(self, task_id: str, url: str | None) -> None:
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

    async def _sync_published_post_featured(self, task_id: str, url: str | None) -> list[str]:
        """Update posts.featured_image_url and trigger a static rebuild for published tasks.

        posts.featured_image_url is what the static-export JSON reads; pipeline_versions
        is the canonical draft store but not what the live site serves. Skips silently
        for non-published tasks (drafts, approved-but-not-live, etc.).

        Deliberately stops at the rebuild: it does NOT call
        ``trigger_isr_revalidate``. The Next.js post page caches on the
        ``posts`` / ``post:<slug>`` tags with no time-based ``revalidate``, so
        the live page keeps serving the previous image until a caller
        revalidates. Callers that need the swap visible immediately must do
        that themselves. Note also that the rebuild re-uploads every published
        post's JSON, so it can outlast an adapter's HTTP timeout — the DB
        writes above have already committed by then, making a client-side
        timeout a slow success rather than a failed edit.
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
