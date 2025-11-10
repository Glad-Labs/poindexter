"""
Memory System Extensions for Intelligent Orchestrator

Extends the existing memory system with:
- Execution history tracking
- Learning pattern accumulation  
- Decision tree building
- Training dataset generation
- Semantic workflow pattern matching
"""

import logging
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Set
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class PatternType(str, Enum):
    """Types of patterns that can be learned"""
    WORKFLOW = "workflow"
    DECISION = "decision"
    USER_PREFERENCE = "user_preference"
    BUSINESS_OBJECTIVE = "business_objective"
    TOOL_COMBINATION = "tool_combination"
    QUALITY_ISSUE = "quality_issue"


@dataclass
class ExecutionPattern:
    """A pattern identified from multiple similar executions"""
    pattern_id: str
    pattern_type: PatternType
    name: str
    description: str
    examples: List[Dict[str, Any]]  # Successful execution examples
    frequency: int  # How many times seen
    confidence: float  # 0-1
    success_rate: float  # % of attempts that succeeded
    average_quality_score: float
    learned_at: datetime


@dataclass
class DecisionNode:
    """Node in a decision tree learned from executions"""
    node_id: str
    condition: str  # The question/condition (e.g., "content_length > 1000?")
    true_path_action: str  # Action if true
    false_path_action: str  # Action if false
    confidence: float  # How confident we are in this split
    samples: int  # How many samples created this node
    outcomes: Dict[str, float]  # Distribution of outcomes


