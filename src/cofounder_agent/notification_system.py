"""
Advanced Notification System for AI Co-Founder
Provides intelligent alerts, business notifications, and proactive insights
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum
import json

# Set up logging
logger = logging.getLogger(__name__)

class NotificationType(Enum):
    """Types of notifications the system can generate"""
    TASK_UPDATE = "task_update"
    BUSINESS_ALERT = "business_alert"
    PERFORMANCE_MILESTONE = "performance_milestone"
    SYSTEM_STATUS = "system_status"
    STRATEGIC_OPPORTUNITY = "strategic_opportunity"
    COST_OPTIMIZATION = "cost_optimization"
    CONTENT_PERFORMANCE = "content_performance"
    REVENUE_UPDATE = "revenue_update"
    RISK_ALERT = "risk_alert"
    GOAL_PROGRESS = "goal_progress"

class Priority(Enum):
    """Notification priority levels"""
    CRITICAL = "critical"
    HIGH = "high" 
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

@dataclass
class Notification:
    """Structured notification object"""
    id: str
    type: NotificationType
    priority: Priority
    title: str
    message: str
    timestamp: datetime
    data: Optional[Dict[str, Any]] = None
    read: bool = False
    actions: Optional[List[Dict[str, Any]]] = None
    expires_at: Optional[datetime] = None

class SmartNotificationSystem:
    """AI-powered notification system with intelligent prioritization"""
    
    def __init__(self):
        self.notifications: List[Notification] = []
        self.subscribers = []
        self.notification_rules = self._initialize_rules()
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    def _initialize_rules(self) -> Dict[str, Any]:
        """Initialize notification rules and thresholds"""
        return {
            "task_completion_rate": {
                "threshold": 0.8,
                "check_frequency": 3600,  # Check every hour
                "priority": Priority.MEDIUM
            },
            "revenue_change": {
                "threshold": 0.1,  # 10% change
                "check_frequency": 86400,  # Check daily
                "priority": Priority.HIGH
            },
            "cost_spike": {
                "threshold": 0.2,  # 20% increase
                "check_frequency": 1800,  # Check every 30 minutes
                "priority": Priority.CRITICAL
            },
            "content_performance": {
                "threshold": 0.15,  # 15% change in engagement
                "check_frequency": 7200,  # Check every 2 hours
                "priority": Priority.MEDIUM
            },
            "system_health": {
                "threshold": 0.95,  # 95% uptime
                "check_frequency": 300,   # Check every 5 minutes
                "priority": Priority.HIGH
            }
        }
    
    async def subscribe(self, callback):
        """Subscribe to notifications"""
        self.subscribers.append(callback)
        self.logger.info("New subscriber added to notification system")
    
    async def create_notification(
        self,
        notification_type: NotificationType,
        priority: Priority,
        title: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
        actions: Optional[List[Dict[str, Any]]] = None,
        expires_in_hours: Optional[int] = None
    ) -> Notification:
        """Create and distribute a new notification"""
        
        notification_id = f"notif-{datetime.now().strftime('%Y%m%d%H%M%S')}-{len(self.notifications)}"
        expires_at = datetime.now() + timedelta(hours=expires_in_hours) if expires_in_hours else None
        
        notification = Notification(
            id=notification_id,
            type=notification_type,
            priority=priority,
            title=title,
            message=message,
            timestamp=datetime.now(),
            data=data,
            actions=actions,
            expires_at=expires_at
        )
        
        self.notifications.append(notification)
        
        # Notify all subscribers
        await self._notify_subscribers(notification)
        
        self.logger.info(f"Created {priority.value} notification: {title}")
        return notification
    
    async def _notify_subscribers(self, notification: Notification):
        """Notify all subscribers about new notification"""
        for callback in self.subscribers:
            try:
                await callback(notification)
            except Exception as e:
                self.logger.error(f"Error notifying subscriber: {e}")
    
    async def check_business_metrics(self, metrics: Dict[str, Any]):
        """Check business metrics and generate alerts if thresholds are exceeded"""
        
        # Task completion rate check
        if "task_completion_rate" in metrics:
            rate = metrics["task_completion_rate"]
            threshold = self.notification_rules["task_completion_rate"]["threshold"]
            
            if rate < threshold:
                await self.create_notification(
                    NotificationType.BUSINESS_ALERT,
                    Priority.MEDIUM,
                    "Task Completion Rate Below Target",
                    f"Current completion rate is {rate:.1%}, below target of {threshold:.1%}. "
                    f"Consider reviewing task priorities and resource allocation.",
                    data={"current_rate": rate, "target_rate": threshold},
                    actions=[
                        {"type": "view_tasks", "label": "View Tasks", "url": "/tasks"},
                        {"type": "analyze_bottlenecks", "label": "Analyze Bottlenecks"}
                    ]
                )
        
        # Revenue change check
        if "revenue_change" in metrics:
            change = metrics["revenue_change"]
            threshold = self.notification_rules["revenue_change"]["threshold"]
            
            if abs(change) >= threshold:
                priority = Priority.HIGH if change < 0 else Priority.MEDIUM
                direction = "decreased" if change < 0 else "increased"
                
                await self.create_notification(
                    NotificationType.REVENUE_UPDATE,
                    priority,
                    f"Significant Revenue Change Detected",
                    f"Revenue has {direction} by {abs(change):.1%} compared to previous period. "
                    f"{'Immediate action may be required.' if change < 0 else 'Great progress!'}",
                    data={"revenue_change": change, "threshold": threshold},
                    actions=[
                        {"type": "view_financials", "label": "View Financials", "url": "/financials"},
                        {"type": "generate_report", "label": "Generate Report"}
                    ]
                )
        
        # Cost spike check
        if "cost_increase" in metrics:
            increase = metrics["cost_increase"]
            threshold = self.notification_rules["cost_spike"]["threshold"]
            
            if increase >= threshold:
                await self.create_notification(
                    NotificationType.COST_OPTIMIZATION,
                    Priority.CRITICAL,
                    "Cost Spike Detected",
                    f"Costs have increased by {increase:.1%}, exceeding the {threshold:.1%} threshold. "
                    f"Immediate cost analysis recommended.",
                    data={"cost_increase": increase, "threshold": threshold},
                    actions=[
                        {"type": "cost_analysis", "label": "Analyze Costs"},
                        {"type": "optimize_costs", "label": "Optimize Costs"},
                        {"type": "emergency_pause", "label": "Emergency Pause", "style": "danger"}
                    ],
                    expires_in_hours=24
                )
    
    async def check_content_performance(self, performance_data: Dict[str, Any]):
        """Check content performance and generate optimization suggestions"""
        
        if "engagement_change" in performance_data:
            change = performance_data["engagement_change"]
            threshold = self.notification_rules["content_performance"]["threshold"]
            
            if abs(change) >= threshold:
                if change > 0:
                    await self.create_notification(
                        NotificationType.CONTENT_PERFORMANCE,
                        Priority.MEDIUM,
                        "Content Performance Improvement",
                        f"Content engagement has improved by {change:.1%}. "
                        f"Consider scaling successful content strategies.",
                        data=performance_data,
                        actions=[
                            {"type": "view_top_content", "label": "View Top Content"},
                            {"type": "scale_strategy", "label": "Scale Strategy"}
                        ]
                    )
                else:
                    await self.create_notification(
                        NotificationType.CONTENT_PERFORMANCE,
                        Priority.HIGH,
                        "Content Performance Decline",
                        f"Content engagement has decreased by {abs(change):.1%}. "
                        f"Content strategy review recommended.",
                        data=performance_data,
                        actions=[
                            {"type": "analyze_content", "label": "Analyze Content"},
                            {"type": "revise_strategy", "label": "Revise Strategy"}
                        ]
                    )
    
    async def check_system_health(self, health_data: Dict[str, Any]):
        """Monitor system health and generate alerts"""
        
        if "uptime" in health_data:
            uptime = health_data["uptime"]
            threshold = self.notification_rules["system_health"]["threshold"]
            
            if uptime < threshold:
                await self.create_notification(
                    NotificationType.SYSTEM_STATUS,
                    Priority.CRITICAL,
                    "System Health Alert",
                    f"System uptime is {uptime:.1%}, below target of {threshold:.1%}. "
                    f"System maintenance may be required.",
                    data=health_data,
                    actions=[
                        {"type": "system_diagnostics", "label": "Run Diagnostics"},
                        {"type": "restart_services", "label": "Restart Services"},
                        {"type": "contact_support", "label": "Contact Support"}
                    ]
                )
    
    async def generate_strategic_opportunity_alert(self, opportunity: Dict[str, Any]):
        """Generate alerts for strategic business opportunities"""
        
        await self.create_notification(
            NotificationType.STRATEGIC_OPPORTUNITY,
            Priority.HIGH,
            f"Strategic Opportunity: {opportunity.get('title', 'New Opportunity')}",
            opportunity.get("description", "A new strategic opportunity has been identified."),
            data=opportunity,
            actions=[
                {"type": "view_details", "label": "View Details"},
                {"type": "create_plan", "label": "Create Action Plan"},
                {"type": "schedule_review", "label": "Schedule Review"}
            ],
            expires_in_hours=72
        )
    
    async def generate_goal_progress_update(self, goal_data: Dict[str, Any]):
        """Generate progress updates for business goals"""
        
        progress = goal_data.get("progress", 0)
        goal_name = goal_data.get("name", "Business Goal")
        
        if progress >= 0.9:  # 90% progress
            priority = Priority.MEDIUM
            message = f"Excellent progress! {goal_name} is {progress:.1%} complete."
        elif progress >= 0.75:  # 75% progress
            priority = Priority.LOW
            message = f"Good progress on {goal_name}: {progress:.1%} complete."
        elif progress < 0.5:  # Less than 50% progress
            priority = Priority.HIGH
            message = f"Goal {goal_name} may need attention. Only {progress:.1%} complete."
        else:
            return  # No notification needed for moderate progress
        
        await self.create_notification(
            NotificationType.GOAL_PROGRESS,
            priority,
            f"Goal Progress Update: {goal_name}",
            message,
            data=goal_data,
            actions=[
                {"type": "view_goal", "label": "View Goal Details"},
                {"type": "update_strategy", "label": "Update Strategy"}
            ]
        )
    
    def get_notifications(
        self, 
        limit: int = 50, 
        unread_only: bool = False,
        priority_filter: Optional[Priority] = None
    ) -> List[Notification]:
        """Get notifications with filtering options"""
        
        notifications = self.notifications.copy()
        
        # Filter out expired notifications
        now = datetime.now()
        notifications = [n for n in notifications if not n.expires_at or n.expires_at > now]
        
        # Apply filters
        if unread_only:
            notifications = [n for n in notifications if not n.read]
        
        if priority_filter:
            notifications = [n for n in notifications if n.priority == priority_filter]
        
        # Sort by priority and timestamp
        priority_order = {
            Priority.CRITICAL: 0,
            Priority.HIGH: 1, 
            Priority.MEDIUM: 2,
            Priority.LOW: 3,
            Priority.INFO: 4
        }
        
        notifications.sort(key=lambda n: (priority_order[n.priority], -n.timestamp.timestamp()))
        
        return notifications[:limit]
    
    async def mark_as_read(self, notification_id: str) -> bool:
        """Mark a notification as read"""
        for notification in self.notifications:
            if notification.id == notification_id:
                notification.read = True
                self.logger.info(f"Marked notification {notification_id} as read")
                return True
        return False
    
    def get_notification_stats(self) -> Dict[str, Any]:
        """Get notification system statistics"""
        now = datetime.now()
        active_notifications = [n for n in self.notifications if not n.expires_at or n.expires_at > now]
        
        return {
            "total_notifications": len(self.notifications),
            "active_notifications": len(active_notifications),
            "unread_notifications": len([n for n in active_notifications if not n.read]),
            "critical_alerts": len([n for n in active_notifications if n.priority == Priority.CRITICAL]),
            "high_priority": len([n for n in active_notifications if n.priority == Priority.HIGH]),
            "last_notification": active_notifications[0].timestamp.isoformat() if active_notifications else None,
            "subscribers": len(self.subscribers)
        }