"""
Quality Score Persistence Service

Stores quality evaluation results in PostgreSQL and provides
querying/analytics capabilities for quality metrics.
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, date
from services.quality_service import QualityScore

logger = logging.getLogger(__name__)


class QualityScorePersistence:
    """
    Persistence layer for quality evaluation scores.

    Handles:
    - Storing evaluation results in database
    - Tracking quality improvements
    - Generating daily metrics/trends
    - Querying evaluation history
    """

    def __init__(self, database_service):
        """
        Initialize persistence service

        Args:
            database_service: DatabaseService instance for PostgreSQL access
        """
        self.db = database_service

    async def store_evaluation(
        self,
        content_id: str,
        quality_score: QualityScore,
        task_id: Optional[str] = None,
        content_length: Optional[int] = None,
        context_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Store a quality evaluation result in the database

        Args:
            content_id: Unique identifier for the content being evaluated
            quality_score: QualityScore object from evaluator
            task_id: Optional link to content task
            content_length: Optional word count
            context_data: Optional dict with keywords, topic, etc.

        Returns:
            Dict with stored evaluation details including database ID
        """

        try:
            query = """
            INSERT INTO quality_evaluations (
                content_id, task_id,
                overall_score, clarity, accuracy, completeness, relevance, 
                seo_quality, readability, engagement,
                passing, feedback, suggestions,
                evaluated_by, evaluation_method, evaluation_timestamp,
                content_length, context_data
            ) VALUES (
                $1, $2,
                $3, $4, $5, $6, $7, $8, $9, $10,
                $11, $12, $13,
                $14, $15, $16,
                $17, $18
            )
            RETURNING id, content_id, overall_score, passing, evaluation_timestamp
            """

            import json

            result = await self.db.execute_query(
                query,
                content_id,
                task_id,
                quality_score.overall_score,
                quality_score.clarity,
                quality_score.accuracy,
                quality_score.completeness,
                quality_score.relevance,
                quality_score.seo_quality,
                quality_score.readability,
                quality_score.engagement,
                quality_score.passing,
                quality_score.feedback,
                json.dumps(quality_score.suggestions),
                quality_score.evaluated_by,
                (
                    "pattern-based"
                    if quality_score.evaluated_by == "QualityEvaluator"
                    else "llm-based"
                ),
                quality_score.evaluation_timestamp,
                content_length,
                json.dumps(context_data or {}),
            )

            logger.info(
                f"✅ Stored evaluation for content {content_id}: score={quality_score.overall_score}, passing={quality_score.passing}"
            )

            return {
                "stored": True,
                "evaluation_id": result[0] if result else None,
                "content_id": content_id,
                "overall_score": quality_score.overall_score,
                "passing": quality_score.passing,
            }

        except Exception as e:
            logger.error(f"❌ Failed to store evaluation: {str(e)}")
            return {"stored": False, "error": str(e)}

    async def store_improvement(
        self,
        content_id: str,
        initial_score: float,
        improved_score: float,
        best_improved_criterion: Optional[str] = None,
        changes_made: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Record a quality improvement after refinement

        Args:
            content_id: Content being improved
            initial_score: Score before refinement
            improved_score: Score after refinement
            best_improved_criterion: Which criterion improved most
            changes_made: Description of changes made

        Returns:
            Dict with improvement tracking result
        """

        try:
            improvement = improved_score - initial_score
            passed_after = improved_score >= 7.0

            query = """
            INSERT INTO quality_improvement_logs (
                content_id, initial_score, improved_score, score_improvement,
                best_improved_criterion, refinement_type, changes_made,
                passed_after_refinement
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id, score_improvement, passed_after_refinement
            """

            result = await self.db.execute_query(
                query,
                content_id,
                initial_score,
                improved_score,
                improvement,
                best_improved_criterion,
                "auto-refinement",
                changes_made,
                passed_after,
            )

            logger.info(
                f"✅ Recorded improvement for {content_id}: {initial_score:.1f} → {improved_score:.1f} (+{improvement:.1f})"
            )

            return {
                "recorded": True,
                "improvement_log_id": result[0] if result else None,
                "score_improvement": improvement,
                "passed_after_refinement": passed_after,
            }

        except Exception as e:
            logger.error(f"❌ Failed to record improvement: {str(e)}")
            return {"recorded": False, "error": str(e)}

    async def get_evaluation_history(
        self, content_id: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get evaluation history for content

        Args:
            content_id: Content to query
            limit: Maximum number of evaluations to return

        Returns:
            List of evaluations (newest first)
        """

        try:
            query = """
            SELECT 
                id, content_id, overall_score, clarity, accuracy, completeness,
                relevance, seo_quality, readability, engagement,
                passing, feedback, evaluation_timestamp
            FROM quality_evaluations
            WHERE content_id = $1
            ORDER BY evaluation_timestamp DESC
            LIMIT $2
            """

            results = await self.db.fetch_query(query, content_id, limit)

            return [dict(row) for row in results] if results else []

        except Exception as e:
            logger.error(f"❌ Failed to get evaluation history: {str(e)}")
            return []

    async def get_latest_evaluation(self, content_id: str) -> Optional[Dict[str, Any]]:
        """Get the most recent evaluation for content"""

        history = await self.get_evaluation_history(content_id, limit=1)
        return history[0] if history else None

    async def get_quality_metrics_for_date(
        self, target_date: Optional[date] = None
    ) -> Dict[str, Any]:
        """
        Get quality metrics for a specific date

        Args:
            target_date: Date to query (defaults to today)

        Returns:
            Dict with aggregated metrics for the date
        """

        if target_date is None:
            target_date = date.today()

        try:
            query = """
            SELECT 
                date, total_evaluations, passing_count, failing_count,
                pass_rate, average_score,
                avg_clarity, avg_accuracy, avg_completeness, avg_relevance,
                avg_seo_quality, avg_readability, avg_engagement,
                total_refinements, avg_refinements_per_content,
                total_improvement_points
            FROM quality_metrics_daily
            WHERE date = $1
            """

            result = await self.db.fetch_query(query, target_date)

            if result:
                return dict(result[0])
            else:
                # No metrics for this date yet
                return {
                    "date": target_date,
                    "total_evaluations": 0,
                    "passing_count": 0,
                    "failing_count": 0,
                    "pass_rate": 0.0,
                    "average_score": 0.0,
                }

        except Exception as e:
            logger.error(f"❌ Failed to get metrics for date {target_date}: {str(e)}")
            return {}

    async def get_quality_trend(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        Get quality metrics trend over N days

        Args:
            days: Number of days to look back

        Returns:
            List of daily metrics (oldest to newest)
        """

        try:
            query = """
            SELECT 
                date, total_evaluations, pass_rate, average_score,
                avg_clarity, avg_accuracy, avg_completeness, avg_relevance,
                avg_seo_quality, avg_readability, avg_engagement
            FROM quality_metrics_daily
            WHERE date >= CURRENT_DATE - INTERVAL '%s days'
            ORDER BY date ASC
            """

            results = await self.db.fetch_query(query, days)

            return [dict(row) for row in results] if results else []

        except Exception as e:
            logger.error(f"❌ Failed to get quality trend: {str(e)}")
            return []

    async def get_content_quality_summary(self, content_id: str) -> Dict[str, Any]:
        """
        Get comprehensive quality summary for content

        Args:
            content_id: Content to summarize

        Returns:
            Dict with current score, history, improvements, etc.
        """

        try:
            # Get latest evaluation
            latest = await self.get_latest_evaluation(content_id)

            # Get all evaluations
            history = await self.get_evaluation_history(content_id, limit=None)

            # Get improvement logs
            query = """
            SELECT 
                initial_score, improved_score, score_improvement,
                best_improved_criterion, refinement_timestamp
            FROM quality_improvement_logs
            WHERE content_id = $1
            ORDER BY refinement_timestamp DESC
            LIMIT 5
            """
            improvements = await self.db.fetch_query(query, content_id)

            return {
                "content_id": content_id,
                "latest_evaluation": latest,
                "total_evaluations": len(history),
                "evaluation_count": len(history),
                "improvement_count": len(improvements) if improvements else 0,
                "improvements": [dict(imp) for imp in (improvements or [])],
                "passing": latest.get("passing") if latest else False,
                "current_score": latest.get("overall_score") if latest else 0.0,
                "score_trend": [e.get("overall_score") for e in history[-5:]] if history else [],
            }

        except Exception as e:
            logger.error(f"❌ Failed to get quality summary: {str(e)}")
            return {"error": str(e)}

    async def calculate_and_store_daily_metrics(self) -> bool:
        """
        Calculate and store daily aggregate metrics

        Should be called once daily (e.g., via scheduled task)

        Returns:
            True if successful, False otherwise
        """

        try:
            today = date.today()

            # Calculate aggregates for today
            agg_query = """
            SELECT 
                COUNT(*) as total_evaluations,
                SUM(CASE WHEN passing THEN 1 ELSE 0 END) as passing_count,
                SUM(CASE WHEN NOT passing THEN 1 ELSE 0 END) as failing_count,
                AVG(overall_score) as average_score,
                AVG(clarity) as avg_clarity,
                AVG(accuracy) as avg_accuracy,
                AVG(completeness) as avg_completeness,
                AVG(relevance) as avg_relevance,
                AVG(seo_quality) as avg_seo_quality,
                AVG(readability) as avg_readability,
                AVG(engagement) as avg_engagement
            FROM quality_evaluations
            WHERE DATE(evaluation_timestamp) = $1
            """

            result = await self.db.fetch_query(agg_query, today)

            if result:
                row = result[0]
                total = row["total_evaluations"] or 0
                passing = row["passing_count"] or 0
                pass_rate = (passing / total * 100) if total > 0 else 0

                insert_query = """
                INSERT INTO quality_metrics_daily (
                    date, total_evaluations, passing_count, failing_count,
                    pass_rate, average_score,
                    avg_clarity, avg_accuracy, avg_completeness, avg_relevance,
                    avg_seo_quality, avg_readability, avg_engagement
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                ON CONFLICT (date) DO UPDATE SET
                    total_evaluations = EXCLUDED.total_evaluations,
                    passing_count = EXCLUDED.passing_count,
                    failing_count = EXCLUDED.failing_count,
                    pass_rate = EXCLUDED.pass_rate,
                    average_score = EXCLUDED.average_score,
                    avg_clarity = EXCLUDED.avg_clarity,
                    avg_accuracy = EXCLUDED.avg_accuracy,
                    avg_completeness = EXCLUDED.avg_completeness,
                    avg_relevance = EXCLUDED.avg_relevance,
                    avg_seo_quality = EXCLUDED.avg_seo_quality,
                    avg_readability = EXCLUDED.avg_readability,
                    avg_engagement = EXCLUDED.avg_engagement
                """

                await self.db.execute_query(
                    insert_query,
                    today,
                    total,
                    passing,
                    total - passing,
                    pass_rate,
                    row["average_score"],
                    row["avg_clarity"],
                    row["avg_accuracy"],
                    row["avg_completeness"],
                    row["avg_relevance"],
                    row["avg_seo_quality"],
                    row["avg_readability"],
                    row["avg_engagement"],
                )

                logger.info(
                    f"✅ Calculated daily metrics for {today}: {total} evaluations, {pass_rate:.1f}% pass rate"
                )
                return True

            return True

        except Exception as e:
            logger.error(f"❌ Failed to calculate daily metrics: {str(e)}")
            return False


# Dependency injection function (replaces singleton pattern)
def get_quality_score_persistence(database_service) -> QualityScorePersistence:
    """
    Factory function for QualityScorePersistence dependency injection.

    Replaces singleton pattern with FastAPI Depends() for:
    - Testability: Can inject mocks/test instances
    - Thread safety: No global state
    - Flexibility: Can create new instances per request if needed

    Usage in route:
        @router.get("/endpoint")
        async def handler(
            persistence = Depends(get_quality_score_persistence(database_service))
        ):
            return await persistence.store_evaluation(...)

    Args:
        database_service: DatabaseService instance for persistence

    Returns:
        QualityScorePersistence instance
    """
    return QualityScorePersistence(database_service)
