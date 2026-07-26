"""Unit tests for the VRAM-budget clamp in services/llm_providers/dispatcher.py.

The clamp (`_clamp_num_ctx_to_budget`) is the pre-load fit guard: before the
GPU lock is acquired it estimates the model footprint at the requested context
and, if that would breach (total - desktop_reserve), reduces num_ctx to the
largest value that still fits — keeping the NVIDIA driver from spilling VRAM
into system RAM (the WDDM freeze). It fails OPEN: if the model arch can't be
read, the requested context passes through unchanged.

No module-level asyncio mark — pyproject `asyncio_mode = "auto"` auto-marks the
coroutine tests; an explicit mark would wrongly tag any sync helper here.
"""


def _budget(*vals):
    """Async stand-in for the now-async ``_budget_inputs`` (resolves the
    gpu_vram_total_gb 'auto' sentinel via the GPU registry, 2026-06-28)."""

    async def _f(_pc):
        return vals

    return _f


def _arch_factory(weight_gb=18):
    from services.vram_budget import ModelArch

    # gemma-4-31B-class illustrative shape; tests assert the clamp relationship,
    # not absolute GB, so the exact numbers only need to be internally usable.
    async def _arch(*_a, **_k):
        return ModelArch(
            n_layers=48, n_kv_heads=8, head_dim=128,
            weight_bytes=weight_gb * 1024**3,
        )

    return _arch


async def test_clamp_num_ctx_reduces_when_over_budget(monkeypatch):
    import services.llm_providers.dispatcher as d

    # f16 KV (2.0 bytes/elem — the dangerous *unquantized* case): a 65k context
    # on an 18GB-weights writer projects ~31.5GB, over the 32-3=29GB budget, so
    # it must clamp down to something that fits.
    monkeypatch.setattr(d, "_budget_inputs", _budget(32.0, 3.0, 2.0))
    monkeypatch.setattr(d, "_read_arch_for_budget", _arch_factory())

    clamped = await d._clamp_num_ctx_to_budget(
        pool=None, model="ollama/gemma-4-31B-it-qat:latest",
        num_ctx=65536, provider_config={},
    )
    assert 0 < clamped < 65536


async def test_clamp_noop_when_within_budget(monkeypatch):
    import services.llm_providers.dispatcher as d

    # q8 KV (1.0) at 8k ctx on the same writer is ~20GB — comfortably inside the
    # 29GB budget, so the requested context passes through untouched.
    monkeypatch.setattr(d, "_budget_inputs", _budget(32.0, 3.0, 1.0))
    monkeypatch.setattr(d, "_read_arch_for_budget", _arch_factory())

    out = await d._clamp_num_ctx_to_budget(
        pool=None, model="m", num_ctx=8192, provider_config={},
    )
    assert out == 8192


async def test_clamp_noop_when_arch_unavailable(monkeypatch):
    import services.llm_providers.dispatcher as d

    async def _none(*_a, **_k):
        return None

    monkeypatch.setattr(d, "_read_arch_for_budget", _none)
    # fail-open: unknown arch -> return the requested num_ctx unchanged
    out = await d._clamp_num_ctx_to_budget(
        pool=None, model="m", num_ctx=8192, provider_config={},
    )
    assert out == 8192


# ---------------------------------------------------------------------------
# num_ctx defaulting — closes the seam gap where a LOCAL dispatch that never
# threaded num_ctx (the vision-QA / media / research paths, not just the
# writer) loaded the model at Ollama's Modelfile default (e.g. gemma-4-31B at
# 262144) and sailed past the clamp, which only fires when num_ctx is present.
# ---------------------------------------------------------------------------


def test_resolve_default_num_ctx_none_for_paid(monkeypatch):
    """A paid/cloud dispatch never gets a defaulted num_ctx — it is meaningless
    there and would trigger a wasted /api/show against a non-local model in the
    clamp below."""
    import services.llm_providers.dispatcher as d

    monkeypatch.setattr(d, "_is_paid_llm_call", lambda _model, _pc: True)
    assert d._resolve_default_num_ctx("qa.vision", "claude-opus-4-8", {}) is None


def test_resolve_default_num_ctx_resolves_for_local(monkeypatch):
    """A local dispatch that didn't thread num_ctx gets the per-phase resolved
    context, so vision / media / research are bounded + clamped like the writer.
    Delegates to resolve_num_ctx with the request's phase and the container's
    SiteConfig (None when no container is bootstrapped)."""
    import services.llm_providers.dispatcher as d

    monkeypatch.setattr(d, "_is_paid_llm_call", lambda _model, _pc: False)
    captured = {}

    def _fake_resolve(phase, *, site_config=None):
        captured["phase"] = phase
        captured["site_config"] = site_config
        return 4096

    monkeypatch.setattr("services.ollama_client.resolve_num_ctx", _fake_resolve)
    monkeypatch.setattr("services.container_registry.get_container", lambda: None)

    out = d._resolve_default_num_ctx(
        "qa.vision", "ollama/gemma-4-31B-it-qat:latest", {},
    )
    assert out == 4096
    assert captured["phase"] == "qa.vision"
    assert captured["site_config"] is None


