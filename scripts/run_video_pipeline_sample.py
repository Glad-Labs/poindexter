"""One-off driver for the V0 video pipeline — produce a sample for ops review.

Drives all six video Stages in order on a real published post. NOT
part of the production worker — an explicit script lives here so a
human can watch the pipeline progress, override strategy choices for
a fast preview, and inspect the intermediate context after each Stage.

Usage:
    poetry run python scripts/run_video_pipeline_sample.py <post_slug>

Environment:
    POINDEXTER_SECRET_KEY  must be exported before running (the
                           SiteConfig.get_secret path needs it for
                           pgcrypto decryption).

Why bias to Pexels for the sample run:
    SDXL's ~30s/scene × ~10 scenes = ~5 min just on visuals. Pexels
    is API-fast (~1s/scene) and produces respectable stock photos
    for hardware / cooling topics. The strategy override is local to
    this script — production picks via app_settings unchanged.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import asyncpg


# Make src/cofounder_agent importable when invoked from the repo root.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "src", "cofounder_agent"))


_DATABASE_URL = (
    "postgresql://poindexter:poindexter-brain-local"
    "@127.0.0.1:15432/poindexter_brain"
)


class _OverrideConfig:
    """SiteConfig-shaped wrapper that overrides specific keys.

    Wraps the real SiteConfig so we get all the encrypted-secret
    plumbing for free, but biases ``video_scene_visuals_strategy``
    toward Pexels for fast first-sample turnaround.
    """

    def __init__(self, real: Any, overrides: dict[str, Any]) -> None:
        self._real = real
        self._overrides = overrides
        # Expose the underlying pool so Stages that rely on
        # ``site_config._pool`` (scene_visuals reuse-lookup, stitch
        # media_assets persistence) still work.
        self._pool = getattr(real, "_pool", None)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._overrides:
            return self._overrides[key]
        return self._real.get(key, default)

    async def get_secret(self, key: str, default: str = "") -> str:
        return await self._real.get_secret(key, default)


async def _load_post(pool: Any, slug: str) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, slug, title, content, excerpt, seo_description, seo_keywords
            FROM posts
            WHERE slug = $1
            """,
            slug,
        )
    if row is None:
        raise SystemExit(f"post not found: slug={slug!r}")
    return dict(row)


async def _build_site_config(pool: Any) -> Any:
    from services.site_config import SiteConfig

    sc = SiteConfig()
    await sc.load(pool=pool)
    # The pool attach happens inside load() but we rely on it for
    # both Stages and the override wrapper.
    sc._pool = pool  # type: ignore[attr-defined]
    return sc


async def _run_stages(
    context: dict[str, Any], skip_long_form: bool = False,
) -> dict[str, Any]:
    """Execute the six video Stages in order, propagating context_updates.

    ``skip_long_form=True`` skips the StitchLongFormStage. Short-form
    sample runs benefit from this when the upstream Wan/SDXL/TTS work
    is already slow — long-form would double the wall-clock cost.
    The script + scene_visuals + tts Stages still produce both
    long_form and short_form outputs in their context (they're cheap
    once running); only the long-form stitch is skipped.
    """
    from services.stages.script_for_video import ScriptForVideoStage
    from services.stages.scene_visuals import SceneVisualsStage
    from services.stages.tts_for_video import TtsForVideoStage
    from services.stages.stitch_long_form import StitchLongFormStage
    from services.stages.stitch_short_form import StitchShortFormStage
    from services.stages.upload_to_platform import UploadToPlatformStage

    stages: list[Any] = [
        ScriptForVideoStage(),
        SceneVisualsStage(),
        TtsForVideoStage(),
    ]
    if not skip_long_form:
        stages.append(StitchLongFormStage())
    stages.extend([
        StitchShortFormStage(),
        UploadToPlatformStage(),
    ])

    for stage in stages:
        print(f"\n=== {stage.name} ===")
        result = await stage.execute(context, {})
        print(f"  ok={result.ok} | {result.detail}")
        if result.metrics:
            for key, value in result.metrics.items():
                print(f"  metric.{key}: {value}")
        # Merge context_updates into context the way the production
        # orchestrator does.
        if result.context_updates:
            context.update(result.context_updates)
        if not result.ok and getattr(stage, "halts_on_failure", False):
            print(f"  halting: stage marked halts_on_failure=True")
            break

    return context


