"""
Tests for ProcessComposer — intent-to-workflow orchestration layer.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.process_composer import (
    ProcessComposer,
    ProcessPlan,
    ProcessResult,
    StepResult,
    create_default_composer,
)


# ---------------------------------------------------------------------------
# Dataclass behavior
# ---------------------------------------------------------------------------


class TestStepResult:
    def test_success_fields(self):
        r = StepResult(step_name="probe", success=True, output={"ok": 1}, duration_ms=42.5)
        assert r.step_name == "probe"
        assert r.success is True
        assert r.output == {"ok": 1}
        assert r.error is None
        assert r.duration_ms == 42.5

    def test_failure_fields(self):
        r = StepResult(step_name="probe", success=False, error="boom")
        assert r.success is False
        assert r.error == "boom"
        assert r.output is None


class TestProcessResult:
    def test_summary_success(self):
        steps = [
            StepResult(step_name="a", success=True),
            StepResult(step_name="b", success=True),
        ]
        r = ProcessResult(process_name="health_check", intent="check health", success=True, steps=steps)
        assert r.summary == "[health_check] SUCCESS (2/2 steps)"

    def test_summary_partial_failure(self):
        steps = [
            StepResult(step_name="a", success=True),
            StepResult(step_name="b", success=False, error="timeout"),
        ]
        r = ProcessResult(process_name="deploy", intent="deploy", success=False, steps=steps)
        assert r.summary == "[deploy] FAILED (1/2 steps)"

    def test_summary_no_steps(self):
        r = ProcessResult(process_name="empty", intent="noop", success=True, steps=[])
        assert r.summary == "[empty] SUCCESS (0/0 steps)"


# ---------------------------------------------------------------------------
# ProcessPlan
# ---------------------------------------------------------------------------


class TestProcessPlan:
    def setup_method(self):
        self.plan = ProcessPlan(
            plan_id="plan_test_001",
            process_name="health_check",
            intent="check health",
            reason="health check keywords",
            steps=[
                {"name": "probe_site", "category": "monitoring", "description": "Check site HTTP status"},
                {"name": "notify", "category": "notification", "description": "Send notification"},
            ],
        )

    def test_initial_status(self):
        assert self.plan.status == "pending_approval"

    def test_approve(self):
        result = self.plan.approve()
        assert self.plan.status == "approved"
        assert result is self.plan  # returns self for chaining

    def test_reject_without_reason(self):
        result = self.plan.reject()
        assert self.plan.status == "rejected"
        assert "rejection_reason" not in self.plan.context
        assert result is self.plan

    def test_reject_with_reason(self):
        self.plan.reject(reason="too expensive")
        assert self.plan.status == "rejected"
        assert self.plan.context["rejection_reason"] == "too expensive"

    def test_summary_contains_key_info(self):
        s = self.plan.summary
        assert "plan_test_001" in s
        assert "health_check" in s
        assert "check health" in s
        assert "pending_approval" in s
        assert "probe_site" in s
        assert "notify" in s
        assert "Steps (2):" in s


# ---------------------------------------------------------------------------
# ProcessComposer core
# ---------------------------------------------------------------------------


class TestProcessComposer:
    def setup_method(self):
        self.composer = ProcessComposer()

    # -- Registration -------------------------------------------------------

    def test_register_step(self):
        async def dummy(**kw):
            return {}

        self.composer.register_step(
            "my_step", dummy, description="does stuff", category="test",
            requires=["intent"], produces=["result"],
        )
        steps = self.composer.list_steps()
        assert len(steps) == 1
        assert steps[0]["name"] == "my_step"
        assert steps[0]["category"] == "test"
        assert steps[0]["requires"] == ["intent"]
        assert steps[0]["produces"] == ["result"]

    def test_register_process(self):
        self.composer.register_process("my_flow", ["step_a", "step_b"])
        procs = self.composer.list_processes()
        assert procs == {"my_flow": ["step_a", "step_b"]}

    def test_list_steps_empty(self):
        assert self.composer.list_steps() == []

    def test_list_processes_empty(self):
        assert self.composer.list_processes() == {}

    # -- Keyword classification ---------------------------------------------

    def test_keyword_classify_content(self):
        for phrase in ["write a blog post", "create content about AI", "article on GPUs"]:
            result = self.composer._keyword_classify(phrase)
            assert result["process_name"] == "create_content"
            assert "create_task" in result["steps"]

    def test_keyword_classify_health(self):
        for phrase in ["check site health", "is the API alive", "system status"]:
            result = self.composer._keyword_classify(phrase)
            assert result["process_name"] == "health_check"
            assert "probe_site" in result["steps"]
            assert "probe_api" in result["steps"]

    def test_keyword_classify_publish(self):
        for phrase in ["publish the draft", "approve the release"]:
            result = self.composer._keyword_classify(phrase)
            assert result["process_name"] == "publish_content", f"Failed for: {phrase}"

    def test_keyword_classify_budget(self):
        for phrase in ["what is the cost", "show me the spend", "budget overview", "save money"]:
            result = self.composer._keyword_classify(phrase)
            assert result["process_name"] == "cost_report", f"Failed for: {phrase}"
            assert "check_budget" in result["steps"]

    def test_keyword_classify_unknown(self):
        result = self.composer._keyword_classify("do a backflip")
        assert result["process_name"] == "unknown"
        assert result["steps"] == []

    # -- classify_intent (async, LLM fallback to keyword) -------------------

    @pytest.mark.asyncio
    async def test_classify_intent_falls_back_to_keyword(self):
        """With no model_router, classify_intent uses keyword fallback."""
        result = await self.composer.classify_intent("check site health")
        assert result["process_name"] == "health_check"

    @pytest.mark.asyncio
    async def test_classify_intent_uses_router_when_available(self):
        mock_router = AsyncMock()
        mock_router.route_request.return_value = {
            "content": '{"process_name": "custom", "steps": ["step_x"], "reason": "LLM said so"}'
        }
        self.composer.router = mock_router
        result = await self.composer.classify_intent("do something novel")
        assert result["process_name"] == "custom"
        assert result["steps"] == ["step_x"]

    @pytest.mark.asyncio
    async def test_classify_intent_llm_failure_falls_back(self):
        mock_router = AsyncMock()
        mock_router.route_request.side_effect = Exception("LLM down")
        self.composer.router = mock_router
        result = await self.composer.classify_intent("write a blog post")
        assert result["process_name"] == "create_content"


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class TestProcessComposerExecution:
    def setup_method(self):
        self.composer = ProcessComposer()

        self.step_a = AsyncMock(return_value={"a_output": 1})
        self.step_b = AsyncMock(return_value={"b_output": 2})
        self.step_fail = AsyncMock(side_effect=ValueError("step exploded"))

        self.composer.register_step("step_a", self.step_a, description="Step A", category="test")
        self.composer.register_step("step_b", self.step_b, description="Step B", category="test")
        self.composer.register_step("step_fail", self.step_fail, description="Failing step", category="test")

    @pytest.mark.asyncio
    async def test_execute_runs_matched_steps(self):
        """execute() classifies intent and runs matching steps."""
        self.composer.register_process("create_content", ["step_a", "step_b"])
        result = await self.composer.execute("write a blog post about AI")
        assert result.success is True
        assert len(result.steps) == 2
        assert result.steps[0].step_name == "step_a"
        assert result.steps[1].step_name == "step_b"
        self.step_a.assert_awaited_once()
        self.step_b.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_context_passed_to_steps(self):
        """Context dict and intent are forwarded to step functions."""
        self.composer.register_process("create_content", ["step_a"])
        await self.composer.execute("write a post", context={"custom_key": "val"})
        call_kwargs = self.step_a.call_args[1]
        assert call_kwargs["custom_key"] == "val"
        assert call_kwargs["intent"] == "write a post"

    @pytest.mark.asyncio
    async def test_execute_pipeline_context_accumulates(self):
        """Output from step_a is available to step_b via pipeline context."""
        self.composer.register_process("create_content", ["step_a", "step_b"])
        await self.composer.execute("write a post")
        # step_b should receive a_output from step_a's dict output
        call_kwargs = self.step_b.call_args[1]
        assert call_kwargs["a_output"] == 1

    @pytest.mark.asyncio
    async def test_execute_no_valid_steps(self):
        """When no steps match, result is failure with classification error."""
        result = await self.composer.execute("do a backflip")
        assert result.success is False
        assert result.steps[0].error == "No valid steps found"

    @pytest.mark.asyncio
    async def test_execute_steps_handles_failure_gracefully(self):
        """A failing step records the error but doesn't crash the pipeline."""
        self.composer.register_process("create_content", ["step_fail", "step_b"])
        result = await self.composer.execute("write a post")
        assert result.success is False
        assert result.steps[0].step_name == "step_fail"
        assert result.steps[0].success is False
        assert "step exploded" in result.steps[0].error
        # step_b still runs after the failure
        assert result.steps[1].step_name == "step_b"
        assert result.steps[1].success is True

    @pytest.mark.asyncio
    async def test_execute_steps_sets_last_error_on_failure(self):
        """After a step fails, last_error is added to pipeline context."""
        self.composer.register_process("create_content", ["step_fail", "step_b"])
        await self.composer.execute("write a post")
        call_kwargs = self.step_b.call_args[1]
        assert "step exploded" in call_kwargs["last_error"]

    @pytest.mark.asyncio
    async def test_execute_steps_records_duration(self):
        self.composer.register_process("create_content", ["step_a"])
        result = await self.composer.execute("write a post")
        assert result.steps[0].duration_ms >= 0


