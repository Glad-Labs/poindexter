"""
QA Registry — composable, reusable quality assurance workflows.

Each QA workflow is a named chain of reviewers. Reviewers are pluggable
functions that take content and return a pass/fail with feedback.
Workflows are composed at runtime from the registry.

Usage:
    registry = QARegistry()
    registry.register_reviewer("factual_validator", factual_validator_fn)
    registry.register_reviewer("llm_critic", llm_critic_fn)
    registry.register_workflow("blog_content", ["factual_validator", "llm_critic"])

    result = await registry.run_workflow("blog_content", content=text, title=title)
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ReviewResult:
    """Result from a single reviewer."""
    reviewer_name: str
    passed: bool
    score: float  # 0-100
    feedback: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowResult:
    """Aggregated result from a QA workflow."""
    workflow_name: str
    passed: bool
    final_score: float
    reviews: List[ReviewResult] = field(default_factory=list)
    stop_reason: Optional[str] = None  # If workflow stopped early

    @property
    def summary(self) -> str:
        status = "PASSED" if self.passed else "FAILED"
        lines = [f"[{self.workflow_name}] {status} (score: {self.final_score:.0f})"]
        for r in self.reviews:
            icon = "pass" if r.passed else "FAIL"
            lines.append(f"  {r.reviewer_name}: {r.score:.0f} [{icon}] {r.feedback[:80]}")
        if self.stop_reason:
            lines.append(f"  Stopped: {self.stop_reason}")
        return "\n".join(lines)


# Type for reviewer functions
# Each reviewer receives keyword args (content, title, topic, etc.)
# and returns a ReviewResult
ReviewerFn = Callable[..., Coroutine[Any, Any, ReviewResult]]


@dataclass
class ReviewerConfig:
    """Configuration for a registered reviewer."""
    name: str
    fn: ReviewerFn
    weight: float = 1.0  # Weight in score aggregation
    stop_on_fail: bool = False  # If True, workflow stops if this reviewer fails
    model: Optional[str] = None  # Configurable model for LLM-based reviewers
    description: str = ""


@dataclass
class WorkflowConfig:
    """Configuration for a QA workflow."""
    name: str
    reviewer_names: List[str]
    min_score: float = 70.0  # Minimum score to pass
    require_unanimous: bool = False  # All reviewers must pass
    description: str = ""


class QARegistry:
    """Registry for QA reviewers and workflows.

    Reviewers are pluggable functions. Workflows are named chains of reviewers.
    Models are configurable per reviewer via settings_service.
    """

    def __init__(self, settings_service=None):
        self.settings = settings_service
        self._reviewers: Dict[str, ReviewerConfig] = {}
        self._workflows: Dict[str, WorkflowConfig] = {}

    def register_reviewer(
        self,
        name: str,
        fn: ReviewerFn,
        weight: float = 1.0,
        stop_on_fail: bool = False,
        model: Optional[str] = None,
        description: str = "",
    ) -> None:
        """Register a reviewer function."""
        self._reviewers[name] = ReviewerConfig(
            name=name, fn=fn, weight=weight, stop_on_fail=stop_on_fail,
            model=model, description=description,
        )
        logger.info("[QA_REGISTRY] Registered reviewer: %s", name)

    def register_workflow(
        self,
        name: str,
        reviewer_names: List[str],
        min_score: float = 70.0,
        require_unanimous: bool = False,
        description: str = "",
    ) -> None:
        """Register a QA workflow as a chain of reviewers."""
        # Validate all reviewers exist
        for rn in reviewer_names:
            if rn not in self._reviewers:
                logger.warning("[QA_REGISTRY] Workflow '%s' references unknown reviewer '%s'", name, rn)
        self._workflows[name] = WorkflowConfig(
            name=name, reviewer_names=reviewer_names, min_score=min_score,
            require_unanimous=require_unanimous, description=description,
        )
        logger.info("[QA_REGISTRY] Registered workflow: %s (%d reviewers)", name, len(reviewer_names))

    async def run_workflow(self, workflow_name: str, **kwargs) -> WorkflowResult:
        """
        Run a QA workflow against content.

        Args:
            workflow_name: Name of the registered workflow
            **kwargs: Passed to each reviewer (content, title, topic, etc.)

        Returns:
            WorkflowResult with aggregated pass/fail and individual reviews
        """
        workflow = self._workflows.get(workflow_name)
        if not workflow:
            return WorkflowResult(
                workflow_name=workflow_name, passed=False, final_score=0,
                stop_reason=f"Unknown workflow: {workflow_name}",
            )

        reviews: List[ReviewResult] = []
        stop_reason = None

        for reviewer_name in workflow.reviewer_names:
            reviewer = self._reviewers.get(reviewer_name)
            if not reviewer:
                logger.warning("[QA_REGISTRY] Skipping unknown reviewer: %s", reviewer_name)
                continue

            # Get model override from settings if available
            model = reviewer.model
            if self.settings and model is None:
                setting_key = f"qa_reviewer_{reviewer_name}_model"
                model = await self.settings.get(setting_key)

            try:
                result = await reviewer.fn(model=model, **kwargs)
                reviews.append(result)
                logger.info(
                    "[QA_REGISTRY] %s.%s: score=%.0f passed=%s",
                    workflow_name, reviewer_name, result.score, result.passed,
                )
            except Exception as e:
                logger.error("[QA_REGISTRY] Reviewer %s failed: %s", reviewer_name, e, exc_info=True)
                reviews.append(ReviewResult(
                    reviewer_name=reviewer_name, passed=False, score=0,
                    feedback=f"Reviewer error: {str(e)[:100]}",
                ))

            # Check stop_on_fail
            if reviewer.stop_on_fail and reviews[-1] and not reviews[-1].passed:
                stop_reason = f"Stopped by {reviewer_name} (stop_on_fail=True)"
                break

        # Aggregate scores
        if reviews:
            total_weight = sum(
                self._reviewers[r.reviewer_name].weight
                for r in reviews if r.reviewer_name in self._reviewers
            )
            if total_weight > 0:
                final_score = sum(
                    r.score * self._reviewers.get(r.reviewer_name, ReviewerConfig(name="", fn=lambda: None)).weight
                    for r in reviews if r.reviewer_name in self._reviewers
                ) / total_weight
            else:
                final_score = sum(r.score for r in reviews) / len(reviews)
        else:
            final_score = 0

        # Determine pass/fail
        if workflow.require_unanimous:
            passed = all(r.passed for r in reviews) and final_score >= workflow.min_score
        else:
            passed = final_score >= workflow.min_score

        result = WorkflowResult(
            workflow_name=workflow_name, passed=passed, final_score=final_score,
            reviews=reviews, stop_reason=stop_reason,
        )
        logger.info("[QA_REGISTRY] %s", result.summary.split("\n")[0])
        return result

    def list_workflows(self) -> List[Dict[str, Any]]:
        """List all registered workflows."""
        return [
            {
                "name": w.name,
                "reviewers": w.reviewer_names,
                "min_score": w.min_score,
                "require_unanimous": w.require_unanimous,
                "description": w.description,
            }
            for w in self._workflows.values()
        ]

    def list_reviewers(self) -> List[Dict[str, Any]]:
        """List all registered reviewers."""
        return [
            {
                "name": r.name,
                "weight": r.weight,
                "stop_on_fail": r.stop_on_fail,
                "model": r.model,
                "description": r.description,
            }
            for r in self._reviewers.values()
        ]

    async def load_dynamic_workflows(self) -> None:
        """Load workflow definitions from app_settings.

        Settings format (key: qa_workflow_{name}, value: JSON):
            {"reviewers": ["programmatic_validator", "seo_checker"],
             "min_score": 70, "require_unanimous": false,
             "description": "Blog content QA"}

        This allows workflows to be created/modified at runtime via
        OpenClaw or the settings API without code changes.
        """
        if not self.settings:
            return

        import json
        all_settings = await self.settings.get_by_category("qa_workflows")
        for key, value in all_settings.items():
            if not key.startswith("qa_workflow_"):
                continue
            workflow_name = key.replace("qa_workflow_", "")
            try:
                config = json.loads(value) if isinstance(value, str) else value
                self.register_workflow(
                    name=workflow_name,
                    reviewer_names=config.get("reviewers", []),
                    min_score=config.get("min_score", 70.0),
                    require_unanimous=config.get("require_unanimous", False),
                    description=config.get("description", f"Dynamic workflow: {workflow_name}"),
                )
                logger.info("[QA_REGISTRY] Loaded dynamic workflow: %s", workflow_name)
            except Exception as e:
                logger.warning("[QA_REGISTRY] Failed to load workflow %s: %s", key, e)


    async def select_workflow_for_task(
        self, topic: str, category: str = "", target_audience: str = "", task_type: str = "blog_post",
    ) -> str:
        """
        Dynamically select or build a QA workflow based on task intent.

        Uses a lightweight LLM call to analyze the task and pick the best
        reviewer chain from registered reviewers. Falls back to "blog_content"
        if the LLM is unavailable.

        Returns the workflow name (creates it dynamically if needed).
        """
        available_reviewers = self.list_reviewers()
        if not available_reviewers:
            return "blog_content"

        # Build context for the selector
        reviewer_catalog = "\n".join(
            f"- {r['name']}: {r['description']} (weight={r['weight']}, stop_on_fail={r['stop_on_fail']})"
            for r in available_reviewers
        )

        selector_prompt = f"""You are a QA workflow selector. Given a content task, pick the right reviewers.

