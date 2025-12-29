"""Main LangGraph orchestrator service for FastAPI integration"""

import logging
import uuid
from datetime import datetime
from typing import Optional, AsyncIterator
from .langgraph_graphs.content_pipeline import create_content_pipeline_graph
from .langgraph_graphs.states import ContentPipelineState

logger = logging.getLogger(__name__)


class LangGraphOrchestrator:
    """LangGraph-based orchestration engine for FastAPI"""

    def __init__(self, db_service, llm_service=None, quality_service=None, metadata_service=None):
        """Initialize orchestrator with required services"""
        self.db = db_service
        self.llm = llm_service
        self.quality = quality_service
        self.metadata = metadata_service

        # Create graphs
        self.content_graph = create_content_pipeline_graph(
            llm_service=llm_service,
            quality_service=quality_service,
            metadata_service=metadata_service,
            db_service=db_service,
        )

        logger.info("LangGraphOrchestrator initialized successfully")

    async def execute_content_pipeline(
        self, request_data: dict, user_id: str, stream: bool = False
    ):
        """Execute content generation pipeline"""

        request_id = str(uuid.uuid4())
        logger.info(f"Starting content pipeline: {request_id}")

        # Initialize state
        initial_state: ContentPipelineState = {
            "topic": request_data.get("topic", ""),
            "keywords": request_data.get("keywords", []),
            "audience": request_data.get("audience", "general"),
            "tone": request_data.get("tone", "professional"),
            "word_count": request_data.get("word_count", 800),
            "request_id": request_id,
            "user_id": user_id,
            "research_notes": "",
            "outline": "",
            "draft": "",
            "final_content": "",
            "quality_score": 0.0,
            "quality_feedback": "",
            "passed_quality": False,
            "refinement_count": 0,
            "max_refinements": 3,
            "seo_score": 0.0,
            "metadata": {},
            "tags": [],
            "task_id": None,
            "status": "in_progress",
            "created_at": datetime.now(),
            "completed_at": None,
            "messages": [],
            "errors": [],
        }

        if stream:
            # Streaming execution (for WebSocket)
            return self._stream_execution(initial_state)
        else:
            # Regular execution (for HTTP)
            return await self._sync_execution(initial_state)

    async def _sync_execution(self, initial_state: ContentPipelineState) -> dict:
        """Execute graph synchronously"""

        try:
            # Run the graph to completion
            result = await self.content_graph.ainvoke(initial_state)

            logger.info(f"Pipeline completed: {result['request_id']}")

            return {
                "success": True,
                "request_id": result["request_id"],
                "task_id": result["task_id"],
                "status": result["status"],
                "quality_score": result["quality_score"],
                "refinement_count": result["refinement_count"],
                "content_preview": (
                    (result["final_content"][:500] + "...")
                    if result["final_content"]
                    else "No content generated"
                ),
            }

        except Exception as e:
            logger.error(f"Pipeline failed: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e), "request_id": initial_state["request_id"]}

    async def _stream_execution(self, initial_state: ContentPipelineState) -> AsyncIterator[dict]:
        """Execute graph with streaming events"""

        try:
            # Stream events from graph
            async for event in self.content_graph.astream(initial_state):
                # event is (node_name, state)
                node_name, state = event

                # Yield progress update
                yield {
                    "type": "progress",
                    "node": node_name,
                    "progress": self._calculate_progress(node_name),
                    "status": state.get("status", "processing"),
                    "quality_score": state.get("quality_score", 0),
                    "refinement_count": state.get("refinement_count", 0),
                    "current_content_preview": (
                        state.get("draft", "")[:300] if state.get("draft") else ""
                    ),
                }

            # Yield final result
            yield {
                "type": "complete",
                "request_id": state["request_id"],
                "task_id": state["task_id"],
                "quality_score": state["quality_score"],
                "refinements": state["refinement_count"],
                "status": "completed",
            }

        except Exception as e:
            logger.error(f"Streaming failed: {str(e)}", exc_info=True)
            yield {"type": "error", "error": str(e), "request_id": initial_state["request_id"]}

    @staticmethod
    def _calculate_progress(node_name: str) -> float:
        """Calculate progress percentage based on current node"""

        progress_map = {
            "research": 15,
            "outline": 30,
            "draft": 50,
            "assess": 70,
            "refine": 75,  # Could loop multiple times
            "finalize": 95,
            "end": 100,
        }

        return progress_map.get(node_name, 0)
