"""
Process Composer — intent-to-workflow orchestration layer.

Reads intent (natural language or structured) and dynamically assembles
business processes from registered building blocks. This is the brain
that turns "do X" into a chain of executable steps.

Building blocks are registered as "capabilities" — functions that take
inputs and produce outputs. The composer chains them based on intent.

Usage:
    composer = ProcessComposer(settings_service=settings)
    result = await composer.execute("Write a blog post about AI orchestration")
    result = await composer.execute("Check if the site is healthy")
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    """Result from a single process step."""
    step_name: str
    success: bool
    output: Any = None
    error: Optional[str] = None
    duration_ms: float = 0


@dataclass
class ProcessResult:
    """Result from a complete process execution."""
    process_name: str
    intent: str
    success: bool
    steps: List[StepResult] = field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    @property
    def summary(self) -> str:
        status = "SUCCESS" if self.success else "FAILED"
        passed = sum(1 for s in self.steps if s.success)
        return f"[{self.process_name}] {status} ({passed}/{len(self.steps)} steps)"


# Type for step functions
StepFn = Callable[..., Coroutine[Any, Any, Any]]


@dataclass
class StepDefinition:
    """A registered process step (building block)."""
    name: str
    fn: StepFn
    description: str = ""
    category: str = "general"  # content, qa, notification, monitoring, etc.
    requires: List[str] = field(default_factory=list)  # Input keys needed
    produces: List[str] = field(default_factory=list)  # Output keys produced


INTENT_CLASSIFIER_PROMPT = """You are a business process router. Given a user intent, select the right steps to execute.

Available steps (building blocks):
{step_catalog}

User intent: "{intent}"

Rules:
- Pick ONLY steps that are relevant to the intent
- Order them logically (dependencies first)
- Include error handling steps if the process could fail
- Keep it minimal — don't add unnecessary steps

