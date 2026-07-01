"""Glad Labs operator model overlay — PRIVATE (stripped from the public mirror).

These pins re-apply Matt's custom local Ollama models over the public OSS
defaults on a fresh install or a settings reset, via
``services.settings_defaults.apply_operator_model_overrides`` (which overwrites
a key only when it still holds the OSS default, so live tuning survives a
reboot).

Every model here is a custom local build that is NOT on the public Ollama
registry, so it must never become an OSS default. That's enforced from the other
direction by ``tests/unit/services/test_oss_seed_model_hygiene.py`` (it scans the
public seed files — ``settings_defaults.DEFAULTS`` and ``0000_baseline.seeds.sql``
— for these tags). This module is the one legitimate home for them, and
``scripts/sync-to-github.sh`` strips it from the ``poindexter`` public mirror.

To change a model on the operator rig: edit the value here and reboot the worker,
or ``poindexter settings set <key> <model>`` for a live (non-persistent) change.
Values mirror the live prod ``app_settings`` as of 2026-06-30.
"""

from __future__ import annotations

# Setting key -> the operator's custom local Ollama model tag.
OPERATOR_MODEL_PINS: dict[str, str] = {
    # gemma-4-31B-it-qat — custom QAT daily-driver writer (2026-06-18 bakeoff
    # winner) and the writer-grade roles that share it.
    "pipeline_writer_model": "ollama/gemma-4-31B-it-qat:latest",
    "pipeline_fallback_model": "ollama/gemma-4-31B-it-qat:latest",
    "video_director_model": "ollama/gemma-4-31B-it-qat:latest",
    "structured_extraction_model": "ollama/gemma-4-31B-it-qat:latest",
    "image_prompt_model": "ollama/gemma-4-31B-it-qat:latest",
    "image_search_query_model": "ollama/gemma-4-31B-it-qat:latest",
    "writer_self_review_model": "ollama/gemma-4-31B-it-qat:latest",
    "qa_fallback_writer_model": "ollama/gemma-4-31B-it-qat:latest",
    "podcast_script_model": "ollama/gemma-4-31B-it-qat:latest",
    "preferred_ollama_model": "gemma-4-31B-it-qat:latest",
    # glm-4.7-5090 — custom RTX 5090 fine-tune (pipeline architect).
    "pipeline_architect_model": "ollama/glm-4.7-5090:latest",
    # gemma-4-E2B-Q2 — tiny custom quant for the low-latency voice agent.
    "voice_agent_llm_model": "ollama/gemma-4-E2B-Q2:latest",
}
