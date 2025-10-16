"""
Firestore client for GLAD Labs AI Co-Founder
Implements real-time database operations for tasks, logs, and financial data
"""

import os
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
try:
    from google.cloud import firestore  # type: ignore
    from google.cloud.firestore import DocumentReference, DocumentSnapshot  # type: ignore
except Exception:  # pragma: no cover - optional dependency at dev time
    firestore = None  # type: ignore
    DocumentReference = object  # type: ignore
    DocumentSnapshot = object  # type: ignore
    class _FireStoreStubs:
        SERVER_TIMESTAMP = None
        class Query:
            DESCENDING = None
    FIRESTORE_STUBS = _FireStoreStubs()
else:
    FIRESTORE_STUBS = firestore  # type: ignore
import structlog

# Configure structured logging
logger = structlog.get_logger(__name__)

class FirestoreClient:
    """
    Firestore client for managing GLAD Labs operational data
    
    Collections:
    - tasks: Content creation and operational tasks
    - agents: Agent status and configuration
    - financials: Expense tracking and burn rate data
    - logs: Structured logging for operations
    """
    
    def __init__(self, project_id: Optional[str] = None):
        """Initialize Firestore client with project configuration"""
        self.project_id = project_id or os.getenv('GCP_PROJECT_ID')
        self.dev_mode = os.getenv('DEV_MODE', 'false').lower() == 'true' or os.getenv('USE_MOCK_SERVICES', 'false').lower() == 'true'
        
        if not self.project_id:
            logger.warning("No GCP_PROJECT_ID found, using default project")
            self.project_id = "glad-labs-dev-local"
        
        try:
            if firestore is None:
                raise RuntimeError("google-cloud-firestore not installed or import failed")
            self.db = firestore.Client(project=self.project_id)
            
            if self.dev_mode:
                logger.info("Firestore client initialized in DEV MODE (local/mock services)", project_id=self.project_id)
            else:
                logger.info("Firestore client initialized", project_id=self.project_id)
        except Exception as e:
            logger.error("Failed to initialize Firestore client", error=str(e))
            if not self.dev_mode:
                raise
            else:
                logger.warning("Continuing in dev mode without Firestore functionality")
                self.db = None  # Set to None for dev mode fallback
    
    def _check_db_available(self) -> bool:
        """Check if database is available, log warning if in dev mode"""
        if self.db is None:
            if self.dev_mode:
                logger.debug("Firestore operation skipped - running in dev mode")
            return False
        return True
    
    # Task Management Methods
    async def add_task(self, task_data: Dict[str, Any]) -> str:
        """
        Add a new task to the tasks collection
        
        Args:
            task_data: Task information including topic, status, metadata
            
        Returns:
            Document ID of the created task
        """
        if not self._check_db_available():
            # Return a mock task ID in dev mode
            import uuid
            mock_id = str(uuid.uuid4())
            logger.info("Task created in DEV MODE (not persisted)", task_id=mock_id)
            return mock_id
            
        try:
            # Add timestamp and ensure required fields following data_schemas.md
            enhanced_task_data = {
                "taskName": task_data.get("taskName", f"Task: {task_data.get('topic', 'Unknown')}"),
                "agentId": task_data.get("agentId", "content-creation-agent-v1"),
                "status": task_data.get("status", "queued"),
                "createdAt": FIRESTORE_STUBS.SERVER_TIMESTAMP,
                "updatedAt": FIRESTORE_STUBS.SERVER_TIMESTAMP,
                
                # Core task data
                "topic": task_data.get("topic", "Unknown topic"),
                "primary_keyword": task_data.get("primary_keyword", "content"),
                "target_audience": task_data.get("target_audience", "General"),
                "category": task_data.get("category", "Blog Post"),
                
                # Metadata following GLAD Labs schema
                "metadata": {
                    "priority": task_data.get("metadata", {}).get("priority", 2),
                    "estimated_duration_minutes": task_data.get("metadata", {}).get("estimated_duration_minutes", 45),
                    "source": task_data.get("metadata", {}).get("source", "cofounder_orchestrator"),
                    "content_type": task_data.get("metadata", {}).get("content_type", "blog_post"),
                    "word_count_target": task_data.get("metadata", {}).get("word_count_target", 1500),
                    **task_data.get("metadata", {})  # Include any additional metadata
                }
            }
            
            # Add the task document
            doc_ref = self.db.collection('tasks').add(enhanced_task_data)[1]  # type: ignore[union-attr]
            
            logger.info("Task created successfully", 
                       task_id=doc_ref.id, 
                       topic=enhanced_task_data.get('topic', 'unknown'),
                       agent_id=enhanced_task_data.get('agentId', 'unknown'),
                       status=enhanced_task_data.get('status', 'unknown'))
            
            return doc_ref.id
            
        except Exception as e:
            logger.error("Failed to add task", error=str(e), task_data=task_data)
            raise
    
    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific task by ID"""
        if not self._check_db_available():
            logger.debug("Get task skipped - dev mode")
            return None
            
        try:
            doc_ref = self.db.collection('tasks').document(task_id)  # type: ignore[union-attr]
            doc = doc_ref.get()
            
            if doc.exists:
                data = doc.to_dict()
                data['id'] = doc.id
                return data
            else:
                logger.warning("Task not found", task_id=task_id)
                return None
                
        except Exception as e:
            logger.error("Failed to get task", task_id=task_id, error=str(e))
            raise
    
    async def update_task_status(self, task_id: str, status: str, 
                                metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Update task status and optional metadata"""
        if not self._check_db_available():
            logger.debug("Update task status skipped - dev mode")
            return True  # Return success in dev mode
            
        try:
            update_data = {
                'status': status,
                'updated_at': FIRESTORE_STUBS.SERVER_TIMESTAMP
            }
            
            if metadata:
                update_data.update(metadata)
            
            doc_ref = self.db.collection('tasks').document(task_id)  # type: ignore[union-attr]
            doc_ref.update(update_data)
            
            logger.info("Task status updated", 
                       task_id=task_id, 
                       status=status)
            
            return True
            
        except Exception as e:
            logger.error("Failed to update task status", 
                        task_id=task_id, 
                        status=status, 
                        error=str(e))
            return False
    
    async def get_pending_tasks(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get pending tasks for processing"""
        if not self._check_db_available():
            logger.debug("Get pending tasks skipped - dev mode")
            return []
            
        try:
            query = (self.db.collection('tasks')  # type: ignore[union-attr]
                    .where('status', '==', 'pending')
                    .order_by('created_at')
                    .limit(limit))
            
            tasks = []
            for doc in query.stream():
                data = doc.to_dict()
                data['id'] = doc.id
                tasks.append(data)
            
            logger.info("Retrieved pending tasks", count=len(tasks))
            return tasks
            
        except Exception as e:
            logger.error("Failed to get pending tasks", error=str(e))
            raise
    
    # Financial Tracking Methods
    async def add_financial_entry(self, entry_data: Dict[str, Any]) -> str:
        """Add a financial transaction entry"""
        if not self._check_db_available():
            import uuid
            mock_id = str(uuid.uuid4())
            logger.info("Financial entry created in DEV MODE (not persisted)", entry_id=mock_id)
            return mock_id
            
        try:
            entry_data.update({
                'created_at': FIRESTORE_STUBS.SERVER_TIMESTAMP,
                'timestamp': entry_data.get('timestamp', datetime.utcnow())
            })
            
            doc_ref = self.db.collection('financials').add(entry_data)[1]  # type: ignore[union-attr]
            
            logger.info("Financial entry added", 
                       entry_id=doc_ref.id,
                       amount=entry_data.get('amount', 0),
                       category=entry_data.get('category', 'unknown'))
            
            return doc_ref.id
            
        except Exception as e:
            logger.error("Failed to add financial entry", error=str(e))
            raise
    
    async def get_financial_summary(self, days: int = 30) -> Dict[str, Any]:
        """Get financial summary for the specified period"""
        if not self._check_db_available():
            logger.debug("Get financial summary skipped - dev mode")
            return {'total_spend': 0.0, 'entry_count': 0, 'average_daily_spend': 0.0, 'entries': []}
            
        try:
            from datetime import timedelta
            
            start_date = datetime.utcnow() - timedelta(days=days)
            
            query = (self.db.collection('financials')  # type: ignore[union-attr]
                    .where('timestamp', '>=', start_date)
                    .order_by('timestamp', direction=FIRESTORE_STUBS.Query.DESCENDING))
            
            entries = []
            total_spend = 0.0
            
            for doc in query.stream():
                data = doc.to_dict()
                data['id'] = doc.id
                entries.append(data)
                total_spend += data.get('amount', 0)
            
            summary = {
                'total_spend': total_spend,
                'entry_count': len(entries),
                'average_daily_spend': total_spend / days if days > 0 else 0,
                'entries': entries
            }
            
            logger.info("Financial summary generated", 
                       total_spend=total_spend,
                       entry_count=len(entries),
                       period_days=days)
            
            return summary
            
        except Exception as e:
            logger.error("Failed to get financial summary", error=str(e))
            raise
    
    # Agent Status Methods
    async def update_agent_status(self, agent_name: str, status_data: Dict[str, Any]) -> bool:
        """Update agent status and heartbeat"""
        if not self._check_db_available():
            logger.debug("Update agent status skipped - dev mode", agent_name=agent_name)
            return True
            
        try:
            status_data.update({
                'last_heartbeat': FIRESTORE_STUBS.SERVER_TIMESTAMP,
                'updated_at': FIRESTORE_STUBS.SERVER_TIMESTAMP
            })
            
            doc_ref = self.db.collection('agents').document(agent_name)  # type: ignore[union-attr]
            doc_ref.set(status_data, merge=True)
            
            logger.info("Agent status updated", 
                       agent_name=agent_name,
                       status=status_data.get('status', 'unknown'))
            
            return True
            
        except Exception as e:
            logger.error("Failed to update agent status", 
                        agent_name=agent_name, 
                        error=str(e))
            return False
    
    async def get_agent_status(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """Get current status of a specific agent"""
        if not self._check_db_available():
            logger.debug("Get agent status skipped - dev mode")
            return None
            
        try:
            doc_ref = self.db.collection('agents').document(agent_name)  # type: ignore[union-attr]
            doc = doc_ref.get()
            
            if doc.exists:
                return doc.to_dict()
            else:
                logger.warning("Agent status not found", agent_name=agent_name)
                return None
                
        except Exception as e:
            logger.error("Failed to get agent status", 
                        agent_name=agent_name, 
                        error=str(e))
            raise
    
    # Logging Methods
    async def add_log_entry(self, level: str, message: str, 
                           metadata: Optional[Dict[str, Any]] = None) -> str:
        """Add a structured log entry"""
        if not self._check_db_available():
            import uuid
            return str(uuid.uuid4())
            
        try:
            log_data = {
                'level': level,
                'message': message,
                'timestamp': FIRESTORE_STUBS.SERVER_TIMESTAMP,
                'metadata': metadata or {}
            }
            
            doc_ref = self.db.collection('logs').add(log_data)[1]  # type: ignore[union-attr]
            return doc_ref.id
            
        except Exception as e:
            logger.error("Failed to add log entry", error=str(e))
            raise
    
    # Health Check Methods
    async def health_check(self) -> Dict[str, Any]:
        """Perform a basic health check on Firestore connection"""
        if not self._check_db_available():
            return {
                'status': 'dev_mode',
                'timestamp': datetime.utcnow().isoformat(),
                'project_id': self.project_id,
                'message': 'Running in development mode without Firestore'
            }
            
        try:
            # Try to read from a test collection
            test_doc = self.db.collection('health').document('check')  # type: ignore[union-attr]
            test_doc.set({
                'timestamp': FIRESTORE_STUBS.SERVER_TIMESTAMP,
                'status': 'healthy'
            })
            
            # Read it back
            doc = test_doc.get()
            if doc.exists:
                return {
                    'status': 'healthy',
                    'timestamp': datetime.utcnow().isoformat(),
                    'project_id': self.project_id
                }
            else:
                return {
                    'status': 'unhealthy',
                    'error': 'Could not verify write operation'
                }
                
        except Exception as e:
            logger.error("Health check failed", error=str(e))
            return {
                'status': 'unhealthy',
                'error': str(e)
            }
    # Content Pipeline Methods
    async def add_content_task(self, task_data: Dict[str, Any]) -> str:
        """Add a new content creation task to the content_tasks collection"""
        if not self._check_db_available():
            import uuid
            mock_id = str(uuid.uuid4())
            logger.info("Content task created in DEV MODE (not persisted)", task_id=mock_id, topic=task_data.get("topic"))
            return mock_id
            
        try:
            enhanced_task_data = {
                "topic": task_data.get("topic"),
                "primary_keyword": task_data.get("primary_keyword", ""),
                "target_audience": task_data.get("target_audience", "general"),
                "category": task_data.get("category", "uncategorized"),
                "status": task_data.get("status", "New"),
                "auto_publish": task_data.get("auto_publish", False),
                "source": task_data.get("source", "api"),
                "created_at": FIRESTORE_STUBS.SERVER_TIMESTAMP,
                "updated_at": FIRESTORE_STUBS.SERVER_TIMESTAMP,
            }
            
            doc_ref = self.db.collection('content_tasks').document()
            doc_ref.set(enhanced_task_data)
            
            logger.info("Content task created", task_id=doc_ref.id, topic=task_data.get("topic"))
            return doc_ref.id
            
        except Exception as e:
            logger.error("Failed to add content task", error=str(e))
            raise
    
    async def get_content_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get a content task by ID"""
        if not self._check_db_available():
            logger.debug("Content task retrieval skipped - running in dev mode")
            return None
            
        try:
            doc_ref = self.db.collection('content_tasks').document(task_id)
            doc = doc_ref.get()
            
            if doc.exists:
                task_data = doc.to_dict()
                task_data['id'] = doc.id
                return task_data
            else:
                logger.warning("Content task not found", task_id=task_id)
                return None
                
        except Exception as e:
            logger.error("Failed to get content task", task_id=task_id, error=str(e))
            raise
    
    async def get_task_runs(self, task_id: str) -> List[Dict[str, Any]]:
        """Get all run logs for a content task"""
        if not self._check_db_available():
            return []
            
        try:
            runs_ref = self.db.collection('runs').where('task_id', '==', task_id)
            docs = runs_ref.order_by('created_at', direction=FIRESTORE_STUBS.Query.DESCENDING).stream()
            
            runs = []
            for doc in docs:
                run_data = doc.to_dict()
                run_data['id'] = doc.id
                runs.append(run_data)
            
            return runs
            
        except Exception as e:
            logger.error("Failed to get task runs", task_id=task_id, error=str(e))
            return []
    
    async def log_webhook_event(self, webhook_data: Dict[str, Any]) -> str:
        """Log a webhook event from Strapi"""
        if not self._check_db_available():
            import uuid
            mock_id = str(uuid.uuid4())
            logger.info("Webhook event logged in DEV MODE (not persisted)", event_id=mock_id, event=webhook_data.get("event"))
            return mock_id
            
        try:
            enhanced_webhook_data = {
                **webhook_data,
                "logged_at": FIRESTORE_STUBS.SERVER_TIMESTAMP
            }
            
            doc_ref = self.db.collection('webhooks').document()
            doc_ref.set(enhanced_webhook_data)
            
            logger.info("Webhook event logged", event_id=doc_ref.id, event=webhook_data.get("event"))
            return doc_ref.id
            
        except Exception as e:
            logger.error("Failed to log webhook event", error=str(e))
            raise