# ---------------------------------------------------------------------------
# Plan + execute_plan
# ---------------------------------------------------------------------------


class TestProcessComposerPlan:
    def setup_method(self):
        self.composer = ProcessComposer()
        self.step_a = AsyncMock(return_value={"done": True})
        self.composer.register_step("create_task", self.step_a, description="Create task", category="content")
        self.composer.register_step("notify", AsyncMock(return_value={"notified": True}),
                                    description="Notify", category="notification")
        self.composer.register_process("create_content", ["create_task", "notify"])

    @pytest.mark.asyncio
    async def test_plan_creates_pending_plan(self):
        plan = await self.composer.plan("write a blog post")
        assert isinstance(plan, ProcessPlan)
        assert plan.status == "pending_approval"
        assert plan.process_name == "create_content"
        assert plan.intent == "write a blog post"
        assert len(plan.steps) >= 1
        assert plan.plan_id.startswith("plan_")

    @pytest.mark.asyncio
    async def test_plan_includes_step_metadata(self):
        plan = await self.composer.plan("write a blog post")
        step_names = [s["name"] for s in plan.steps]
        assert "create_task" in step_names
        # Each step has description and category
        for s in plan.steps:
            assert "description" in s
            assert "category" in s

    @pytest.mark.asyncio
    async def test_plan_with_context(self):
        plan = await self.composer.plan("write a post", context={"priority": "high"})
        assert plan.context["priority"] == "high"

    @pytest.mark.asyncio
    async def test_execute_plan_approved(self):
        plan = await self.composer.plan("write a blog post")
        plan.approve()
        result = await self.composer.execute_plan(plan)
        assert result.success is True
        assert result.process_name == "create_content"

    @pytest.mark.asyncio
    async def test_execute_plan_not_approved(self):
        plan = await self.composer.plan("write a blog post")
        # Don't approve — still pending
        result = await self.composer.execute_plan(plan)
        assert result.success is False
        assert "not approved" in result.steps[0].error.lower()

    @pytest.mark.asyncio
    async def test_execute_plan_rejected(self):
        plan = await self.composer.plan("write a blog post")
        plan.reject(reason="not now")
        result = await self.composer.execute_plan(plan)
        assert result.success is False
        assert "rejected" in result.steps[0].error.lower()

    @pytest.mark.asyncio
    async def test_plan_filters_unregistered_steps(self):
        """Steps returned by classifier that aren't registered are excluded."""
        # keyword classifier returns ["create_task", "notify"] for content intent
        # Remove "notify" registration — only create_task should survive
        del self.composer._steps["notify"]
        plan = await self.composer.plan("write a blog post")
        step_names = [s["name"] for s in plan.steps]
        assert "create_task" in step_names
        assert "notify" not in step_names


# ---------------------------------------------------------------------------
# create_default_composer factory
# ---------------------------------------------------------------------------


class TestCreateDefaultComposer:
    def test_registers_expected_steps(self):
        composer = create_default_composer()
        steps = {s["name"] for s in composer.list_steps()}
        assert steps == {"create_task", "probe_site", "probe_api", "check_budget", "notify"}

    def test_registers_expected_processes(self):
        composer = create_default_composer()
        procs = composer.list_processes()
        assert "create_content" in procs
        assert "health_check" in procs
        assert "cost_report" in procs
        assert procs["health_check"] == ["probe_site", "probe_api", "notify"]

    def test_step_categories(self):
        composer = create_default_composer()
        steps = {s["name"]: s["category"] for s in composer.list_steps()}
        assert steps["probe_site"] == "monitoring"
        assert steps["probe_api"] == "monitoring"
        assert steps["check_budget"] == "cost"
        assert steps["notify"] == "notification"
        assert steps["create_task"] == "content"

    def test_passes_settings_and_router(self):
        mock_settings = MagicMock()
        mock_router = MagicMock()
        composer = create_default_composer(settings_service=mock_settings, model_router=mock_router)
        assert composer.settings is mock_settings
        assert composer.router is mock_router