# ---------------------------------------------------------------------------
# Langfuse session/phase stamping — closes the 2026-07-10 finding that 0/3236
# traces carried a session, so the console task-trace deeplink (which resolves
# task_id as the Langfuse session id) pointed at nothing. dispatch_complete is
# the single choke point every task-attributed LLM call flows through, so it
# stamps the RESERVED Langfuse OTEL attributes on its span:
#   - ``session.id``  (LangfuseOtelSpanAttributes.TRACE_SESSION_ID) -> groups a
#     task's calls into one Langfuse session
#   - ``langfuse.observation.metadata.phase`` -> first-class phase filter
# Both keys are the verified constants from the installed langfuse 4.x SDK; a
# wrong key is a silent no-op (the exact bug class being fixed), so these tests
# pin the literal attribute strings the ingestion honors.
# ---------------------------------------------------------------------------


def _install_recording_dispatch(monkeypatch, d, recorded):
    """Stub every dispatch_complete collaborator on the happy path and install a
    tracer that records ``set_attribute`` calls into ``recorded``."""
    import contextlib

    class _RecordingSpan:
        def set_attribute(self, key, value):
            recorded.append((key, value))

        def record_exception(self, _exc):
            pass

    class _FakeTracer:
        @contextlib.contextmanager
        def start_as_current_span(self, _name):
            yield _RecordingSpan()

    class _FakeCompletion:
        prompt_tokens = 10
        completion_tokens = 5
        finish_reason = "stop"
        text = "the drafted body"

    class _FakeProvider:
        name = "fake"

        async def complete(self, **_kwargs):
            return _FakeCompletion()

    async def _get_provider(_pool, _tier):
        return _FakeProvider()

    async def _get_provider_config(_pool, _name):
        return {}

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(d, "_tracer", _FakeTracer())
    monkeypatch.setattr(d, "get_provider", _get_provider)
    monkeypatch.setattr(d, "get_provider_config", _get_provider_config)
    monkeypatch.setattr(d, "_enforce_budget_if_paid", _noop)
    monkeypatch.setattr(d, "_record_dispatch_cost", _noop)
    monkeypatch.setattr(d, "_vram_guard_enabled", lambda: False)
    monkeypatch.setattr(d, "_gpu_serialize_local_dispatch", lambda _m, _pc: False)


async def test_dispatch_complete_stamps_langfuse_session_and_phase(monkeypatch):
    import services.llm_providers.dispatcher as d

    recorded: list[tuple[str, object]] = []
    _install_recording_dispatch(monkeypatch, d, recorded)

    await d.dispatch_complete(
        pool=object(),
        messages=[{"role": "user", "content": "hi"}],
        model="ollama/gemma-4-31B-it-qat:latest",
        tier="standard",
        task_id="task-abc-123",
        phase="draft_generation",
        num_ctx=8192,  # pre-set so the default/clamp path is skipped
    )

    # session.id groups the task's LLM calls into one Langfuse session.
    assert ("session.id", "task-abc-123") in recorded
    # phase becomes a first-class, filterable observation-metadata field.
    assert ("langfuse.observation.metadata.phase", "draft_generation") in recorded


async def test_dispatch_complete_omits_session_when_no_task_id(monkeypatch):
    import services.llm_providers.dispatcher as d

    recorded: list[tuple[str, object]] = []
    _install_recording_dispatch(monkeypatch, d, recorded)

    await d.dispatch_complete(
        pool=object(),
        messages=[{"role": "user", "content": "hi"}],
        model="ollama/gemma-4-31B-it-qat:latest",
        tier="standard",
        num_ctx=8192,
    )

    # No task context -> no session stamp (never mint an orphan empty session).
    assert not any(key == "session.id" for key, _ in recorded)
    assert not any(key == "langfuse.trace.metadata.task_id" for key, _ in recorded)


