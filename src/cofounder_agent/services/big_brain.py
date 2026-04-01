"""
Big Brain — self-maintaining knowledge graph with reasoning queue.

Not a passive memory system. An active intelligence that:
- Processes observations into structured knowledge
- Connects dots between facts
- Self-maintains (expires stale knowledge, resolves contradictions)
- Reasons proactively (generates insights and action proposals)
- Has its own queue of things to think about

Architecture:
  Events → brain_queue → Reasoning Engine → brain_knowledge + brain_decisions

  brain_knowledge: entity/attribute/value triples (knowledge graph)
  brain_queue: things to think about (observations, questions, contradictions)
  brain_decisions: what was decided and why (audit trail)

Usage:
    brain = BigBrain(pool)

    # Feed it observations
    await brain.observe("Matt mentioned he's anxious about launching")
    await brain.learn("matt", "career", "geotechnical engineer", source="conversation")
    await brain.learn("gladlabs", "posts_count", "64", source="system")

    # It thinks on its own
    insights = await brain.think()  # processes queue

    # Query it
    facts = await brain.recall("matt")  # everything about Matt
    answer = await brain.ask("What should we prioritize?")
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BigBrain:
    """Self-maintaining knowledge graph with reasoning capabilities."""

    def __init__(self, pool=None):
        self.pool = pool

    # ========================================================================
    # LEARN — add knowledge
    # ========================================================================

    async def learn(
        self,
        entity: str,
        attribute: str,
        value: str,
        confidence: float = 1.0,
        source: str = "system",
        session: str = "",
        tags: Optional[List[str]] = None,
        ttl_days: Optional[int] = None,
    ) -> bool:
        """Store a fact in the knowledge graph. Upserts on entity+attribute."""
        if not self.pool:
            return False
        try:
            expires = None
            if ttl_days:
                from datetime import timedelta
                expires = datetime.now(timezone.utc) + timedelta(days=ttl_days)

            await self.pool.execute("""
                INSERT INTO brain_knowledge (entity, attribute, value, confidence, source, source_session, tags, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (entity, attribute) DO UPDATE SET
                    value = EXCLUDED.value,
                    confidence = EXCLUDED.confidence,
                    source = EXCLUDED.source,
                    source_session = EXCLUDED.source_session,
                    updated_at = NOW()
            """, entity, attribute, value, confidence, source, session, tags or [], expires)
            logger.info("[BRAIN] Learned: %s.%s = %s", entity, attribute, value[:50])
            return True
        except Exception as e:
            logger.error("[BRAIN] Failed to learn: %s", e)
            return False

    # ========================================================================
    # OBSERVE — queue something for the brain to think about
    # ========================================================================

    async def observe(
        self,
        observation: str,
        item_type: str = "observation",
        context: Optional[Dict[str, Any]] = None,
        priority: int = 5,
    ) -> int:
        """Add something to the thinking queue."""
        if not self.pool:
            return 0
        try:
            row = await self.pool.fetchrow("""
                INSERT INTO brain_queue (item_type, content, context, priority)
                VALUES ($1, $2, $3::jsonb, $4)
                RETURNING id
            """, item_type, observation, json.dumps(context or {}), priority)
            logger.info("[BRAIN] Queued: [%s] %s", item_type, observation[:60])
            return row["id"] if row else 0
        except Exception as e:
            logger.error("[BRAIN] Failed to queue: %s", e)
            return 0

    # ========================================================================
    # RECALL — query knowledge
    # ========================================================================

    async def recall(self, entity: str) -> List[Dict[str, Any]]:
        """Get everything known about an entity."""
        if not self.pool:
            return []
        try:
            rows = await self.pool.fetch("""
                SELECT attribute, value, confidence, source, updated_at
                FROM brain_knowledge
                WHERE entity = $1
                AND (expires_at IS NULL OR expires_at > NOW())
                ORDER BY confidence DESC, updated_at DESC
            """, entity)
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("[BRAIN] Failed to recall %s: %s", entity, e)
            return []

    async def recall_fact(self, entity: str, attribute: str) -> Optional[str]:
        """Get a specific fact."""
        if not self.pool:
            return None
        try:
            row = await self.pool.fetchrow("""
                SELECT value FROM brain_knowledge
                WHERE entity = $1 AND attribute = $2
                AND (expires_at IS NULL OR expires_at > NOW())
            """, entity, attribute)
            return row["value"] if row else None
        except Exception as e:
            logger.warning("[BRAIN] Failed to recall fact %s.%s: %s", entity, attribute, e)
            return None

    async def semantic_search(
        self, query: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Search knowledge using pgvector semantic similarity.

        Embeds the query via Ollama and searches the embeddings table for
        brain_knowledge entries. Falls back to the existing ILIKE-based
        search() if Ollama or pgvector is unavailable.

        Args:
            query: Natural language search query.
            limit: Maximum number of results.

        Returns:
            List of dicts with entity, attribute, value, confidence,
            similarity (float, 0-1).
        """
        if not self.pool:
            return []

        try:
            from .ollama_client import OllamaClient
            from .embeddings_db import EmbeddingsDatabase

            ollama = OllamaClient()
            query_embedding = await ollama.embed(query)
            await ollama.close()

            embeddings_db = EmbeddingsDatabase(self.pool)
            similar = await embeddings_db.search_similar(
                embedding=query_embedding,
                limit=limit,
                source_type="brain_knowledge",
                min_similarity=0.3,
            )

            if not similar:
                logger.info("[BRAIN] Semantic search found no results, falling back to ILIKE")
                return await self.search(query, limit)

            # Enrich results with full knowledge triple from brain_knowledge
            results = []
            for match in similar:
                source_id = match.get("source_id", "")
                # source_id format is "entity::attribute"
                parts = source_id.split("::", 1)
                if len(parts) == 2:
                    entity, attribute = parts
                    try:
                        row = await self.pool.fetchrow(
                            """
                            SELECT entity, attribute, value, confidence, updated_at
                            FROM brain_knowledge
                            WHERE entity = $1 AND attribute = $2
                            AND (expires_at IS NULL OR expires_at > NOW())
                            """,
                            entity,
                            attribute,
                        )
                        if row:
                            result = dict(row)
                            result["similarity"] = match.get("similarity", 0)
                            results.append(result)
                    except Exception:
                        logger.debug(
                            "[BRAIN] Failed to fetch knowledge triple for %s::%s",
                            entity, attribute, exc_info=True,
                        )

            logger.info("[BRAIN] Semantic search returned %d results", len(results))
            return results if results else await self.search(query, limit)

        except Exception as e:
            logger.warning("[BRAIN] Semantic search unavailable (%s), falling back to ILIKE", e)
            return await self.search(query, limit)

    async def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search knowledge by value content."""
        if not self.pool:
            return []
        try:
            rows = await self.pool.fetch("""
                SELECT entity, attribute, value, confidence, updated_at
                FROM brain_knowledge
                WHERE value ILIKE $1 OR entity ILIKE $1 OR attribute ILIKE $1
                AND (expires_at IS NULL OR expires_at > NOW())
                ORDER BY confidence DESC
                LIMIT $2
            """, f"%{query}%", limit)
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("[BRAIN] Search failed: %s", e)
            return []

    # ========================================================================
    # THINK — process the queue
    # ========================================================================

    async def think(self, max_items: int = 10) -> List[Dict[str, Any]]:
        """Process pending items in the reasoning queue.

        For each item:
        1. Read the observation/question
        2. Check existing knowledge for context
        3. Generate insight or action
        4. Store decision in brain_decisions
        5. Update knowledge graph if new facts discovered
        """
        if not self.pool:
            return []

        try:
            items = await self.pool.fetch("""
                SELECT id, item_type, content, context
                FROM brain_queue
                WHERE status = 'pending'
                ORDER BY priority ASC, created_at ASC
                LIMIT $1
            """, max_items)
        except Exception as e:
            logger.error("[BRAIN] Failed to read queue: %s", e)
            return []

        results = []
        for item in items:
            try:
                result = await self._process_queue_item(item)
                results.append(result)
                await self.pool.execute(
                    "UPDATE brain_queue SET status = 'processed', result = $1, processed_at = NOW() WHERE id = $2",
                    json.dumps(result), item["id"],
                )
            except Exception as e:
                logger.error("[BRAIN] Failed to process item %d: %s", item["id"], e)
                await self.pool.execute(
                    "UPDATE brain_queue SET status = 'failed', result = $1, processed_at = NOW() WHERE id = $2",
                    str(e)[:500], item["id"],
                )

        logger.info("[BRAIN] Processed %d/%d queue items", len(results), len(items))
        return results

    async def _process_queue_item(self, item: dict) -> dict:
        """Process a single queue item — extract facts and generate insights."""
        content = item["content"]
        item_type = item["item_type"]
        context = json.loads(item["context"]) if isinstance(item["context"], str) else (item["context"] or {})

        if item_type == "observation":
            return await self._process_observation(content, context)
        elif item_type == "metric":
            return await self._process_metric(content, context)
        elif item_type == "question":
            return await self._process_question(content, context)
        elif item_type == "contradiction":
            return await self._process_contradiction(content, context)
        else:
            return {"type": item_type, "result": "unhandled item type"}

    async def _process_observation(self, content: str, context: dict) -> dict:
        """Extract facts from a natural language observation."""
        # Simple keyword-based fact extraction (can be upgraded to LLM later)
        facts_extracted = []

        # Look for numeric facts
        import re
        numbers = re.findall(r'(\w+)\s+(?:is|are|has|have|at|=|:)\s+(\$?[\d,.]+%?)', content, re.IGNORECASE)
        for entity_hint, value in numbers:
            await self.learn("system", entity_hint.lower(), value, source="observation", confidence=0.7)
            facts_extracted.append(f"{entity_hint} = {value}")

        # Record the decision
        await self._record_decision(
            f"Processed observation: {content[:100]}",
            f"Extracted {len(facts_extracted)} facts: {facts_extracted}",
            context,
        )

        return {"type": "observation", "facts_extracted": facts_extracted}

    async def _process_metric(self, content: str, context: dict) -> dict:
        """Process a system metric and check for anomalies."""
        metric_name = context.get("metric", "unknown")
        value = context.get("value", 0)
        threshold = context.get("threshold")

        await self.learn("metrics", metric_name, str(value), source="system", ttl_days=7)

        alert = None
        if threshold and float(value) > float(threshold):
            alert = f"Metric {metric_name} ({value}) exceeds threshold ({threshold})"
            await self._record_decision(
                f"Alert: {alert}",
                f"Metric exceeded threshold",
                context,
            )

        return {"type": "metric", "metric": metric_name, "value": value, "alert": alert}

    async def _process_question(self, content: str, context: dict) -> dict:
        """Answer a question using the knowledge graph."""
        # Search knowledge for relevant facts
        results = await self.search(content)
        answer = "No relevant knowledge found."
        if results:
            facts = [f"{r['entity']}.{r['attribute']} = {r['value']}" for r in results[:5]]
            answer = f"Based on {len(results)} facts: {'; '.join(facts)}"

        return {"type": "question", "question": content, "answer": answer, "facts_used": len(results)}

    async def _process_contradiction(self, content: str, context: dict) -> dict:
        """Resolve a contradiction in knowledge."""
        old_fact = context.get("old", "")
        new_fact = context.get("new", "")

        # Default: trust newer information
        await self._record_decision(
            f"Resolved contradiction: chose new fact over old",
            f"Old: {old_fact}, New: {new_fact}. Newer information preferred.",
            context,
        )

        return {"type": "contradiction", "resolution": "kept_newer", "old": old_fact, "new": new_fact}

    # ========================================================================
    # SELF-MAINTAIN — cleanup and consistency
    # ========================================================================

    async def self_maintain(self) -> dict:
        """Run self-maintenance: expire old facts, find contradictions, update confidence."""
        if not self.pool:
            return {}

        results = {"expired": 0, "low_confidence": 0}

        try:
            # Expire old facts
            result = await self.pool.execute(
                "DELETE FROM brain_knowledge WHERE expires_at IS NOT NULL AND expires_at < NOW()"
            )
            results["expired"] = int(result.split()[-1]) if result else 0

            # Flag low-confidence facts for review
            rows = await self.pool.fetch(
                "SELECT entity, attribute, value, confidence FROM brain_knowledge WHERE confidence < 0.3"
            )
            results["low_confidence"] = len(rows)

            # Clean old processed queue items (keep last 7 days)
            await self.pool.execute(
                "DELETE FROM brain_queue WHERE status != 'pending' AND created_at < NOW() - INTERVAL '7 days'"
            )

            logger.info("[BRAIN] Maintenance: expired=%d, low_confidence=%d",
                        results["expired"], results["low_confidence"])
        except Exception as e:
            logger.error("[BRAIN] Maintenance failed: %s", e)

        return results

    # ========================================================================
    # DECISIONS — audit trail
    # ========================================================================

    async def _record_decision(self, decision: str, reasoning: str, context: dict, confidence: float = 0.5):
        """Log a decision for the audit trail."""
        if not self.pool:
            return
        try:
            await self.pool.execute("""
                INSERT INTO brain_decisions (decision, reasoning, context, confidence)
                VALUES ($1, $2, $3::jsonb, $4)
            """, decision, reasoning, json.dumps(context), confidence)
        except Exception as e:
            logger.warning("[BRAIN] Failed to record decision: %s", e)

    # ========================================================================
    # STATS — what does the brain know?
    # ========================================================================

    async def stats(self) -> dict:
        """Get brain statistics."""
        if not self.pool:
            return {}
        try:
            knowledge = await self.pool.fetchrow("SELECT COUNT(*) as count FROM brain_knowledge WHERE expires_at IS NULL OR expires_at > NOW()")
            queue = await self.pool.fetchrow("SELECT COUNT(*) FILTER (WHERE status = 'pending') as pending, COUNT(*) as total FROM brain_queue")
            decisions = await self.pool.fetchrow("SELECT COUNT(*) as count FROM brain_decisions")
            entities = await self.pool.fetchrow("SELECT COUNT(DISTINCT entity) as count FROM brain_knowledge")

            return {
                "knowledge_facts": knowledge["count"] if knowledge else 0,
                "unique_entities": entities["count"] if entities else 0,
                "queue_pending": queue["pending"] if queue else 0,
                "queue_total": queue["total"] if queue else 0,
                "decisions_logged": decisions["count"] if decisions else 0,
            }
        except Exception as e:
            logger.warning("[BRAIN] Stats query failed: %s", e)
            return {}

    # ========================================================================
    # SEED — populate with initial knowledge
    # ========================================================================

    async def seed_initial_knowledge(self):
        """Seed the brain with foundational facts about Glad Labs."""
        facts = [
            ("gladlabs", "founded", "2025-09-25", "config"),
            ("gladlabs", "founder", "Matt", "config"),
            ("gladlabs", "domain", "gladlabs.io", "config"),
            ("gladlabs", "business_type", "AI-operated content business", "config"),
            ("gladlabs", "revenue_model", "passive — AdSense, products, affiliates, SaaS", "config"),
            ("gladlabs", "tech_stack", "FastAPI + Next.js + PostgreSQL + Ollama + Railway + Vercel", "config"),
            ("gladlabs", "gpu", "NVIDIA RTX 5090 (32GB VRAM)", "config"),
            ("gladlabs", "monthly_cost", "~$30", "config"),
            ("gladlabs", "bank_balance", "$362.75", "mercury"),
            ("matt", "role", "solo founder and operator", "conversation"),
            ("matt", "day_job", "geotechnical engineer", "conversation"),
            ("matt", "goal", "replace 9-5 with AI business revenue", "conversation"),
            ("matt", "preference_ui", "no UI — manage from phone via Telegram/Grafana", "conversation"),
            ("matt", "preference_env_vars", "minimize — store config in DB", "conversation"),
            ("matt", "preference_work_style", "autonomous — don't ask what's next, just work", "conversation"),
            ("system", "posts_count", "64", "database"),
            ("system", "services_count", "15+", "code"),
            ("system", "test_count", "5564", "pytest"),
            ("system", "grafana_panels", "66+", "grafana"),
            ("system", "alert_rules", "5", "grafana"),
        ]
        seeded = 0
        for entity, attribute, value, source in facts:
            if await self.learn(entity, attribute, value, source=source, confidence=1.0):
                seeded += 1
        logger.info("[BRAIN] Seeded %d initial facts", seeded)
        return seeded
