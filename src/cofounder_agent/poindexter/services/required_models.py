"""The Ollama models a fresh install must pull for the default content pipeline.

Nothing in the pipeline pulls a model on demand: a model Ollama lacks fails
that call. So the README quick start and docs/quickstart.mdx must pull exactly
the models the default pipeline calls — and they drifted: the README pulled
``qwen3:8b``, which no default setting referenced any more, while the pipeline
called models neither page listed.

This module is the one place that says which settings name those models.
The model TAGS are never written here; they are read from the settings:

* tests/unit/services/test_required_models.py derives the tags from the seeded
  defaults (``settings_defaults.DEFAULTS`` + ``0000_baseline.seeds.sql``) and
  fails when the README's ``ollama pull`` line or docs/quickstart.mdx differ,
  so changing a default model is a one-place edit the docs cannot miss;
* ``poindexter setup --auto`` prints the pull command from the LIVE
  app_settings, so an operator who re-pointed a role gets their own list;
* the ``quickstart-e2e`` workflow fails when the pipeline calls a model the
  README does not pull, which is how a new model role gets noticed here.

The keys below are the roles the default ``canonical_blog`` graph calls on a
fresh install. Measured on a clean runner (quickstart-e2e, 2026-09-28,
``cost_logs.model`` over one post): ``gemma3:27b`` 21 calls, ``phi4:14b`` 18,
``llama3:latest`` 3 (the media-script stage) — and nothing else. Embeddings are
not in ``cost_logs`` but every RAG lookup needs them. Left out on purpose, with
the reason:

* ``qa_vision_model`` (``qwen3-vl``, ~20 GB) — only called when a post has
  images; a fresh install with no image source has none;
* ``qa_fallback_critic_model`` — only used when the primary critic fails;
* ``ops_*`` / ``console_chat_model`` / voice — features the first post does
  not touch.

Those are *optional* pulls (README "Which model does what"): pull them when you
switch the feature on.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

# Roles the default pipeline calls, in pull order (largest first is not the
# point — the order a reader sees them in the docs).
PIPELINE_MODEL_KEYS: tuple[str, ...] = (
    # The writer, and the dozen roles seeded with the same model (self-review,
    # structured extraction, image prompts, podcast/video-director scripts).
    "pipeline_writer_model",
    # The cross-model critic — the hard QA gate — and the Ragas / DeepEval
    # judges that share it.
    "pipeline_critic_model",
    # Podcast + video script drafting (generate_media_scripts). Non-critical to
    # the post, but the default pipeline calls it, so a fresh install without it
    # logs a "model not found" on every run.
    "video_scene_model",
    # Embeddings (RAG retrieval, originality checks, memory).
    "embedding_model",
)

# Roles a feature uses once it is switched on — NOT part of the first-post pull.
# Listed so the docs' "optional models" table is derived from the same settings
# instead of hand-written: tests/unit/services/test_required_models.py requires
# every tag here to appear in the README and docs/quickstart.mdx.
OPTIONAL_MODEL_KEYS: tuple[str, ...] = (
    # Image QA + captions — only called when a post has images.
    "qa_vision_model",
    # Fallback critic — only used when the primary critic is unavailable.
    "qa_fallback_critic_model",
    # The brain's alert triage / ops firefighter.
    "ops_triage_writer_model",
    # Console chat and the voice agent.
    "console_chat_model",
)

# Values that choose a model at runtime rather than naming one.
_SENTINELS = frozenset({"", "auto"})
# Provider prefixes that address the local Ollama server.
_OLLAMA_PREFIXES = ("ollama/", "ollama_chat/")


def ollama_tag(value: str | None) -> str | None:
    """The Ollama tag a setting value names, or None when it names none.

    ``ollama/gemma3:27b`` -> ``gemma3:27b``; ``nomic-embed-text`` stays as is;
    another provider (``anthropic/…``) or a HuggingFace repo (``org/model``)
    is not an Ollama pull, so None.
    """
    raw = (value or "").strip()
    if raw.lower() in _SENTINELS:
        return None
    lowered = raw.lower()
    for prefix in _OLLAMA_PREFIXES:
        if lowered.startswith(prefix):
            return raw[len(prefix):] or None
    if "/" in raw:
        return None
    return raw


def required_models(
    settings: Mapping[str, str],
    keys: Iterable[str] = PIPELINE_MODEL_KEYS,
) -> list[str]:
    """Distinct Ollama tags the given settings name for ``keys``, in key order."""
    tags: list[str] = []
    for key in keys:
        tag = ollama_tag(settings.get(key))
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def pull_command(tags: Iterable[str]) -> str:
    """``ollama pull a && ollama pull b`` — the README's one-line shape."""
    return " && ".join(f"ollama pull {tag}" for tag in tags)
