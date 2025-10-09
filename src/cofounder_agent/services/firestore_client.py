"""
Firestore client for GLAD Labs AI Co-Founder
Implements real-time database operations for tasks, logs, and financial data
"""

import os
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
from google.cloud import firestore
from google.cloud.firestore import DocumentReference, DocumentSnapshot
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
        if not self.project_id:
            logger.warning("No GCP_PROJECT_ID found, using default project")
        
        try:
            self.db = firestore.Client(project=self.project_id)
            logger.info("Firestore client initialized", project_id=self.project_id)
        except Exception as e:
            logger.error("Failed to initialize Firestore client", error=str(e))
            raise
    
    # Task Management Methods
    async def add_task(self, task_data: Dict[str, Any]) -> str:
        """
        Add a new task to the tasks collection
        
        Args:
            task_data: Task information including topic, status, metadata
            
        Returns:
            Document ID of the created task
        """
        try:
            # Add timestamp and ensure required fields
            task_data.update({
                'created_at': firestore.SERVER_TIMESTAMP,
                'updated_at': firestore.SERVER_TIMESTAMP,
                'status': task_data.get('status', 'pending')
            })
            
            # Add the task document
            doc_ref = self.db.collection('tasks').add(task_data)[1]
            
            logger.info("Task created successfully", 
                       task_id=doc_ref.id, 
                       topic=task_data.get('topic', 'unknown'))
            
            return doc_ref.id
            
        except Exception as e:
            logger.error("Failed to add task", error=str(e), task_data=task_data)
            raise
    
    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific task by ID"""
        try:
            doc_ref = self.db.collection('tasks').document(task_id)
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
        try:
            update_data = {
                'status': status,
                'updated_at': firestore.SERVER_TIMESTAMP
            }
            
            if metadata:
                update_data.update(metadata)
            
            doc_ref = self.db.collection('tasks').document(task_id)
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
        try:
            query = (self.db.collection('tasks')
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
        try:
            entry_data.update({
                'created_at': firestore.SERVER_TIMESTAMP,
                'timestamp': entry_data.get('timestamp', datetime.utcnow())
            })
            
            doc_ref = self.db.collection('financials').add(entry_data)[1]
            
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
        try:
            from datetime import timedelta
            
            start_date = datetime.utcnow() - timedelta(days=days)
            
            query = (self.db.collection('financials')
                    .where('timestamp', '>=', start_date)
                    .order_by('timestamp', direction=firestore.Query.DESCENDING))
            
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
        try:
            status_data.update({
                'last_heartbeat': firestore.SERVER_TIMESTAMP,
                'updated_at': firestore.SERVER_TIMESTAMP
            })
            
            doc_ref = self.db.collection('agents').document(agent_name)
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
        try:
            doc_ref = self.db.collection('agents').document(agent_name)
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
        try:
            log_data = {
                'level': level,
                'message': message,
                'timestamp': firestore.SERVER_TIMESTAMP,
                'metadata': metadata or {}
            }
            
            doc_ref = self.db.collection('logs').add(log_data)[1]
            return doc_ref.id
            
        except Exception as e:
            logger.error("Failed to add log entry", error=str(e))
            raise
    
    # Health Check Methods
    async def health_check(self) -> Dict[str, Any]:
        """Perform a basic health check on Firestore connection"""
        try:
            # Try to read from a test collection
            test_doc = self.db.collection('health').document('check')
            test_doc.set({
                'timestamp': firestore.SERVER_TIMESTAMP,
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