class _FakeDatabaseService:
    """Minimal services.container 'database' service shim.

    Several providers (PexelsProvider, etc.) reach for
    ``services.container.get_service('database').pool`` to read encrypted
    secrets. The DI container is normally populated by FastAPI's
    lifespan; this driver runs outside FastAPI so we register a fake
    service that exposes the same .pool attribute.
    """

    def __init__(self, pool: Any) -> None:
        self.pool = pool


async def main(
    slug: str, strategy: str = "pexels", skip_long_form: bool = False,
) -> None:
    pool = await asyncpg.create_pool(_DATABASE_URL, min_size=1, max_size=4)
    try:
        # Populate the module-level service container so providers that
        # use the DI seam (Pexels, etc.) can reach the DB pool.
        from services.container import register_service
        register_service("database", _FakeDatabaseService(pool))

        post = await _load_post(pool, slug)
        print(f"loaded post: {post['title'][:80]}")
        print(f"  id: {post['id']}")
        print(f"  content: {len(post['content'])} chars")

        real_config = await _build_site_config(pool)

        # Override scene-visuals strategy to Pexels for sample-run speed.
        site_config = _OverrideConfig(
            real_config,
            overrides={
                "video_scene_visuals_strategy": strategy,
                # Force-set the writer model in case the auto path
                # would pick something slow. Strip the ollama/ prefix
                # — script_for_video does this itself but being
                # explicit here surfaces the choice.
                # Override to a non-thinking model for the sample.
                # GLM-4.7 is a thinking model and returns 10k of CoT
                # trace + empty content for JSON-mode prompts; the
                # script Stage gets nothing parseable. Gemma 3 27B
                # produces clean JSON in tests and is comparable
                # quality for prose tasks.
                "pipeline_writer_model": "gemma3:27b",
                # ollama_base_url in app_settings is host.docker.internal
                # for the in-Docker worker. This driver runs on the host
                # itself; rewrite to localhost so the bare-host process
                # can reach Ollama.
                "ollama_base_url": "http://localhost:11434",
                # SDXL / Wan / video sidecars are also addressed via
                # host.docker.internal in production. Map all of them
                # to localhost for the host runner.
                "sdxl_server_url": "http://localhost:9836",
                "plugin.video_provider.wan2.1-1.3b.server_url": "http://localhost:9840",
            },
        )

        # Tags from the post — used by upload_to_platform. The posts
        # table uses seo_keywords (text, comma-separated) rather than
        # a relational tags column.
        kw_raw = post.get("seo_keywords") or ""
        if isinstance(kw_raw, str):
            tags = [t.strip() for t in kw_raw.split(",") if t.strip()]
        else:
            tags = list(kw_raw) if isinstance(kw_raw, list) else []

        context: dict[str, Any] = {
            "task_id": f"sample_{post['id']}",
            "post_id": post["id"],
            "title": post["title"],
            "content": post["content"],
            "excerpt": post.get("excerpt") or "",
            "seo_description": post.get("seo_description") or "",
            "tags": tags,
            "site_config": site_config,
        }

        await _run_stages(context, skip_long_form=skip_long_form)

        print("\n=== final video_outputs ===")
        outputs = context.get("video_outputs") or {}
        for kind in ("long_form", "short_form"):
            payload = outputs.get(kind) or {}
            if not payload:
                print(f"  {kind}: <empty>")
                continue
            print(f"  {kind}:")
            print(f"    output_path:    {payload.get('output_path')}")
            print(f"    public_url:     {payload.get('public_url') or '<no upload>'}")
            print(f"    media_asset_id: {payload.get('media_asset_id')}")
            print(f"    duration_s:     {payload.get('duration_s')}")
            print(f"    file_size:      {payload.get('file_size_bytes')} bytes")
            print(f"    srt_path:       {payload.get('srt_path')}")
    finally:
        await pool.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "usage: run_video_pipeline_sample.py <post_slug> "
            "[strategy=pexels|sdxl|wan|mixed|reuse_first] "
            "[--short-only]"
        )
        sys.exit(1)
    _argv = sys.argv[1:]
    _skip_long = "--short-only" in _argv
    _argv = [a for a in _argv if a != "--short-only"]
    _slug = _argv[0]
    _strategy = _argv[1] if len(_argv) >= 2 else "pexels"
    asyncio.run(main(_slug, _strategy, _skip_long))
