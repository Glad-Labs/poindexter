"""Anti-drift parity test for Glad-Labs/poindexter#693.

The finalize ``task_metadata`` blob is assembled in two production code
paths:

* ``FinalizeTaskStage`` (``modules/content/stages/finalize_task.py``) —
  the dev_diary terminal stage.
* ``content.persist_task`` (``modules/content/atoms/content_persist_task.py``)
  — the canonical_blog graph_def atom.

These drifted: the atom path silently grew four media keys
(``short_shot_list`` / ``video_ambient_audio_path`` / ``podcast_audio_path``
/ ``podcast_intro_audio_path``) that the stage path never gained. That
class of asymmetry is exactly what #693 was — metadata regressing on one
path while the other moved on.

Both paths now assemble the blob through the single
``modules.content.task_metadata.build_task_metadata`` helper. This suite
pins the invariant the helper exists to guarantee: **the two paths emit
an identical key set.** If a future edit adds a key to one path only, the
helper is the only place to add it — and the parity assertion breaks if
someone bypasses it.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The canonical key set the finalize metadata blob must always carry.
# Adding a field is a deliberate two-line change here + in the helper;
# the parity test then proves both production paths picked it up.
EXPECTED_METADATA_KEYS = frozenset(
    {
        "preview_token",
        "featured_image_url",
        "featured_image_alt",
        "featured_image_width",
        "featured_image_height",
        "featured_image_photographer",
        "featured_image_source",
        "content",
        "pre_approve_content",
        "seo_title",
        "seo_description",
        "seo_keywords",
        "topic",
        "style",
        "tone",
        "category",
        "target_audience",
        "post_id",
        "quality_score",
        "quality_score_early_eval",
        "qa_final_score",
        "content_length",
        "word_count",
        "podcast_script",
        "video_scenes",
        "short_summary_script",
        "video_shot_list",
        "short_shot_list",
        "video_ambient_audio_path",
        "podcast_audio_path",
        "podcast_intro_audio_path",
    }
)

# Field values both paths read by identical key names. No db handle here
# so it deep-copies cleanly; each path gets its own fresh copy + db mock.
_SHARED_STATE: dict = {
    "task_id": "task-693-parity",
    "topic": "Why context windows aren't free",
    "style": "technical",
    "tone": "analyst",
    "content": "# Title\n\nBody prose with several words to count here.",
    "category": "ai_ml",
    "target_audience": "indie devs",
    "title": "Why Context Windows Aren't Free",
    "featured_image_url": "https://r2.example/featured.jpg",
    "featured_image_alt": "alt text",
    "featured_image_width": 1200,
    "featured_image_height": 630,
    "featured_image_photographer": "Jane Doe",
    "featured_image_source": "pexels",
    "seo_title": "Context Windows Cost Analysis",
    "seo_description": "The hidden costs of large context windows",
    "seo_keywords": ["context windows", "LLM costs"],
    "quality_score": 88,
    "qa_final_score": 88,
    "preview_token": "a" * 32,
    "podcast_script": "PODCAST",
    "video_scenes": ["scene-1"],
    "short_summary_script": "SHORT",
    "video_shot_list": {"version": 1, "shots": []},
    "short_shot_list": {"version": 1, "shots": []},
    "video_ambient_audio_path": "/tmp/ambient.wav",
    "podcast_audio_path": "/tmp/podcast.wav",
    "podcast_intro_audio_path": "/tmp/intro.wav",
    "models_used_by_phase": {"writer": "glm-4.7-5090"},
    "quality_result": SimpleNamespace(overall_score=85),
}


def _make_db() -> MagicMock:
    db = MagicMock()
    db.pool = MagicMock()
    db.update_task = AsyncMock()
    db.update_task_status_guarded = AsyncMock(return_value="ok")
    return db


def _fresh_state(db: MagicMock) -> dict:
    state = copy.deepcopy(_SHARED_STATE)
    state["database_service"] = db
    return state


def _captured_metadata(db: MagicMock) -> dict:
    """Pull the ``task_metadata`` blob out of the update_task call."""
    db.update_task.assert_awaited()
    return db.update_task.await_args.kwargs["updates"]["task_metadata"]


async def _run_finalize_stage() -> dict:
    from modules.content.stages.finalize_task import FinalizeTaskStage

    db = _make_db()
    ctx = _fresh_state(db)
    fake_pdb = MagicMock()
    fake_pdb.upsert_version = AsyncMock()
    with patch(
        "services.pipeline_db.PipelineDB", return_value=fake_pdb,
    ), patch(
        "services.text_utils.normalize_text", side_effect=lambda s: s,
    ), patch(
        "services.excerpt_generator.generate_excerpt", return_value="excerpt",
    ), patch(
        "services.title_generation.strip_qa_batch_suffix", side_effect=lambda s: s,
    ), patch(
        "services.content_revisions_logger.log_revision", new=AsyncMock(),
    ):
        await FinalizeTaskStage().execute(ctx, {})
    return _captured_metadata(db)


async def _run_persist_atom() -> dict:
    from modules.content.atoms.content_persist_task import run as persist_run

    db = _make_db()
    state = _fresh_state(db)
    fake_pdb = MagicMock()
    fake_pdb.upsert_version = AsyncMock()
    with patch(
        "services.pipeline_db.PipelineDB", return_value=fake_pdb,
    ), patch(
        "services.text_utils.normalize_text", side_effect=lambda s: s,
    ), patch(
        "services.title_generation.strip_qa_batch_suffix", side_effect=lambda s: s,
    ), patch(
        "services.content_revisions_logger.log_revision", new=AsyncMock(),
    ):
        await persist_run(state)
    return _captured_metadata(db)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_finalize_and_persist_emit_identical_metadata_keys():
    """The dev_diary stage and the canonical_blog atom must agree on the
    exact set of ``task_metadata`` keys (Glad-Labs/poindexter#693)."""
    finalize_meta = await _run_finalize_stage()
    persist_meta = await _run_persist_atom()

    finalize_keys = set(finalize_meta)
    persist_keys = set(persist_meta)

    assert finalize_keys == persist_keys, (
        "FinalizeTaskStage and content.persist_task drifted on task_metadata "
        "keys (Glad-Labs/poindexter#693).\n"
        f"  only in finalize_task: {sorted(finalize_keys - persist_keys)}\n"
        f"  only in persist_task:  {sorted(persist_keys - finalize_keys)}"
    )
    # Both must match the canonical contract, not just each other.
    assert finalize_keys == set(EXPECTED_METADATA_KEYS)


@pytest.mark.unit
def test_build_task_metadata_contract():
    """The shared helper assembles exactly the canonical key set and
    derives content_length / word_count from the passed content."""
    from modules.content.task_metadata import build_task_metadata

    meta = build_task_metadata(
        {
            "topic": "t",
            "style": "s",
            "tone": "n",
            "category": "c",
            "target_audience": "devs",
            "featured_image_url": "u",
            "qa_final_score": 91,
            "video_shot_list": {"shots": []},
        },
        preview_token="tok",
        content_text="hello world example",
        seo_title="seo t",
        seo_description="seo d",
        seo_keywords_list=["k1", "k2"],
        final_quality_score=90,
        early_eval_score=85,
    )

    assert set(meta) == set(EXPECTED_METADATA_KEYS)
    assert meta["content"] == "hello world example"
    assert meta["pre_approve_content"] == "hello world example"
    assert meta["content_length"] == len("hello world example")
    assert meta["word_count"] == 3
    assert meta["seo_keywords"] == ["k1", "k2"]
    assert meta["quality_score"] == 90
    assert meta["quality_score_early_eval"] == 85
    assert meta["post_id"] is None


@pytest.mark.unit
def test_build_task_metadata_defaults_target_audience():
    """A missing/empty target_audience falls back to 'General' — the
    behaviour both call sites relied on inline."""
    from modules.content.task_metadata import build_task_metadata

    meta = build_task_metadata(
        {},
        preview_token="",
        content_text="",
        seo_title="",
        seo_description="",
        seo_keywords_list=[],
        final_quality_score=0,
        early_eval_score=0,
    )
    assert meta["target_audience"] == "General"
