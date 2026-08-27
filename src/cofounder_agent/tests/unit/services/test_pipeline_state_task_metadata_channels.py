"""Guard: every state key ``build_task_metadata`` reads is a declared channel.

LangGraph silently drops state updates whose keys are not declared in the
``PipelineState`` TypedDict (poindexter#753). ``build_task_metadata`` snapshots
the run's state into ``pipeline_tasks.task_metadata``, so an undeclared key
there is not a cosmetic gap — it is a value that a stage computed, LangGraph
threw away at ``ainvoke``, and the operator (or the public site) then read as
empty. The existing ``test_task_metadata_parity_693`` tests call
``build_task_metadata`` with a hand-built state dict, which bypasses LangGraph
entirely — so they stayed green through both regressions below.

Two live regressions this pins (both found 2026-08-27):

* ``vetoed_by`` — qa.aggregate computes the rails that hard-vetoed a draft, and
  the 2026-08-20 "operator saw a flag with no reason" fix persists it as
  ``qa_vetoed_by``. The channel was never declared, so every rejected task
  persisted ``qa_vetoed_by=[]``: a hard veto that named no rail.
* the ``featured_image_*`` scalars — stage.source_featured_image writes alt /
  width / height / photographer / source, and stage.caption_images rewrites the
  alt text from the rendered pixels. All five were dropped, leaving 171 of 193
  published posts with a hero image carrying empty alt text (and zero carrying
  photographer attribution) while the vision re-caption ran on every one.

Deriving the key set from the source rather than hardcoding it means a NEW
``state.get`` in ``task_metadata.py`` is covered the moment it is added — the
point being that this class of bug is invisible until someone reads production
data, so the gate has to be automatic.

Mirrors ``test_pipeline_state_goto_channel.py`` / ``test_pipeline_state_niche_slug.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.template_runner import PipelineState

_TASK_METADATA_SRC = (
    Path(__file__).resolve().parents[3]
    / "modules"
    / "content"
    / "task_metadata.py"
)

# ``state.get("key")`` / ``state.get("key", default)`` — the only way
# build_task_metadata reads the run state.
_STATE_GET_RE = re.compile(r'state\.get\(\s*"([^"]+)"')


def _state_keys_read_by_task_metadata() -> set[str]:
    return set(_STATE_GET_RE.findall(_TASK_METADATA_SRC.read_text()))


@pytest.mark.unit
def test_task_metadata_source_is_readable():
    """Fail loud if the module moved — otherwise the regex silently matches
    nothing and this whole gate passes vacuously."""
    assert _TASK_METADATA_SRC.is_file(), f"{_TASK_METADATA_SRC} not found"
    keys = _state_keys_read_by_task_metadata()
    assert len(keys) > 20, (
        f"only {len(keys)} state.get keys parsed from task_metadata.py — the "
        "read pattern probably changed; update _STATE_GET_RE"
    )


@pytest.mark.unit
def test_every_task_metadata_state_key_is_a_declared_channel():
    declared = set(PipelineState.__annotations__)
    undeclared = sorted(k for k in _state_keys_read_by_task_metadata() if k not in declared)
    assert not undeclared, (
        "build_task_metadata reads state keys that are NOT declared on "
        f"PipelineState: {undeclared}. LangGraph drops undeclared keys at "
        "ainvoke on the graph_def path (the prod path), so these persist as "
        "empty no matter what the producing stage computed. Declare them in "
        "services/template_runner.py::PipelineState."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "key",
    [
        "vetoed_by",
        "featured_image_alt",
        "featured_image_width",
        "featured_image_height",
        "featured_image_photographer",
        "featured_image_source",
    ],
)
def test_regression_channels_stay_declared(key: str):
    """Name the six explicitly so a future refactor of task_metadata.py that
    drops the ``state.get`` call can't quietly retire the guard above."""
    assert key in PipelineState.__annotations__