class EnhancedMemorySystem:
    """
    Extended memory system for learning from orchestrator execution.
    
    Builds upon the existing AIMemorySystem to track:
    1. Workflow patterns (sequences of tools that work well together)
    2. Quality patterns (what causes failures)
    3. Decision trees (learned decision logic)
    4. User preferences (evolving over time)
    5. Business metrics correlations (what drives results)
    """

    def __init__(self, base_memory_system):
        """
        Initialize enhanced memory system wrapping existing system.
        
        Args:
            base_memory_system: Instance of AIMemorySystem
        """
        self.memory = base_memory_system
        self.execution_patterns: Dict[str, ExecutionPattern] = {}
        self.decision_trees: Dict[str, DecisionNode] = {}
        self.workflow_templates: Dict[str, Dict[str, Any]] = {}
        self.quality_correlations: List[Dict[str, Any]] = []
        self.business_metric_history: List[Dict[str, Any]] = []

    async def record_execution(
        self,
        request: str,
        workflow_steps: List[str],
        result_quality: float,
        business_metrics: Dict[str, Any],
        outcome: str  # "success", "partial_success", "failure"
    ) -> Dict[str, Any]:
        """
        Record a single execution for pattern learning.
        
        This will:
        1. Store raw execution data
        2. Update pattern frequencies
        3. Adjust confidence scores
        4. Update decision trees
        """
        execution_record: Dict[str, Any] = {
            "request": request,
            "workflow_steps": workflow_steps,
            "quality": result_quality,
            "business_metrics": business_metrics,
            "outcome": outcome,
            "timestamp": datetime.now().isoformat()
        }

        # Store in base memory system
        await self.memory.store_memory(
            content=json.dumps(execution_record),
            memory_type="execution_history",
            importance=3 if result_quality > 0.8 else 2,
            confidence=result_quality,
            metadata={"outcome": outcome}
        )

        # Update patterns
        await self._update_workflow_patterns(workflow_steps, result_quality, outcome)

        # Track business metrics
        self.business_metric_history.append({
            "timestamp": datetime.now().isoformat(),
            "metrics": business_metrics,
            "quality_score": result_quality
        })

        return execution_record

    async def _update_workflow_patterns(
        self,
        workflow_steps: List[str],
        quality: float,
        outcome: str
    ):
        """Update or create workflow patterns from execution"""
        # Create a pattern key from the workflow steps
        pattern_key = "→".join(workflow_steps)

        if pattern_key not in self.execution_patterns:
            # New pattern
            self.execution_patterns[pattern_key] = ExecutionPattern(
                pattern_id=pattern_key,
                pattern_type=PatternType.WORKFLOW,
                name=f"Workflow: {pattern_key[:50]}...",
                description=f"Workflow using steps: {', '.join(workflow_steps)}",
                examples=[],
                frequency=1,
                confidence=0.5,
                success_rate=1.0 if outcome == "success" else 0.0,
                average_quality_score=quality,
                learned_at=datetime.now()
            )
        else:
            # Update existing pattern
            pattern = self.execution_patterns[pattern_key]
            pattern.frequency += 1
            
            # Update success rate (exponential moving average)
            success = 1.0 if outcome == "success" else 0.0
            alpha = 0.3  # Smoothing factor
            pattern.success_rate = alpha * success + (1 - alpha) * pattern.success_rate
            
            # Update quality score (exponential moving average)
            pattern.average_quality_score = (
                alpha * quality + (1 - alpha) * pattern.average_quality_score
            )
            
            # Increase confidence based on frequency
            pattern.confidence = min(1.0, 0.5 + (pattern.frequency / 20.0))

        # Store example
        self.execution_patterns[pattern_key].examples.append({
            "quality": quality,
            "outcome": outcome,
            "timestamp": datetime.now().isoformat()
        })

    def get_workflow_patterns(
        self,
        min_frequency: int = 2,
        min_success_rate: float = 0.7
    ) -> List[ExecutionPattern]:
        """
        Get learned workflow patterns.
        
        Returns patterns that have been seen multiple times and have good success rate.
        """
        return [
            pattern for pattern in self.execution_patterns.values()
            if pattern.frequency >= min_frequency 
            and pattern.success_rate >= min_success_rate
        ]

    async def correlate_with_business_metrics(self) -> Dict[str, Any]:
        """
        Analyze correlations between workflows and business metrics.
        
        Identifies which workflows tend to produce better business outcomes.
        """
        correlations = {}

        for pattern_key, pattern in self.execution_patterns.items():
            if len(pattern.examples) < 3:
                continue  # Not enough data

            # Get average quality for this pattern
            avg_quality = pattern.average_quality_score

            # Find corresponding business metrics
            pattern_metrics = []
            for metric_record in self.business_metric_history[-100:]:
                # Rough matching based on timestamp
                if metric_record["quality_score"] >= avg_quality - 0.1:
                    pattern_metrics.append(metric_record["metrics"])

            if pattern_metrics:
                # Calculate average metrics when this pattern was used
                correlations[pattern_key] = {
                    "workflow_steps": pattern_key.split("→"),
                    "quality_score": pattern.average_quality_score,
                    "frequency": pattern.frequency,
                    "success_rate": pattern.success_rate,
                    "avg_metrics": self._average_metrics(pattern_metrics)
                }

        return correlations

    def _average_metrics(self, metrics_list: List[Dict[str, Any]]) -> Dict[str, float]:
        """Calculate average of metrics across records"""
        if not metrics_list:
            return {}

        averaged = {}
        for key in metrics_list[0].keys():
            values = [m.get(key, 0) for m in metrics_list if isinstance(m.get(key), (int, float))]
            if values:
                averaged[key] = sum(values) / len(values)

        return averaged

    async def get_recommended_workflow(self, request: str) -> Optional[List[str]]:
        """
        Get recommended workflow for a request based on learned patterns.
        
        Uses semantic search in memory + pattern matching to find similar
        successful workflows.
        """
        # Search memory for similar requests
        similar_memories = await self.memory.semantic_search(
            request,
            limit=5,
            memory_type="execution_history"
        )

        if not similar_memories:
            return None

        # Extract workflows from similar executions
        workflows = []
        for memory in similar_memories:
            try:
                execution = json.loads(memory.content)
                if execution.get("outcome") == "success":
                    workflows.append(execution.get("workflow_steps", []))
            except json.JSONDecodeError:
                continue

        if not workflows:
            return None

        # Return most common workflow
        workflow_scores: Dict[str, int] = {}
        for workflow in workflows:
            key = "→".join(workflow)
            workflow_scores[key] = workflow_scores.get(key, 0) + 1

        best_workflow_key = max(workflow_scores.keys(), key=lambda k: workflow_scores.get(k, 0))
        return best_workflow_key.split("→")

    def export_learned_patterns(self, format: str = "json") -> str:
        """Export learned patterns for analysis or training"""
        if format == "json":
            patterns_data = [
                {
                    "pattern_id": pattern.pattern_id,
                    "type": pattern.pattern_type.value,
                    "name": pattern.name,
                    "frequency": pattern.frequency,
                    "confidence": pattern.confidence,
                    "success_rate": pattern.success_rate,
                    "average_quality": pattern.average_quality_score,
                    "examples_count": len(pattern.examples)
                }
                for pattern in self.execution_patterns.values()
            ]
            return json.dumps(patterns_data, indent=2)
        elif format == "markdown":
            lines = ["# Learned Execution Patterns\n"]
            for pattern in sorted(
                self.execution_patterns.values(),
                key=lambda p: p.frequency,
                reverse=True
            )[:20]:  # Top 20
                lines.append(f"## {pattern.name}")
                lines.append(f"- **Frequency:** {pattern.frequency} times")
                lines.append(f"- **Success Rate:** {pattern.success_rate:.1%}")
                lines.append(f"- **Avg Quality:** {pattern.average_quality_score:.2f}")
                lines.append(f"- **Confidence:** {pattern.confidence:.1%}")
                lines.append("")
            return "\n".join(lines)
        else:
            raise ValueError(f"Unsupported format: {format}")

    def get_quality_correlations(self) -> Dict[str, Any]:
        """Get factors correlated with high/low quality results"""
        high_quality = []
        low_quality = []

        for record in self.business_metric_history[-100:]:
            if record["quality_score"] >= 0.8:
                high_quality.append(record["metrics"])
            elif record["quality_score"] < 0.6:
                low_quality.append(record["metrics"])

        return {
            "high_quality_factors": self._average_metrics(high_quality),
            "low_quality_factors": self._average_metrics(low_quality),
            "quality_samples": len(high_quality) + len(low_quality)
        }
