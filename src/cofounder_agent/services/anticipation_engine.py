"""
Anticipation Engine — observes patterns and acts before being asked.

The system observes:
  - Content performance trends
  - Pipeline health patterns
  - Cost trends
  - Quality score drift
  - Content gaps (topics not covered)

Then decides and acts:
  - Traffic spike on topic → generate more related content
  - Quality dropping → adjust prompts or switch models
  - Cost approaching limit → switch to cheaper models
  - Content gap detected → auto-create tasks to fill it
  - Stale content detected → suggest updates

This is the "AI that makes AI better" loop.

Usage:
    from services.anticipation_engine import AnticipationEngine
    engine = AnticipationEngine(pool, settings_service)
    actions = await engine.observe_and_decide()
    await engine.execute_actions(actions)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Observation:
    """Something the engine noticed."""
    category: str  # content, quality, cost, health, gap
    signal: str  # what was observed
    value: Any  # the data
    severity: str = "info"  # info, warning, action_needed


@dataclass
class Action:
    """Something the engine decided to do."""
    action_type: str  # create_content, adjust_model, send_alert, update_setting
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    priority: int = 5  # 1=urgent, 10=low
    auto_execute: bool = False  # True = execute without approval


class AnticipationEngine:
    """Observes system patterns and generates proactive actions."""

    def __init__(self, pool=None, settings_service=None):
        self.pool = pool
        self.settings = settings_service

    async def observe(self) -> List[Observation]:
        """Collect observations from all system signals."""
        observations = []

        observations.extend(await self._observe_content_gaps())
        observations.extend(await self._observe_quality_trends())
        observations.extend(await self._observe_cost_trends())
        observations.extend(await self._observe_stale_content())

        logger.info("[ANTICIPATION] Collected %d observations", len(observations))
        return observations

    async def decide(self, observations: List[Observation]) -> List[Action]:
        """Convert observations into actions."""
        actions = []

        for obs in observations:
            if obs.severity == "action_needed":
                action = self._observation_to_action(obs)
                if action:
                    actions.append(action)

        # Sort by priority (lower = more urgent)
        actions.sort(key=lambda a: a.priority)
        logger.info("[ANTICIPATION] Generated %d actions", len(actions))
        return actions

    async def observe_and_decide(self) -> List[Action]:
        """Full loop: observe → decide → return actions for approval."""
        observations = await self.observe()
        return await self.decide(observations)

    async def execute_actions(self, actions: List[Action]) -> List[dict]:
        """Execute approved actions."""
        results = []
        for action in actions:
            if not action.auto_execute:
                results.append({"action": action.description, "status": "needs_approval"})
                continue
            try:
                result = await self._execute_action(action)
                results.append({"action": action.description, "status": "executed", "result": result})
            except Exception as e:
                results.append({"action": action.description, "status": "failed", "error": str(e)[:100]})
        return results

    # ========================================================================
    # OBSERVERS
    # ========================================================================

    async def _observe_content_gaps(self) -> List[Observation]:
        """Detect topics that should be covered but aren't."""
        observations = []
        if not self.pool:
            return observations

        try:
            # Check how many posts per category
            rows = await self.pool.fetch("""
                SELECT c.name, COUNT(p.id) as count
                FROM categories c
                LEFT JOIN posts p ON p.category_id = c.id AND p.status = 'published'
                GROUP BY c.name
                HAVING COUNT(p.id) < 5
            """)
            for row in rows:
                observations.append(Observation(
                    category="gap",
                    signal=f"Category '{row['name']}' has only {row['count']} posts",
                    value={"category": row["name"], "count": row["count"]},
                    severity="action_needed",
                ))

            # Check recency — any category without a post in 2 weeks?
            stale = await self.pool.fetch("""
                SELECT c.name, MAX(p.published_at) as last_post
                FROM categories c
                LEFT JOIN posts p ON p.category_id = c.id AND p.status = 'published'
                GROUP BY c.name
                HAVING MAX(p.published_at) < NOW() - INTERVAL '14 days'
                   OR MAX(p.published_at) IS NULL
            """)
            for row in stale:
                observations.append(Observation(
                    category="gap",
                    signal=f"Category '{row['name']}' has no recent posts",
                    value={"category": row["name"]},
                    severity="action_needed",
                ))
        except Exception as e:
            logger.debug("[ANTICIPATION] Content gap check failed: %s", e)

        return observations

    async def _observe_quality_trends(self) -> List[Observation]:
        """Detect quality score drift."""
        observations = []
        if not self.pool:
            return observations

        try:
            # Compare recent quality to historical
            row = await self.pool.fetchrow("""
                SELECT
                    AVG(CASE WHEN created_at > NOW() - INTERVAL '3 days'
                        THEN quality_score END) as recent_avg,
                    AVG(CASE WHEN created_at <= NOW() - INTERVAL '3 days'
                        THEN quality_score END) as older_avg
                FROM content_tasks
                WHERE quality_score IS NOT NULL
            """)
            if row and row["recent_avg"] and row["older_avg"]:
                recent = float(row["recent_avg"])
                older = float(row["older_avg"])
                if recent < older - 5:
                    observations.append(Observation(
                        category="quality",
                        signal=f"Quality dropping: {recent:.0f} vs {older:.0f} historical",
                        value={"recent": recent, "historical": older, "delta": recent - older},
                        severity="action_needed",
                    ))
        except Exception as e:
            logger.debug("[ANTICIPATION] Quality trend check failed: %s", e)

        return observations

    async def _observe_cost_trends(self) -> List[Observation]:
        """Detect cost approaching limits."""
        observations = []
        if not self.pool or not self.settings:
            return observations

        try:
            daily_limit = float(await self.settings.get("daily_spend_limit") or "2.0")
            row = await self.pool.fetchrow(
                "SELECT COALESCE(SUM(cost_usd), 0) as today FROM cost_logs "
                "WHERE created_at >= date_trunc('day', NOW())"
            )
            today = float(row["today"]) if row else 0
            if today > daily_limit * 0.7:
                observations.append(Observation(
                    category="cost",
                    signal=f"Daily spend at {today/daily_limit*100:.0f}% of limit",
                    value={"spent": today, "limit": daily_limit},
                    severity="action_needed" if today > daily_limit * 0.9 else "warning",
                ))
        except Exception as e:
            logger.debug("[ANTICIPATION] Cost trend check failed: %s", e)

        return observations

    async def _observe_stale_content(self) -> List[Observation]:
        """Detect posts that haven't been updated in a long time."""
        observations = []
        if not self.pool:
            return observations

        try:
            row = await self.pool.fetchrow("""
                SELECT COUNT(*) as stale FROM posts
                WHERE status = 'published'
                AND updated_at < NOW() - INTERVAL '90 days'
            """)
            if row and row["stale"] > 10:
                observations.append(Observation(
                    category="content",
                    signal=f"{row['stale']} posts haven't been updated in 90+ days",
                    value={"stale_count": row["stale"]},
                    severity="info",
                ))
        except Exception as e:
            logger.debug("[ANTICIPATION] Stale content check failed: %s", e)

        return observations

    # ========================================================================
    # DECISION LOGIC
    # ========================================================================

    def _observation_to_action(self, obs: Observation) -> Optional[Action]:
        """Convert an observation into a concrete action."""
        if obs.category == "gap" and "only" in obs.signal:
            cat = obs.value.get("category", "technology")
            return Action(
                action_type="create_content",
                description=f"Generate 3 posts for underrepresented category: {cat}",
                parameters={"category": cat, "count": 3},
                priority=3,
                auto_execute=False,  # Needs approval
            )

        if obs.category == "gap" and "no recent" in obs.signal:
            cat = obs.value.get("category", "technology")
            return Action(
                action_type="create_content",
                description=f"Generate fresh content for stale category: {cat}",
                parameters={"category": cat, "count": 2},
                priority=4,
                auto_execute=False,
            )

        if obs.category == "quality" and "dropping" in obs.signal:
            return Action(
                action_type="adjust_model",
                description="Quality declining — consider switching to a better model or adjusting prompts",
                parameters={"current_delta": obs.value.get("delta", 0)},
                priority=2,
                auto_execute=False,
            )

        if obs.category == "cost" and obs.severity == "action_needed":
            return Action(
                action_type="update_setting",
                description="Cost approaching daily limit — switch to free tier models",
                parameters={"setting": "default_model_tier", "value": "free"},
                priority=1,
                auto_execute=True,  # Auto-protect budget
            )

        return None

    async def _execute_action(self, action: Action) -> str:
        """Execute a single action."""
        if action.action_type == "update_setting" and self.settings:
            key = action.parameters.get("setting", "")
            value = action.parameters.get("value", "")
            if key and value:
                await self.settings.set(key, value)
                return f"Updated {key} = {value}"

        return "Action type not yet implemented"