async def test_dispatch_complete_always_stamps_trace_metadata_model(monkeypatch):
    """The console's /api/traces list reads TRACE-level metadata.model — the
    model @observe sites stamp lands on the observation, which the trace-list
    API never surfaces (poindexter#902). Every dispatch stamps it, task or not."""
    import services.llm_providers.dispatcher as d

    recorded: list[tuple[str, object]] = []
    _install_recording_dispatch(monkeypatch, d, recorded)

    await d.dispatch_complete(
        pool=object(),
        messages=[{"role": "user", "content": "hi"}],
        model="ollama/gemma-4-31B-it-qat:latest",
        tier="standard",
        num_ctx=8192,
    )

    assert (
        "langfuse.trace.metadata.model",
        "ollama/gemma-4-31B-it-qat:latest",
    ) in recorded


async def test_dispatch_complete_falls_back_to_ambient_task_context(monkeypatch):
    """A call site inside a template run that never threaded task_id still
    groups into the task's Langfuse session via the services.task_context
    binding TemplateRunner.run installs (poindexter#902)."""
    import services.llm_providers.dispatcher as d
    from services.task_context import bind_task_id, reset_task_id

    recorded: list[tuple[str, object]] = []
    _install_recording_dispatch(monkeypatch, d, recorded)

    token = bind_task_id("ambient-task-9")
    try:
        await d.dispatch_complete(
            pool=object(),
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/gemma-4-31B-it-qat:latest",
            tier="standard",
            num_ctx=8192,
        )
    finally:
        reset_task_id(token)

    assert ("session.id", "ambient-task-9") in recorded
    assert ("langfuse.trace.metadata.task_id", "ambient-task-9") in recorded


async def test_dispatch_complete_explicit_task_id_beats_ambient(monkeypatch):
    """The ambient binding is a fallback, never an override."""
    import services.llm_providers.dispatcher as d
    from services.task_context import bind_task_id, reset_task_id

    recorded: list[tuple[str, object]] = []
    _install_recording_dispatch(monkeypatch, d, recorded)

    token = bind_task_id("ambient-task-9")
    try:
        await d.dispatch_complete(
            pool=object(),
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/gemma-4-31B-it-qat:latest",
            tier="standard",
            task_id="explicit-task-1",
            num_ctx=8192,
        )
    finally:
        reset_task_id(token)

    assert ("session.id", "explicit-task-1") in recorded
    assert not any(v == "ambient-task-9" for _, v in recorded)


async def test_dispatch_complete_captures_prompt_and_completion(monkeypatch):
    """Every dispatch call must record its prompt + completion on the span, not
    just token counts. Direct callers (QA rails, etc.) don't go through the
    ollama_chat_text wrapper that captured I/O, so before this the raw
    llm.dispatch_complete span held usage but 0 input/0 output (the 2026-07-10
    finding). Uses the reserved Langfuse attributes so the prompt is visible in
    the trace regardless of caller."""
    import json

    import services.llm_providers.dispatcher as d

    recorded: list[tuple[str, object]] = []
    _install_recording_dispatch(monkeypatch, d, recorded)

    await d.dispatch_complete(
        pool=object(),
        messages=[
            {"role": "system", "content": "you are a writer"},
            {"role": "user", "content": "UNIQUE_PROMPT_MARKER_42"},
        ],
        model="ollama/gemma-4-31B-it-qat:latest",
        tier="standard",
        num_ctx=8192,
    )

    rec = dict(recorded)
    # Input: the full messages payload, JSON-serialized so Langfuse renders it
    # as a chat array (matches _serialize -> json.dumps).
    assert "langfuse.observation.input" in rec
    parsed_input = json.loads(rec["langfuse.observation.input"])
    assert parsed_input == [
        {"role": "system", "content": "you are a writer"},
        {"role": "user", "content": "UNIQUE_PROMPT_MARKER_42"},
    ]
    # Output: the completion text (a string is passed through verbatim).
    assert rec.get("langfuse.observation.output") == "the drafted body"


async def test_dispatch_complete_caps_oversize_span_io(monkeypatch):
    """A pathological payload must not bloat the trace store. The vision rail
    routes base64 image data-URLs through dispatch_complete (multi_model_qa
    _vision_complete), so an uncapped capture would store multi-MB blobs per
    call. _set_span_io truncates the serialized value with a visible marker."""
    import services.llm_providers.dispatcher as d

    recorded: list[tuple[str, object]] = []
    _install_recording_dispatch(monkeypatch, d, recorded)

    big = "A" * 500_000  # stand-in for a base64 image blob
    await d.dispatch_complete(
        pool=object(),
        messages=[{"role": "user", "content": big}],
        model="ollama/gemma-4-31B-it-qat:latest",
        tier="standard",
        num_ctx=8192,
    )

    inp = dict(recorded)["langfuse.observation.input"]
    assert len(inp) < 500_000  # oversize payload capped, not stored whole
    assert "truncated" in inp