Return ONLY valid JSON:
{{"process_name": "descriptive_name", "steps": ["step1", "step2", "step3"], "reason": "brief explanation"}}
"""


class ProcessComposer:
    """Orchestrates business processes by composing registered steps based on intent."""

    def __init__(self, settings_service=None, model_router=None):
        self.settings = settings_service
        self.router = model_router
        self._steps: Dict[str, StepDefinition] = {}
        self._predefined_processes: Dict[str, List[str]] = {}

    def register_step(
        self, name: str, fn: StepFn, description: str = "",
        category: str = "general", requires: Optional[List[str]] = None,
        produces: Optional[List[str]] = None,
    ) -> None:
        """Register a building block step."""
        self._steps[name] = StepDefinition(
            name=name, fn=fn, description=description, category=category,
            requires=requires or [], produces=produces or [],
        )
        logger.info("[COMPOSER] Registered step: %s (%s)", name, category)

    def register_process(self, name: str, steps: List[str]) -> None:
        """Register a predefined process (shortcut for known workflows)."""
        self._predefined_processes[name] = steps

    async def classify_intent(self, intent: str) -> Dict[str, Any]:
        """Use LLM to classify intent and select process steps."""
        step_catalog = "\n".join(
            f"- {s.name} [{s.category}]: {s.description} "
            f"(needs: {s.requires or 'nothing'}, produces: {s.produces or 'nothing'})"
            for s in self._steps.values()
        )

        prompt = INTENT_CLASSIFIER_PROMPT.format(
            step_catalog=step_catalog, intent=intent,
        )

        # Try LLM classification
        if self.router:
            try:
                response = await self.router.route_request(
                    prompt=prompt, cost_tier="free", task_type="intent_classification",
                )
                if response and response.get("content"):
                    text = response["content"]
                    match = re.search(r"\{[^{}]*\"steps\"[^{}]*\}", text)
                    if match:
                        return json.loads(match.group(0))
            except Exception as e:
                logger.warning("[COMPOSER] LLM classification failed: %s", e)

        # Fallback: keyword matching
        return self._keyword_classify(intent)

    def _keyword_classify(self, intent: str) -> Dict[str, Any]:
        """Simple keyword-based intent classification as fallback."""
        lower = intent.lower()

        if any(k in lower for k in ["write", "post", "blog", "article", "content"]):
            return {"process_name": "create_content", "steps": ["create_task", "notify"], "reason": "content creation keywords"}
        elif any(k in lower for k in ["health", "check", "status", "alive", "working"]):
            return {"process_name": "health_check", "steps": ["probe_site", "probe_api", "notify"], "reason": "health check keywords"}
        elif any(k in lower for k in ["publish", "approve", "push live"]):
            return {"process_name": "publish_content", "steps": ["approve_task", "publish_task", "notify"], "reason": "publish keywords"}
        elif any(k in lower for k in ["cost", "spend", "budget", "money"]):
            return {"process_name": "cost_report", "steps": ["check_budget", "notify"], "reason": "cost keywords"}

        return {"process_name": "unknown", "steps": [], "reason": "no matching intent"}

    async def execute(self, intent: str, context: Optional[Dict[str, Any]] = None) -> ProcessResult:
        """
        Execute a business process from natural language intent.

        1. Classify the intent
        2. Select/compose the step chain
        3. Execute each step in order
        4. Return aggregated results
        """
        import time
        context = context or {}
        started = datetime.now(timezone.utc)

        # Classify intent
        classification = await self.classify_intent(intent)
        process_name = classification.get("process_name", "unknown")
        step_names = classification.get("steps", [])

        logger.info(
            "[COMPOSER] Intent: '%s' -> process '%s' with steps %s (reason: %s)",
            intent[:60], process_name, step_names, classification.get("reason", ""),
        )

        # Check predefined processes first
        if process_name in self._predefined_processes:
            step_names = self._predefined_processes[process_name]

        # Filter to registered steps only
        valid_steps = [s for s in step_names if s in self._steps]
        if not valid_steps:
            return ProcessResult(
                process_name=process_name, intent=intent, success=False,
                steps=[StepResult(step_name="classification", success=False, error="No valid steps found")],
                started_at=started, completed_at=datetime.now(timezone.utc),
            )

        # Execute steps in order, passing context between them
        results: List[StepResult] = []
        pipeline_context = {**context, "intent": intent, "process_name": process_name}

        for step_name in valid_steps:
            step_def = self._steps[step_name]
            step_start = time.monotonic()

            try:
                output = await step_def.fn(**pipeline_context)
                duration = (time.monotonic() - step_start) * 1000

                # Merge step output into context for next steps
                if isinstance(output, dict):
                    pipeline_context.update(output)

                results.append(StepResult(
                    step_name=step_name, success=True, output=output, duration_ms=duration,
                ))
            except Exception as e:
                duration = (time.monotonic() - step_start) * 1000
                logger.error("[COMPOSER] Step %s failed: %s", step_name, e, exc_info=True)
                results.append(StepResult(
                    step_name=step_name, success=False, error=str(e)[:200], duration_ms=duration,
                ))
                # Don't stop on failure — let subsequent steps handle it
                pipeline_context["last_error"] = str(e)

        success = all(r.success for r in results)
        return ProcessResult(
            process_name=process_name, intent=intent, success=success,
            steps=results, started_at=started, completed_at=datetime.now(timezone.utc),
        )

    def list_steps(self) -> List[Dict[str, Any]]:
        """List all registered steps."""
        return [
            {"name": s.name, "category": s.category, "description": s.description,
             "requires": s.requires, "produces": s.produces}
            for s in self._steps.values()
        ]

    def list_processes(self) -> Dict[str, List[str]]:
        """List all predefined processes."""
        return dict(self._predefined_processes)


# ============================================================================
# BUILT-IN STEPS
# ============================================================================

async def step_create_task(intent: str = "", **kwargs) -> dict:
    """Create a content task from intent."""
    import urllib.request
    API_URL = "https://cofounder-production.up.railway.app"
    API_TOKEN = kwargs.get("api_token", "")

    # Extract topic from intent
    topic = intent
    for prefix in ["write a post about ", "write about ", "create content about ", "blog about "]:
        if topic.lower().startswith(prefix):
            topic = topic[len(prefix):]
            break

    payload = json.dumps({
        "task_name": f"Blog post: {topic}",
        "topic": topic,
        "category": "technology",
        "target_audience": "developers and founders",
    }).encode()

    req = urllib.request.Request(
        f"{API_URL}/api/tasks",
        data=payload,
        headers={"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read())
    return {"task_id": data.get("task_id"), "topic": topic, "status": "pending"}


async def step_probe_site(**kwargs) -> dict:
    """Check if gladlabs.io is responding."""
    import urllib.request
    try:
        req = urllib.request.Request("https://gladlabs.io")
        resp = urllib.request.urlopen(req, timeout=10)
        return {"site_status": resp.status, "site_healthy": resp.status == 200}
    except Exception as e:
        return {"site_status": 0, "site_healthy": False, "site_error": str(e)[:100]}


async def step_probe_api(**kwargs) -> dict:
    """Check if the backend API is healthy."""
    import urllib.request
    try:
        resp = urllib.request.urlopen("https://cofounder-production.up.railway.app/api/health", timeout=10)
        data = json.loads(resp.read())
        return {"api_status": data.get("status"), "api_healthy": data.get("status") in ("healthy", "degraded")}
    except Exception as e:
        return {"api_status": "unreachable", "api_healthy": False, "api_error": str(e)[:100]}


async def step_check_budget(**kwargs) -> dict:
    """Check current spending status."""
    import urllib.request
    try:
        resp = urllib.request.urlopen("https://cofounder-production.up.railway.app/api/metrics/costs/budget", timeout=10)
        return {"budget": json.loads(resp.read())}
    except Exception:
        return {"budget": {"error": "Could not fetch budget"}}


async def step_notify(intent: str = "", process_name: str = "", **kwargs) -> dict:
    """Send a notification about the process result."""
    # This would call the notification system
    logger.info("[NOTIFY] Process '%s' completed for intent: %s", process_name, intent[:50])
    return {"notified": True}


# ============================================================================
# DEFAULT COMPOSER SETUP
# ============================================================================

def create_default_composer(settings_service=None, model_router=None) -> ProcessComposer:
    """Create a composer with built-in steps and processes."""
    composer = ProcessComposer(settings_service=settings_service, model_router=model_router)

    # Register steps
    composer.register_step("create_task", step_create_task, "Create a content task", "content",
                          requires=["intent"], produces=["task_id", "topic"])
    composer.register_step("probe_site", step_probe_site, "Check gladlabs.io HTTP status", "monitoring",
                          produces=["site_status", "site_healthy"])
    composer.register_step("probe_api", step_probe_api, "Check backend API health", "monitoring",
                          produces=["api_status", "api_healthy"])
    composer.register_step("check_budget", step_check_budget, "Check spending limits", "cost",
                          produces=["budget"])
    composer.register_step("notify", step_notify, "Send notification about results", "notification")

    # Register predefined processes
    composer.register_process("create_content", ["create_task", "notify"])
    composer.register_process("health_check", ["probe_site", "probe_api", "notify"])
    composer.register_process("cost_report", ["check_budget", "notify"])

    return composer
