"""The media graph must be seeded with ``niche_slug``, or Stage 2 is niche-blind.

``PipelineState`` has declared ``niche_slug`` since the 2026-07-11 RCA and the
content flow seeds it, but ``dispatch_media_pipeline`` never did. Every media
atom therefore read ``state.get("niche_slug")`` as None: ``resolve_persona_for_
niche`` skipped ``niche.<slug>.media.persona`` and fell to the install default,
and ``resolve_media_policy`` judged every render against the global policy. On
a one-niche install both fallbacks give the same answer, which is why nothing
noticed; a second niche's persona could never win in Stage 2.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from poindexter.services.jobs import dispatch_media_pipeline as dmp


class _Runner:
    """Captures the context TemplateRunner.run is handed."""
    last: dict | None = None

    def __init__(self, pool, site_config=None):
        pass

    async def run(self, slug, context, *, thread_id=None):
        _Runner.last = {"slug": slug, "context": context, "thread_id": thread_id}


@pytest.fixture(autouse=True)
def _reset():
    _Runner.last = None
    yield
    _Runner.last = None


@pytest.mark.asyncio
async def test_niche_slug_from_the_task_row_is_seeded_into_the_context():
    load = AsyncMock(return_value="glad-labs")
    with patch("poindexter.services.template_runner.TemplateRunner", _Runner), \
         patch("poindexter.services.content_router_service._load_niche_slug", load):
        await dmp._run_media_pipeline(pool=object(), site_config=None, task_id="t-1")

    assert _Runner.last is not None
    assert _Runner.last["slug"] == "media_pipeline"
    assert _Runner.last["context"]["niche_slug"] == "glad-labs"
    # the loader is handed the same database_service shim the atoms get
    ds = load.await_args.args[0]
    assert isinstance(ds, dmp._PoolDS)
    assert load.await_args.args[1] == "t-1"


@pytest.mark.asyncio
async def test_a_task_with_no_niche_seeds_the_empty_string_not_none():
    """The declared channel is ``str``; every consumer treats '' as unknown."""
    with patch("poindexter.services.template_runner.TemplateRunner", _Runner), \
         patch("poindexter.services.content_router_service._load_niche_slug",
               AsyncMock(return_value=None)):
        await dmp._run_media_pipeline(pool=object(), site_config=None, task_id="t-2")

    assert _Runner.last["context"]["niche_slug"] == ""


@pytest.mark.asyncio
async def test_existing_context_keys_are_untouched():
    with patch("poindexter.services.template_runner.TemplateRunner", _Runner), \
         patch("poindexter.services.content_router_service._load_niche_slug",
               AsyncMock(return_value="x")):
        pool = object()
        await dmp._run_media_pipeline(pool=pool, site_config="SC", task_id="t-3")

    ctx = _Runner.last["context"]
    assert ctx["task_id"] == "t-3"
    assert ctx["site_config"] == "SC"
    assert ctx["pool"] is pool
    assert isinstance(ctx["database_service"], dmp._PoolDS)
    assert _Runner.last["thread_id"] == "media-t-3"


def test_every_media_atom_that_reads_niche_slug_declares_it():
    """Reading a channel you did not declare is how the auto-publish gate
    starved for six weeks. Each Stage-2 consumer must declare it as an input."""
    import inspect

    from poindexter.modules.content.atoms import (
        media_qa,
        media_render_long_video,
        media_render_narration,
        media_render_short_video,
    )

    for mod in (media_render_narration, media_render_long_video,
                media_render_short_video, media_qa):
        src = inspect.getsource(mod)
        assert 'FieldSpec(name="niche_slug"' in src, f"{mod.__name__} reads niche_slug but does not declare it"


def test_niche_persona_now_beats_the_default_in_stage_two():
    """The behaviour the seeding exists for: with a slug, the NICHE persona wins."""
    from poindexter.services.persona_service import resolve_persona_for_niche
    from poindexter.services.site_config import SiteConfig

    cfg = SiteConfig(initial_config={
        "media_default_persona": "house",
        "niche.acme.media.persona": "acme-host",
        "persona.house.display_name": "House", "persona.house.enabled": "true",
        "persona.acme-host.display_name": "Acme Host", "persona.acme-host.enabled": "true",
    })
    assert resolve_persona_for_niche(cfg, "acme").slug == "acme-host"
    assert resolve_persona_for_niche(cfg, "").slug == "house"       # what Stage 2 got before
    assert resolve_persona_for_niche(cfg, None).slug == "house"
