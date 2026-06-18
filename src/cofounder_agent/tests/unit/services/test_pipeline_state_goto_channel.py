"""Guard: _goto is a declared PipelineState channel (QA rescue cycle).

LangGraph silently drops state updates whose keys are not declared in the
TypedDict schema (poindexter#753). qa.aggregate emits _goto to drive the
rescue branch router, so it MUST be a declared channel."""

from __future__ import annotations

import pytest

from services.template_runner import PipelineState


@pytest.mark.unit
def test_goto_is_declared_channel():
    assert "_goto" in PipelineState.__annotations__
