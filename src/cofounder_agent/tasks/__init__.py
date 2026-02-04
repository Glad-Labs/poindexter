"""Task system for modular, composable workflow execution.

Complete task inventory (16 total tasks + 5 base classes):
- Content: Research, Creative, QA, ImageSelection, Publish (5)
- Social: SocialResearch, SocialCreative, SocialImageFormat, SocialPublish (4)
- Business: FinancialAnalysis, MarketAnalysis, PerformanceReview (3)
- Automation: EmailGenerate, EmailSend (2)
- Utility: Validate, Transform, Notification, Cache, Metrics, Log (6)
"""

# Automation Tasks (2 tasks)
from src.cofounder_agent.tasks.automation_tasks import (
    EmailGenerateTask,
    EmailSendTask,
)
from src.cofounder_agent.tasks.base import ExecutionContext, PureTask, Task, TaskResult, TaskStatus

# Business Intelligence Tasks (3 tasks)
from src.cofounder_agent.tasks.business_tasks import (
    FinancialAnalysisTask,
    MarketAnalysisTask,
    PerformanceReviewTask,
)

# Content Generation Tasks (5 tasks)
from src.cofounder_agent.tasks.content_tasks import (
    CreativeTask,
    ImageSelectionTask,
    PublishTask,
    QATask,
    ResearchTask,
)
from src.cofounder_agent.tasks.registry import TaskRegistry

# Social Media Tasks (4 tasks)
from src.cofounder_agent.tasks.social_tasks import (
    SocialCreativeTask,
    SocialImageFormatTask,
    SocialPublishTask,
    SocialResearchTask,
)

# Utility Tasks (6 tasks)
from src.cofounder_agent.tasks.utility_tasks import (
    CacheTask,
    LogTask,
    MetricsTask,
    NotificationTask,
    TransformTask,
    ValidateTask,
)

__all__ = [
    # Base classes (5 items)
    "Task",
    "PureTask",
    "TaskStatus",
    "TaskResult",
    "ExecutionContext",
    "TaskRegistry",
    # Content tasks (5)
    "ResearchTask",
    "CreativeTask",
    "QATask",
    "ImageSelectionTask",
    "PublishTask",
    # Social tasks (4)
    "SocialResearchTask",
    "SocialCreativeTask",
    "SocialImageFormatTask",
    "SocialPublishTask",
    # Business tasks (3)
    "FinancialAnalysisTask",
    "MarketAnalysisTask",
    "PerformanceReviewTask",
    # Automation tasks (2)
    "EmailGenerateTask",
    "EmailSendTask",
    # Utility tasks (6)
    "ValidateTask",
    "TransformTask",
    "NotificationTask",
    "CacheTask",
    "MetricsTask",
    "LogTask",
]
