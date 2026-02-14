"""
Capability Tasks Service - Database operations for capability-based tasks.

Handles CRUD operations for task definitions and execution history.
"""

from typing import Any, Dict, List, Optional
from datetime import datetime
import uuid

from sqlalchemy import select, update, and_, desc
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert

from .capability_task_executor import (
    CapabilityTaskDefinition,
    CapabilityStep,
    TaskExecutionResult,
)


class CapabilityTask:
    """ORM model for capability_tasks table."""
    pass  # Will be mapped at runtime from SQLAlchemy


class CapabilityExecution:
    """ORM model for capability_executions table."""
    pass  # Will be mapped at runtime from SQLAlchemy


class CapabilityTasksService:
    """Database service for capability-based tasks."""
    
    def __init__(self, db_session: Session):
        """Initialize service with database session."""
        self.db = db_session
    
    # ============ Task Definition CRUD ============
    
    async def create_task(
        self,
        name: str,
        description: str,
        steps: List[CapabilityStep],
        owner_id: str,
        tags: Optional[List[str]] = None,
    ) -> CapabilityTaskDefinition:
        """
        Create a new capability task.
        
        Args:
            name: Task name
            description: Task description
            steps: List of capability steps
            owner_id: Owner user ID (for isolation)
            tags: Optional tags
            
        Returns:
            CapabilityTaskDefinition
        """
        task_id = str(uuid.uuid4())
        
        # Prepare steps as JSON
        steps_json = [step.to_dict() for step in steps]
        tags_json = tags or []
        
        # Insert task
        query = insert('capability_tasks').values(
            id=task_id,
            name=name,
            description=description,
            owner_id=owner_id,
            steps=steps_json,
            tags=tags_json,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            is_active=True,
            version=1,
        )
        
        self.db.execute(query)
        self.db.commit()
        
        return CapabilityTaskDefinition(
            id=task_id,
            name=name,
            description=description,
            steps=steps,
            tags=tags or [],
            owner_id=owner_id,
        )
    
    async def get_task(self, task_id: str, owner_id: str) -> Optional[CapabilityTaskDefinition]:
        """
        Get a task by ID (with owner isolation).
        
        Args:
            task_id: Task ID
            owner_id: Owner user ID
            
        Returns:
            CapabilityTaskDefinition or None
        """
        query = select('capability_tasks').where(
            and_(
                'capability_tasks.id' == task_id,
                'capability_tasks.owner_id' == owner_id,
            )
        )
        
        result = self.db.execute(query).first()
        if not result:
            return None
        
        # Reconstruct from database row
        steps = [
            CapabilityStep(
                capability_name=step['capability_name'],
                inputs=step['inputs'],
                output_key=step['output_key'],
                order=step.get('order', 0),
                metadata=step.get('metadata', {}),
            )
            for step in result['steps']
        ]
        
        return CapabilityTaskDefinition(
            id=result['id'],
            name=result['name'],
            description=result['description'],
            steps=steps,
            tags=result.get('tags', []),
            owner_id=result['owner_id'],
            created_at=result['created_at'],
        )
    
    async def list_tasks(
        self,
        owner_id: str,
        skip: int = 0,
        limit: int = 50,
        tags: Optional[List[str]] = None,
        active_only: bool = True,
    ) -> tuple[List[CapabilityTaskDefinition], int]:
        """
        List tasks for a user.
        
        Args:
            owner_id: Owner user ID
            skip: Pagination offset
            limit: Pagination limit
            tags: Filter by tags (any match)
            active_only: Only return active tasks
            
        Returns:
            Tuple of (tasks, total_count)
        """
        # Base query
        query = select('capability_tasks').where(
            'capability_tasks.owner_id' == owner_id
        )
        
        if active_only:
            query = query.where('capability_tasks.is_active' == True)
        
        # Count total
        count_query = select('COUNT(*)').select_from('capability_tasks').where(
            'capability_tasks.owner_id' == owner_id
        )
        if active_only:
            count_query = count_query.where('capability_tasks.is_active' == True)
        
        total = self.db.execute(count_query).scalar()
        
        # Apply ordering and pagination
        query = query.order_by(desc('capability_tasks.created_at')).offset(skip).limit(limit)
        
        results = self.db.execute(query).fetchall()
        
        tasks = []
        for row in results:
            steps = [
                CapabilityStep(
                    capability_name=step['capability_name'],
                    inputs=step['inputs'],
                    output_key=step['output_key'],
                    order=step.get('order', 0),
                )
                for step in row['steps']
            ]
            
            tasks.append(CapabilityTaskDefinition(
                id=row['id'],
                name=row['name'],
                description=row['description'],
                steps=steps,
                tags=row.get('tags', []),
                owner_id=row['owner_id'],
                created_at=row['created_at'],
            ))
        
        return tasks, total
    
    async def update_task(
        self,
        task_id: str,
        owner_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        steps: Optional[List[CapabilityStep]] = None,
        tags: Optional[List[str]] = None,
    ) -> Optional[CapabilityTaskDefinition]:
        """Update a task definition."""
        
        # Verify ownership
        exists = self.db.execute(
            select('capability_tasks').where(
                and_(
                    'capability_tasks.id' == task_id,
                    'capability_tasks.owner_id' == owner_id,
                )
            )
        ).first()
        
        if not exists:
            return None
        
        # Prepare update
        update_values = {
            'updated_at': datetime.utcnow(),
            'version': exists['version'] + 1,
        }
        
        if name is not None:
            update_values['name'] = name
        if description is not None:
            update_values['description'] = description
        if steps is not None:
            update_values['steps'] = [step.to_dict() for step in steps]
        if tags is not None:
            update_values['tags'] = tags
        
        # Execute update
        update_query = update('capability_tasks').where(
            'capability_tasks.id' == task_id
        ).values(**update_values)
        
        self.db.execute(update_query)
        self.db.commit()
        
        # Return updated task
        return await self.get_task(task_id, owner_id)
    
    async def delete_task(self, task_id: str, owner_id: str) -> bool:
        """
        Delete a task (soft delete - sets is_active=False).
        
        Args:
            task_id: Task ID
            owner_id: Owner user ID
            
        Returns:
            True if deleted, False if not found
        """
        result = self.db.execute(
            update('capability_tasks').where(
                and_(
                    'capability_tasks.id' == task_id,
                    'capability_tasks.owner_id' == owner_id,
                )
            ).values(is_active=False, updated_at=datetime.utcnow())
        )
        
        self.db.commit()
        return result.rowcount > 0
    
    # ============ Execution CRUD ============
    
    async def persist_execution(
        self,
        result: TaskExecutionResult,
    ) -> str:
        """
        Save execution result to database.
        
        Args:
            result: TaskExecutionResult from executor
            
        Returns:
            Execution ID
        """
        # Prepare execution record
        execution_record = {
            'id': result.execution_id,
            'task_id': result.task_id,
            'owner_id': result.owner_id,
            'status': result.status,
            'error_message': result.error,
            'step_results': [r.to_dict() for r in result.step_results],
            'final_outputs': result.final_outputs,
            'total_duration_ms': result.total_duration_ms,
            'progress_percent': result.progress_percent,
            'completed_steps': sum(1 for r in result.step_results if r.status == 'completed'),
            'total_steps': len(result.step_results),
            'started_at': result.started_at,
            'completed_at': result.completed_at,
            'created_at': datetime.utcnow(),
        }
        
        # Insert execution
        query = insert('capability_executions').values(**execution_record)
        self.db.execute(query)
        
        # Update task metrics
        self.db.execute(
            update('capability_tasks').where(
                'capability_tasks.id' == result.task_id
            ).values(
                execution_count='capability_tasks.execution_count + 1',
                success_count=(
                    'capability_tasks.success_count + 1'
                    if result.status == 'completed'
                    else 'capability_tasks.success_count'
                ),
                failure_count=(
                    'capability_tasks.failure_count + 1'
                    if result.status == 'failed'
                    else 'capability_tasks.failure_count'
                ),
                last_executed_at=datetime.utcnow(),
            )
        )
        
        self.db.commit()
        return result.execution_id
    
    async def get_execution(
        self,
        execution_id: str,
        owner_id: str,
    ) -> Optional[TaskExecutionResult]:
        """Get execution result by ID."""
        
        query = select('capability_executions').where(
            and_(
                'capability_executions.id' == execution_id,
                'capability_executions.owner_id' == owner_id,
            )
        )
        
        result = self.db.execute(query).first()
        if not result:
            return None
        
        return self._row_to_execution(result)
    
    async def list_executions(
        self,
        task_id: str,
        owner_id: str,
        skip: int = 0,
        limit: int = 50,
        status_filter: Optional[str] = None,
    ) -> tuple[List[TaskExecutionResult], int]:
        """
        List executions for a task.
        
        Args:
            task_id: Task ID
            owner_id: Owner user ID
            skip: Pagination offset
            limit: Pagination limit
            status_filter: Filter by status (pending, running, completed, failed)
            
        Returns:
            Tuple of (executions, total_count)
        """
        # Base query
        query = select('capability_executions').where(
            and_(
                'capability_executions.task_id' == task_id,
                'capability_executions.owner_id' == owner_id,
            )
        )
        
        if status_filter:
            query = query.where('capability_executions.status' == status_filter)
        
        # Count
        count_query = select('COUNT(*)').select_from('capability_executions').where(
            and_(
                'capability_executions.task_id' == task_id,
                'capability_executions.owner_id' == owner_id,
            )
        )
        if status_filter:
            count_query = count_query.where('capability_executions.status' == status_filter)
        
        total = self.db.execute(count_query).scalar()
        
        # Order and paginate
        query = query.order_by(desc('capability_executions.started_at')).offset(skip).limit(limit)
        
        results = self.db.execute(query).fetchall()
        executions = [self._row_to_execution(row) for row in results]
        
        return executions, total
    
    def _row_to_execution(self, row: Any) -> TaskExecutionResult:
        """Convert database row to TaskExecutionResult."""
        from .capability_task_executor import StepResult
        
        # Reconstruct step results
        step_results = []
        for step_data in row['step_results'] or []:
            step_results.append(StepResult(
                step_index=step_data['step_index'],
                capability_name=step_data['capability_name'],
                output_key=step_data['output_key'],
                output=step_data.get('output'),
                duration_ms=step_data.get('duration_ms', 0),
                error=step_data.get('error'),
                status=step_data.get('status', 'completed'),
            ))
        
        return TaskExecutionResult(
            task_id=row['task_id'],
            execution_id=row['id'],
            owner_id=row['owner_id'],
            status=row['status'],
            step_results=step_results,
            final_outputs=row.get('final_outputs', {}),
            total_duration_ms=row.get('total_duration_ms', 0),
            error=row.get('error_message'),
            started_at=row['started_at'],
            completed_at=row.get('completed_at'),
        )
