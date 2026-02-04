"""
Command Queue Service - Replaces Pub/Sub Messaging
Provides API-based async task dispatch for agent communication

Features:
- In-memory task queue for local development
- PostgreSQL persistence for production
- Async task dispatch via FastAPI endpoints
- Agent polling/callback pattern
"""

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class CommandStatus(str, Enum):
    """Command execution status"""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Command:  # pylint: disable=too-many-instance-attributes
    """Command data structure"""

    id: str = field(default_factory=lambda: str(uuid4()))
    agent_type: str = ""  # "content", "financial", "compliance", etc.
    action: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    status: CommandStatus = CommandStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        data = asdict(self)
        data["status"] = self.status.value
        return data


class CommandQueue:
    """
    Local in-memory command queue

    In production with PostgreSQL, this would be replaced with a database-backed queue.
    For now, this provides the interface and in-memory implementation.
    """

    def __init__(self):
        """Initialize command queue"""
        self.commands: Dict[str, Command] = {}
        self.queue: asyncio.Queue = asyncio.Queue()
        self.handlers: Dict[str, List[Callable]] = {}
        self._running = False

        logger.info("CommandQueue initialized (in-memory)")

    async def enqueue(self, command: Command) -> str:
        """
        Enqueue a command for processing

        Args:
            command: Command to enqueue

        Returns:
            Command ID
        """
        self.commands[command.id] = command
        await self.queue.put(command.id)

        logger.info(
            f"Command enqueued: {command.id} (agent={command.agent_type}, action={command.action})"
        )

        return command.id

    async def dequeue(self, timeout: Optional[float] = None) -> Optional[Command]:
        """
        Dequeue a command for processing

        Args:
            timeout: Optional timeout in seconds

        Returns:
            Command or None if timeout
        """
        try:
            command_id = await asyncio.wait_for(self.queue.get(), timeout=timeout)
            command = self.commands.get(command_id)

            if command:
                command.status = CommandStatus.PROCESSING
                command.started_at = datetime.utcnow().isoformat()
                command.updated_at = datetime.utcnow().isoformat()

            return command
        except asyncio.TimeoutError:
            return None

    async def get_command(self, command_id: str) -> Optional[Command]:
        """Get command by ID"""
        return self.commands.get(command_id)

    async def list_commands(self, status: Optional[CommandStatus] = None) -> List[Command]:
        """List commands, optionally filtered by status"""
        commands = list(self.commands.values())

        if status:
            commands = [c for c in commands if c.status == status]

        # Sort by created_at descending
        commands.sort(key=lambda c: c.created_at, reverse=True)

        return commands

    async def complete_command(self, command_id: str, result: Dict[str, Any]) -> Optional[Command]:
        """Mark command as completed"""
        command = self.commands.get(command_id)

        if not command:
            logger.warning(f"Command not found: {command_id}")
            return None

        command.status = CommandStatus.COMPLETED
        command.result = result
        command.completed_at = datetime.utcnow().isoformat()
        command.updated_at = datetime.utcnow().isoformat()

        logger.info(f"Command completed: {command_id}")

        # Notify handlers
        await self._notify_handlers(command)

        return command

    async def fail_command(
        self, command_id: str, error: str, retry: bool = True
    ) -> Optional[Command]:
        """Mark command as failed"""
        command = self.commands.get(command_id)

        if not command:
            logger.warning(f"Command not found: {command_id}")
            return None

        command.error = error
        command.updated_at = datetime.utcnow().isoformat()

        # Retry logic
        if retry and command.retry_count < command.max_retries:
            command.status = CommandStatus.PENDING
            command.retry_count += 1
            await self.queue.put(command_id)
            logger.info(f"Command retrying: {command_id} (attempt {command.retry_count})")
        else:
            command.status = CommandStatus.FAILED
            logger.error(f"Command failed: {command_id} - {error}")

        return command

    async def cancel_command(self, command_id: str) -> Optional[Command]:
        """Cancel a command"""
        command = self.commands.get(command_id)

        if not command:
            logger.warning(f"Command not found: {command_id}")
            return None

        if command.status in [CommandStatus.COMPLETED, CommandStatus.FAILED]:
            logger.warning(f"Cannot cancel completed/failed command: {command_id}")
            return command

        command.status = CommandStatus.CANCELLED
        command.updated_at = datetime.utcnow().isoformat()

        logger.info(f"Command cancelled: {command_id}")

        return command

    def register_handler(self, agent_type: str, handler: Callable):
        """Register a handler for command completion"""
        if agent_type not in self.handlers:
            self.handlers[agent_type] = []

        self.handlers[agent_type].append(handler)
        logger.info(f"Handler registered for agent: {agent_type}")

    async def _notify_handlers(self, command: Command):
        """Notify registered handlers of command completion"""
        handlers = self.handlers.get(command.agent_type, [])

        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(command)
                else:
                    handler(command)
            except Exception as e:
                logger.error(f"Handler error for {command.agent_type}: {e}")

    async def clear_old_commands(self, max_age_hours: int = 24):
        """Clear old completed commands"""
        from datetime import datetime, timedelta

        now = datetime.utcnow()
        cutoff = now - timedelta(hours=max_age_hours)

        to_delete = []
        for cmd_id, cmd in self.commands.items():
            if cmd.status == CommandStatus.COMPLETED:
                cmd_updated = datetime.fromisoformat(cmd.updated_at)
                if cmd_updated < cutoff:
                    to_delete.append(cmd_id)

        for cmd_id in to_delete:
            del self.commands[cmd_id]

        logger.info(f"Cleared {len(to_delete)} old commands")

    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics"""
        by_status = {}
        for cmd in self.commands.values():
            status = cmd.status.value
            by_status[status] = by_status.get(status, 0) + 1

        return {
            "total_commands": len(self.commands),
            "pending_commands": self.queue.qsize(),
            "by_status": by_status,
            "timestamp": datetime.utcnow().isoformat(),
        }


# Global singleton instance
_command_queue: Optional[CommandQueue] = None


def get_command_queue() -> CommandQueue:
    """Get or create the global command queue"""
    global _command_queue

    if _command_queue is None:
        _command_queue = CommandQueue()

    return _command_queue


async def create_command(
    agent_type: str, action: str, payload: Optional[Dict[str, Any]] = None
) -> Command:
    """
    Create and enqueue a command

    Args:
        agent_type: Type of agent ("content", "financial", etc.)
        action: Action to perform
        payload: Command payload

    Returns:
        Command object
    """
    command = Command(agent_type=agent_type, action=action, payload=payload or {})

    queue = get_command_queue()
    await queue.enqueue(command)

    return command


# Example usage functions for API routes


async def dispatch_content_generation(topic: str, style: str = "blog") -> Dict[str, Any]:
    """Dispatch content generation command"""
    cmd = await create_command(
        agent_type="content", action="generate_content", payload={"topic": topic, "style": style}
    )
    return {"command_id": cmd.id, "status": cmd.status.value}


async def dispatch_financial_analysis(period: str = "monthly") -> Dict[str, Any]:
    """Dispatch financial analysis command"""
    cmd = await create_command(agent_type="financial", action="analyze", payload={"period": period})
    return {"command_id": cmd.id, "status": cmd.status.value}


async def dispatch_compliance_check(content_id: str) -> Dict[str, Any]:
    """Dispatch compliance check command"""
    cmd = await create_command(
        agent_type="compliance", action="check_content", payload={"content_id": content_id}
    )
    return {"command_id": cmd.id, "status": cmd.status.value}
