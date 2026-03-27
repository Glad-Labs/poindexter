"""
Workflow Progress Client Library

Provides a client for interacting with the Workflow Progress Tracking API.
Simplifies progress update operations and WebSocket subscription management.

Usage:
    client = WorkflowProgressClient("http://localhost:8000")

    # Initialize progress
    await client.initialize_progress(
        execution_id="exec_123",
        total_phases=4,
        template="content_generation"
    )

    # Start execution
    await client.start_execution("exec_123", "Beginning workflow...")

    # Complete phases
    await client.complete_phase(
        "exec_123",
        phase_name="research",
        phase_output={"keywords": ["ai", "ml"]},
        duration_ms=5000
    )

    # Mark completion
    await client.mark_complete(
        "exec_123",
        final_output={"content": "..."},
        duration_ms=30000
    )
"""

import asyncio
import json
from datetime import datetime
from typing import Any, Callable, Dict, Optional

import aiohttp
import websockets

from services.logger_config import get_logger

logger = get_logger(__name__)


class WorkflowProgressClient:
    """Client for interacting with Workflow Progress Tracking API"""

    def __init__(self, base_url: str = "http://localhost:8000"):
        """
        Initialize the progress client.

        Args:
            base_url: Base URL of the API (default: localhost:8000)
        """
        self.base_url = base_url.rstrip("/")
        self.api_base = f"{self.base_url}/api/workflow-progress"
        self.session: Optional[aiohttp.ClientSession] = None
        self.ws_connections: Dict[str, Any] = {}

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure HTTP session is initialized"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        """Close HTTP session and WebSocket connections"""
        # Close WebSocket connections
        for execution_id, ws_data in self.ws_connections.items():
            if "websocket" in ws_data:
                try:
                    await ws_data["websocket"].close()
                except Exception as e:
                    logger.debug(f"Error closing WebSocket for {execution_id}: {e}")

        # Close HTTP session
        if self.session and not self.session.closed:
            await self.session.close()

    async def initialize_progress(
        self,
        execution_id: str,
        workflow_id: Optional[str] = None,
        template: Optional[str] = None,
        total_phases: int = 0,
    ) -> Dict[str, Any]:
        """
        Initialize progress tracking for a workflow execution.

        Args:
            execution_id: Unique execution identifier
            workflow_id: Optional workflow ID
            template: Optional template name
            total_phases: Number of phases in workflow

        Returns:
            Response dict with initialized progress
        """
        session = await self._ensure_session()
        url = f"{self.api_base}/initialize/{execution_id}"
        params = {
            "workflow_id": workflow_id,
            "template": template,
            "total_phases": total_phases,
        }

        try:
            async with session.post(url, params=params) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            logger.error(f"Failed to initialize progress: {e}", exc_info=True)
            raise

    async def start_execution(
        self,
        execution_id: str,
        message: str = "Starting workflow execution...",
    ) -> Dict[str, Any]:
        """
        Mark execution as started.

        Args:
            execution_id: Execution identifier
            message: Optional status message

        Returns:
            Response dict with updated progress
        """
        session = await self._ensure_session()
        url = f"{self.api_base}/start/{execution_id}"
        params = {"message": message}

        try:
            async with session.post(url, params=params) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            logger.error(f"Failed to start execution: {e}", exc_info=True)
            raise

    async def start_phase(
        self,
        execution_id: str,
        phase_index: int,
        phase_name: str,
        message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Mark the start of a workflow phase.

        Args:
            execution_id: Execution identifier
            phase_index: Zero-based phase index
            phase_name: Name of the phase
            message: Optional status message

        Returns:
            Response dict with updated progress
        """
        session = await self._ensure_session()
        url = f"{self.api_base}/phase/start/{execution_id}"
        params = {
            "phase_index": phase_index,
            "phase_name": phase_name,
        }
        if message:
            params["message"] = message

        try:
            async with session.post(url, params=params) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            logger.error(f"Failed to start phase: {e}", exc_info=True)
            raise

    async def complete_phase(
        self,
        execution_id: str,
        phase_name: str,
        phase_output: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Mark a phase as completed.

        Args:
            execution_id: Execution identifier
            phase_name: Name of the completed phase
            phase_output: Optional phase output data
            duration_ms: Phase duration in milliseconds

        Returns:
            Response dict with updated progress
        """
        session = await self._ensure_session()
        url = f"{self.api_base}/phase/complete/{execution_id}"
        params = {"phase_name": phase_name}
        if duration_ms is not None:
            params["duration_ms"] = str(duration_ms)

        try:
            async with session.post(
                url, params=params, json={"phase_output": phase_output}
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            logger.error(f"Failed to complete phase: {e}", exc_info=True)
            raise

    async def fail_phase(
        self,
        execution_id: str,
        phase_name: str,
        error: str,
    ) -> Dict[str, Any]:
        """
        Mark a phase as failed.

        Args:
            execution_id: Execution identifier
            phase_name: Name of the failed phase
            error: Error message

        Returns:
            Response dict with updated progress
        """
        session = await self._ensure_session()
        url = f"{self.api_base}/phase/fail/{execution_id}"
        params = {
            "phase_name": phase_name,
            "error": error,
        }

        try:
            async with session.post(url, params=params) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            logger.error(f"Failed to fail phase: {e}", exc_info=True)
            raise

    async def mark_complete(
        self,
        execution_id: str,
        final_output: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[float] = None,
        message: str = "Workflow execution completed",
    ) -> Dict[str, Any]:
        """
        Mark execution as completed.

        Args:
            execution_id: Execution identifier
            final_output: Optional final output data
            duration_ms: Total execution duration in milliseconds
            message: Completion message

        Returns:
            Response dict with final progress
        """
        session = await self._ensure_session()
        url = f"{self.api_base}/complete/{execution_id}"
        params = {"message": message}
        if duration_ms is not None:
            params["duration_ms"] = str(duration_ms)

        try:
            async with session.post(
                url, params=params, json={"final_output": final_output}
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            logger.error(f"Failed to mark execution as complete: {e}", exc_info=True)
            raise

    async def mark_failed(
        self,
        execution_id: str,
        error: str,
        failed_phase: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Mark execution as failed.

        Args:
            execution_id: Execution identifier
            error: Error message
            failed_phase: Optional name of phase that failed

        Returns:
            Response dict with final progress
        """
        session = await self._ensure_session()
        url = f"{self.api_base}/fail/{execution_id}"
        params = {"error": error}
        if failed_phase:
            params["failed_phase"] = failed_phase

        try:
            async with session.post(url, params=params) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            logger.error(f"Failed to mark execution as failed: {e}", exc_info=True)
            raise

    async def get_status(self, execution_id: str) -> Dict[str, Any]:
        """
        Get current progress status.

        Args:
            execution_id: Execution identifier

        Returns:
            Progress object
        """
        session = await self._ensure_session()
        url = f"{self.api_base}/status/{execution_id}"

        try:
            async with session.get(url) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            logger.error(f"Failed to get progress status: {e}", exc_info=True)
            raise

    async def cleanup(self, execution_id: str) -> Dict[str, Any]:
        """
        Clean up progress tracking for an execution.

        Args:
            execution_id: Execution identifier

        Returns:
            Response dict with cleanup confirmation
        """
        session = await self._ensure_session()
        url = f"{self.api_base}/cleanup/{execution_id}"

        try:
            async with session.delete(url) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            logger.error(f"Failed to cleanup progress: {e}", exc_info=True)
            raise

    async def subscribe_progress(
        self,
        execution_id: str,
        callback: Callable[[Dict[str, Any]], Any],
        auto_reconnect: bool = True,
    ) -> None:
        """
        Subscribe to real-time progress updates via WebSocket.

        Args:
            execution_id: Execution identifier
            callback: Async function to call with progress updates
            auto_reconnect: Whether to auto-reconnect on disconnect

        Example:
            async def on_progress(progress):
                print(f"Phase: {progress['progress']['phase_name']}")
                print(f"Progress: {progress['progress']['progress_percent']}%")

            await client.subscribe_progress("exec_123", on_progress)
        """
        ws_url = f"ws://localhost:8000/api/workflow-progress/ws/{execution_id}".replace(
            "http://", ""
        ).replace("https://", "")

        backoff = 1
        max_backoff = 32

        while True:
            try:
                async with websockets.connect(f"ws://{ws_url.split('ws://')[-1]}") as websocket:
                    logger.info(f"Connected to progress WebSocket for {execution_id}")
                    self.ws_connections[execution_id] = {"websocket": websocket}
                    backoff = 1  # Reset backoff on successful connection

                    async for message in websocket:
                        try:
                            data = json.loads(message)
                            if callable(callback):
                                if asyncio.iscoroutinefunction(callback):
                                    await callback(data)
                                else:
                                    callback(data)
                        except json.JSONDecodeError:
                            logger.debug(f"Received non-JSON message: {message}")
                        except Exception as e:
                            logger.error(f"Error processing progress update: {e}", exc_info=True)

            except Exception as e:
                logger.error(f"WebSocket error for {execution_id}: {e}", exc_info=True)

                if not auto_reconnect:
                    raise

                logger.info(f"Reconnecting in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    async def unsubscribe_progress(self, execution_id: str) -> None:
        """
        Unsubscribe from progress updates.

        Args:
            execution_id: Execution identifier
        """
        if execution_id in self.ws_connections:
            try:
                websocket = self.ws_connections[execution_id].get("websocket")
                if websocket:
                    await websocket.close()
            except Exception as e:
                logger.debug(f"Error closing WebSocket: {e}")
            finally:
                del self.ws_connections[execution_id]
                logger.info(f"Unsubscribed from progress for {execution_id}")


# Convenience function for getting a client instance
def get_progress_client(base_url: str = "http://localhost:8000") -> WorkflowProgressClient:
    """
    Get a WorkflowProgressClient instance.

    Args:
        base_url: Base URL of the API

    Returns:
        WorkflowProgressClient instance
    """
    return WorkflowProgressClient(base_url)
