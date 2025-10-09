"""
Performance monitoring and metrics collection for GLAD Labs AI Co-Founder
Implements structured logging, performance tracking, and operational metrics
"""

import time
import asyncio
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import json
from contextlib import asynccontextmanager
import structlog

# Configure structured logging for performance monitoring
logger = structlog.get_logger(__name__)

class PerformanceMonitor:
    """
    Performance monitoring service for GLAD Labs AI Co-Founder system
    
    Tracks:
    - Command processing times
    - Database operation latencies
    - Pub/Sub message processing rates
    - Agent response times
    - Error rates and patterns
    - Resource utilization metrics
    """
    
    def __init__(self, firestore_client=None):
        """Initialize performance monitor with optional Firestore client for persistence"""
        self.firestore_client = firestore_client
        self.metrics_cache = {}
        self.active_operations = {}
        
        # Performance counters
        self.command_count = 0
        self.error_count = 0
        self.total_processing_time = 0.0
        self.database_operation_count = 0
        self.pubsub_message_count = 0
        
        logger.info("Performance monitor initialized", 
                   firestore_available=firestore_client is not None)
    
    @asynccontextmanager
    async def track_operation(self, operation_name: str, metadata: Optional[Dict[str, Any]] = None):
        """
        Context manager to track operation performance
        
        Args:
            operation_name: Name of the operation being tracked
            metadata: Optional metadata about the operation
        """
        operation_id = f"{operation_name}_{int(time.time() * 1000)}"
        start_time = time.time()
        
        # Log operation start
        logger.info("Operation started", 
                   operation_id=operation_id,
                   operation_name=operation_name,
                   metadata=metadata or {})
        
        self.active_operations[operation_id] = {
            "name": operation_name,
            "start_time": start_time,
            "metadata": metadata or {}
        }
        
        try:
            yield operation_id
            
            # Operation completed successfully
            duration = time.time() - start_time
            await self._record_operation_success(operation_id, operation_name, duration, metadata)
            
        except Exception as e:
            # Operation failed
            duration = time.time() - start_time
            await self._record_operation_error(operation_id, operation_name, duration, str(e), metadata)
            raise
        finally:
            # Clean up tracking
            self.active_operations.pop(operation_id, None)
    
    async def track_command_processing(self, command: str, processing_time: float, 
                                     status: str, metadata: Optional[Dict[str, Any]] = None):
        """Track command processing metrics"""
        try:
            self.command_count += 1
            self.total_processing_time += processing_time
            
            if status == "error":
                self.error_count += 1
            
            # Calculate moving averages
            avg_processing_time = self.total_processing_time / self.command_count if self.command_count > 0 else 0
            error_rate = (self.error_count / self.command_count) * 100 if self.command_count > 0 else 0
            
            metrics_data = {
                "metric_type": "command_processing",
                "command": command,
                "processing_time_seconds": processing_time,
                "status": status,
                "timestamp": datetime.utcnow().isoformat(),
                "session_metrics": {
                    "total_commands": self.command_count,
                    "average_processing_time": avg_processing_time,
                    "error_rate_percent": error_rate,
                    "total_errors": self.error_count
                },
                "metadata": metadata or {}
            }
            
            logger.info("Command processing tracked",
                       command=command,
                       processing_time=processing_time,
                       status=status,
                       avg_processing_time=avg_processing_time,
                       error_rate=error_rate)
            
            # Store metrics in Firestore if available
            if self.firestore_client:
                await self._store_metrics(metrics_data)
            
            # Update cache
            self.metrics_cache["latest_command_metrics"] = metrics_data
            
        except Exception as e:
            logger.error("Failed to track command processing", error=str(e))
    
    async def track_database_operation(self, operation_type: str, collection: str, 
                                     duration: float, success: bool, 
                                     record_count: Optional[int] = None):
        """Track database operation performance"""
        try:
            self.database_operation_count += 1
            
            metrics_data = {
                "metric_type": "database_operation",
                "operation_type": operation_type,  # 'read', 'write', 'update', 'delete'
                "collection": collection,
                "duration_seconds": duration,
                "success": success,
                "record_count": record_count,
                "timestamp": datetime.utcnow().isoformat(),
                "session_metrics": {
                    "total_db_operations": self.database_operation_count
                }
            }
            
            logger.info("Database operation tracked",
                       operation_type=operation_type,
                       collection=collection,
                       duration=duration,
                       success=success,
                       record_count=record_count)
            
            # Store metrics
            if self.firestore_client:
                await self._store_metrics(metrics_data)
            
        except Exception as e:
            logger.error("Failed to track database operation", error=str(e))
    
    async def track_pubsub_message(self, message_type: str, topic: str, 
                                 processing_time: Optional[float] = None, 
                                 success: bool = True):
        """Track Pub/Sub message processing"""
        try:
            self.pubsub_message_count += 1
            
            metrics_data = {
                "metric_type": "pubsub_message",
                "message_type": message_type,  # 'publish', 'receive', 'ack'
                "topic": topic,
                "processing_time_seconds": processing_time,
                "success": success,
                "timestamp": datetime.utcnow().isoformat(),
                "session_metrics": {
                    "total_pubsub_messages": self.pubsub_message_count
                }
            }
            
            logger.info("Pub/Sub message tracked",
                       message_type=message_type,
                       topic=topic,
                       processing_time=processing_time,
                       success=success)
            
            # Store metrics
            if self.firestore_client:
                await self._store_metrics(metrics_data)
            
        except Exception as e:
            logger.error("Failed to track Pub/Sub message", error=str(e))
    
    async def get_performance_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get comprehensive performance summary"""
        try:
            summary = {
                "timestamp": datetime.utcnow().isoformat(),
                "period_hours": hours,
                "session_metrics": {
                    "commands_processed": self.command_count,
                    "total_errors": self.error_count,
                    "error_rate_percent": (self.error_count / self.command_count) * 100 if self.command_count > 0 else 0,
                    "average_processing_time": self.total_processing_time / self.command_count if self.command_count > 0 else 0,
                    "database_operations": self.database_operation_count,
                    "pubsub_messages": self.pubsub_message_count
                },
                "active_operations": len(self.active_operations),
                "cache_size": len(self.metrics_cache)
            }
            
            # Get historical data from Firestore if available
            if self.firestore_client:
                historical_metrics = await self._get_historical_metrics(hours)
                summary["historical_data"] = historical_metrics
            
            return summary
            
        except Exception as e:
            logger.error("Failed to get performance summary", error=str(e))
            return {"error": str(e), "timestamp": datetime.utcnow().isoformat()}
    
    async def get_health_metrics(self) -> Dict[str, Any]:
        """Get current system health metrics"""
        try:
            # Calculate health scores
            error_rate = (self.error_count / self.command_count) * 100 if self.command_count > 0 else 0
            avg_response_time = self.total_processing_time / self.command_count if self.command_count > 0 else 0
            
            # Determine health status
            health_status = "healthy"
            if error_rate > 10:  # More than 10% error rate
                health_status = "unhealthy"
            elif error_rate > 5 or avg_response_time > 5.0:  # 5% error rate or >5s avg response
                health_status = "degraded"
            
            health_metrics = {
                "overall_status": health_status,
                "timestamp": datetime.utcnow().isoformat(),
                "metrics": {
                    "error_rate_percent": error_rate,
                    "average_response_time_seconds": avg_response_time,
                    "active_operations": len(self.active_operations),
                    "total_commands_processed": self.command_count,
                    "database_operations": self.database_operation_count,
                    "pubsub_messages": self.pubsub_message_count
                },
                "thresholds": {
                    "error_rate_warning": 5.0,
                    "error_rate_critical": 10.0,
                    "response_time_warning": 3.0,
                    "response_time_critical": 5.0
                }
            }
            
            logger.info("Health metrics calculated", 
                       health_status=health_status,
                       error_rate=error_rate,
                       avg_response_time=avg_response_time)
            
            return health_metrics
            
        except Exception as e:
            logger.error("Failed to get health metrics", error=str(e))
            return {
                "overall_status": "error",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
    
    async def _record_operation_success(self, operation_id: str, operation_name: str, 
                                      duration: float, metadata: Optional[Dict[str, Any]]):
        """Record successful operation completion"""
        logger.info("Operation completed successfully",
                   operation_id=operation_id,
                   operation_name=operation_name,
                   duration_seconds=duration,
                   metadata=metadata or {})
        
        # Store detailed operation metrics
        if self.firestore_client:
            operation_metrics = {
                "metric_type": "operation_completion",
                "operation_id": operation_id,
                "operation_name": operation_name,
                "duration_seconds": duration,
                "status": "success",
                "timestamp": datetime.utcnow().isoformat(),
                "metadata": metadata or {}
            }
            await self._store_metrics(operation_metrics)
    
    async def _record_operation_error(self, operation_id: str, operation_name: str, 
                                    duration: float, error_message: str, 
                                    metadata: Optional[Dict[str, Any]]):
        """Record operation failure"""
        logger.error("Operation failed",
                    operation_id=operation_id,
                    operation_name=operation_name,
                    duration_seconds=duration,
                    error=error_message,
                    metadata=metadata or {})
        
        # Store error metrics
        if self.firestore_client:
            error_metrics = {
                "metric_type": "operation_error",
                "operation_id": operation_id,
                "operation_name": operation_name,
                "duration_seconds": duration,
                "status": "error",
                "error_message": error_message,
                "timestamp": datetime.utcnow().isoformat(),
                "metadata": metadata or {}
            }
            await self._store_metrics(error_metrics)
    
    async def _store_metrics(self, metrics_data: Dict[str, Any]):
        """Store metrics in Firestore"""
        try:
            if self.firestore_client:
                # Add to metrics collection
                doc_ref = self.firestore_client.db.collection('metrics').add(metrics_data)[1]
                logger.debug("Metrics stored", doc_id=doc_ref.id, metric_type=metrics_data.get('metric_type'))
        except Exception as e:
            logger.error("Failed to store metrics", error=str(e))
    
    async def _get_historical_metrics(self, hours: int) -> Dict[str, Any]:
        """Get historical metrics from Firestore"""
        try:
            if not self.firestore_client:
                return {"error": "Firestore not available"}
            
            # Calculate time range
            end_time = datetime.utcnow()
            start_time = end_time - timedelta(hours=hours)
            
            # Query metrics collection
            # Note: This is a simplified query - in production, you'd want to optimize with proper indexing
            query = (self.firestore_client.db.collection('metrics')
                    .where('timestamp', '>=', start_time.isoformat())
                    .where('timestamp', '<=', end_time.isoformat())
                    .order_by('timestamp')
                    .limit(1000))  # Limit to prevent large queries
            
            metrics = []
            for doc in query.stream():
                data = doc.to_dict()
                data['id'] = doc.id
                metrics.append(data)
            
            # Aggregate metrics
            command_metrics = [m for m in metrics if m.get('metric_type') == 'command_processing']
            db_metrics = [m for m in metrics if m.get('metric_type') == 'database_operation']
            pubsub_metrics = [m for m in metrics if m.get('metric_type') == 'pubsub_message']
            
            return {
                "total_metrics": len(metrics),
                "command_processing_count": len(command_metrics),
                "database_operations_count": len(db_metrics),
                "pubsub_messages_count": len(pubsub_metrics),
                "time_range": {
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat(),
                    "hours": hours
                }
            }
            
        except Exception as e:
            logger.error("Failed to get historical metrics", error=str(e))
            return {"error": str(e)}
    
    def reset_session_metrics(self):
        """Reset session-level metrics counters"""
        self.command_count = 0
        self.error_count = 0
        self.total_processing_time = 0.0
        self.database_operation_count = 0
        self.pubsub_message_count = 0
        self.metrics_cache.clear()
        
        logger.info("Session metrics reset")
    
    def get_session_metrics(self) -> Dict[str, Any]:
        """Get current session metrics synchronously for testing"""
        return {
            "command_count": self.command_count,
            "error_count": self.error_count,
            "total_processing_time": self.total_processing_time,
            "average_response_time": (
                self.total_processing_time / self.command_count 
                if self.command_count > 0 else 0.0
            ),
            "database_operation_count": self.database_operation_count,
            "pubsub_message_count": self.pubsub_message_count,
            "active_operations": len(self.active_operations)
        }