Available reviewers:
{reviewer_catalog}

Task:
- Topic: {topic}
- Category: {category}
- Audience: {target_audience}
- Type: {task_type}

Rules:
- programmatic_validator should ALWAYS be first (catches hallucinations)
- Technical content should include code/accuracy reviewers
- SEO content should include seo_checker
- Opinion/editorial needs tone_checker if available
- More reviewers = slower but higher quality

Return ONLY valid JSON:
{{"workflow_name": "auto_{task_type}", "reviewers": ["reviewer1", "reviewer2"], "min_score": 70, "reason": "brief explanation"}}
"""

        try:
            from services.model_router import get_model_router
            router = get_model_router()
            if router:
                response = await router.route_request(
                    prompt=selector_prompt,
                    cost_tier="free",  # Use Ollama for this — it's a simple classification
                    task_type="qa_selector",
                )
                if response and response.get("content"):
                    import json as _json
                    import re
                    text = response["content"]
                    # Extract JSON
                    match = re.search(r"\{[^{}]*\"reviewers\"[^{}]*\}", text)
                    if match:
                        config = _json.loads(match.group(0))
                        workflow_name = config.get("workflow_name", f"auto_{task_type}")
                        reviewers = config.get("reviewers", [])

                        # Filter to only registered reviewers
                        valid_reviewers = [r for r in reviewers if r in self._reviewers]
                        if not valid_reviewers:
                            valid_reviewers = ["programmatic_validator"]

                        # Ensure programmatic_validator is always first
                        if "programmatic_validator" in valid_reviewers:
                            valid_reviewers.remove("programmatic_validator")
                        valid_reviewers.insert(0, "programmatic_validator")

                        # Register the dynamic workflow
                        self.register_workflow(
                            name=workflow_name,
                            reviewer_names=valid_reviewers,
                            min_score=config.get("min_score", 70.0),
                            description=config.get("reason", f"Auto-selected for: {topic[:50]}"),
                        )
                        logger.info(
                            "[QA_REGISTRY] Auto-selected workflow '%s': %s (reason: %s)",
                            workflow_name, valid_reviewers, config.get("reason", ""),
                        )
                        return workflow_name
        except Exception as e:
            logger.warning("[QA_REGISTRY] Workflow auto-selection failed, using default: %s", e)

        return "blog_content"


# ============================================================================
# BUILT-IN REVIEWERS
# ============================================================================

async def programmatic_validator_reviewer(
    content: str = "", title: str = "", topic: str = "", **kwargs
) -> ReviewResult:
    """Programmatic content validation — hard rules, no LLM."""
    from services.content_validator import validate_content
    validation = validate_content(title, content, topic)
    return ReviewResult(
        reviewer_name="programmatic_validator",
        passed=validation.passed,
        score=max(0, 100 - validation.score_penalty),
        feedback="; ".join(i.description[:60] for i in validation.issues[:3]) or "No issues",
        metadata={"critical": validation.critical_count, "warnings": validation.warning_count},
    )


async def seo_checker_reviewer(
    content: str = "", title: str = "", topic: str = "",
    primary_keyword: str = "", **kwargs
) -> ReviewResult:
    """Check SEO basics — keyword in title, first paragraph, headings."""
    import re
    issues = []
    score = 100

    if not primary_keyword:
        return ReviewResult(
            reviewer_name="seo_checker", passed=True, score=80,
            feedback="No primary keyword specified — skipping SEO check",
        )

    kw = primary_keyword.lower()
    clean_title = re.sub(r"<[^>]+>", "", title).lower()
    clean_content = re.sub(r"<[^>]+>", "", content).lower()

    if kw not in clean_title:
        issues.append("Keyword missing from title")
        score -= 15

    # Check first 500 chars
    if kw not in clean_content[:500]:
        issues.append("Keyword missing from first paragraph")
        score -= 10

    # Check headings
    headings = re.findall(r"(?:^|\n)#{1,3}\s+(.+)", content)
    heading_text = " ".join(h.lower() for h in headings)
    if kw not in heading_text:
        issues.append("Keyword missing from headings")
        score -= 10

    return ReviewResult(
        reviewer_name="seo_checker",
        passed=score >= 70,
        score=max(0, score),
        feedback="; ".join(issues) if issues else "SEO looks good",
    )


# ============================================================================
# DEFAULT REGISTRY SETUP
# ============================================================================

def create_default_registry(settings_service=None) -> QARegistry:
    """Create a QA registry with built-in reviewers and workflows."""
    registry = QARegistry(settings_service=settings_service)

    # Register built-in reviewers
    registry.register_reviewer(
        "programmatic_validator",
        programmatic_validator_reviewer,
        weight=2.0,
        stop_on_fail=True,  # Critical issues stop the workflow
        description="Hard rules for fabricated content — no LLM judgment",
    )
    registry.register_reviewer(
        "seo_checker",
        seo_checker_reviewer,
        weight=0.5,
        description="Basic SEO checks — keyword placement in title, headings, first paragraph",
    )

    # Register default workflows
    registry.register_workflow(
        "blog_content",
        reviewer_names=["programmatic_validator", "seo_checker"],
        min_score=70.0,
        require_unanimous=False,
        description="QA for blog posts — factual integrity + SEO basics",
    )
    registry.register_workflow(
        "strict_blog",
        reviewer_names=["programmatic_validator", "seo_checker"],
        min_score=80.0,
        require_unanimous=True,
        description="Strict QA — all reviewers must pass, higher score threshold",
    )

    return registry
