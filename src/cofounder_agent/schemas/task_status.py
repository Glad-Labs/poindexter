"""
Task Status and State Management

Centralized definitions for task statuses and state transitions.
Ensures consistency across all endpoints and prevents invalid status values.
"""

from enum import Enum
from typing import List, Set


class TaskStatus(str, Enum):
    """
    All valid task status values across the system.
    
    Status transitions:
    - pending → generating → completed/failed
    - pending → generating → awaiting_approval → approved/rejected
    - approved → published
    """
    
    # Initial states
    PENDING = "pending"           # Task created, queued for processing
    QUEUED = "queued"              # Task in queue (alias for pending)
    
    # Processing states
    GENERATING = "generating"      # Task actively being processed
    RUNNING = "running"            # Alias for generating (for backward compatibility)
    IN_PROGRESS = "in_progress"    # Task is being executed
    
    # Intermediate states (for approval workflows)
    AWAITING_APPROVAL = "awaiting_approval"  # Content ready, waiting human review
    
    # Terminal states - Approved/Rejected
    APPROVED = "approved"          # Human approved the content
    REJECTED = "rejected"          # Human rejected the content
    
    # Terminal states - Completed
    COMPLETED = "completed"        # Task finished successfully
    SUCCESS = "success"            # Alias for completed
    
    # Terminal states - Failed
    FAILED = "failed"              # Task failed with error
    ERROR = "error"                # Alias for failed
    
    # Published states
    PUBLISHED = "published"        # Content published to CMS/external platform
    
    # Special states
    PAUSED = "paused"              # Task paused (can be resumed)
    CANCELLED = "cancelled"        # Task cancelled by user
    SKIPPED = "skipped"            # Task skipped
    
    @classmethod
    def all_values(cls) -> List[str]:
        """Get all valid status values"""
        return [status.value for status in cls]
    
    @classmethod
    def validate(cls, status: str) -> bool:
        """Check if a status value is valid"""
        return status in cls.all_values()
    
    @classmethod
    def get_terminal_states(cls) -> Set[str]:
        """Get all terminal states (task can't change from these)"""
        return {
            cls.COMPLETED,
            cls.FAILED,
            cls.PUBLISHED,
            cls.REJECTED,
            cls.CANCELLED,
        }
    
    @classmethod
    def get_active_states(cls) -> Set[str]:
        """Get all active states (task is still processing)"""
        return {
            cls.PENDING,
            cls.GENERATING,
            cls.QUEUED,
            cls.RUNNING,
            cls.IN_PROGRESS,
            cls.AWAITING_APPROVAL,
            cls.PAUSED,
        }
    
    @classmethod
    def can_transition(cls, from_status: str, to_status: str) -> bool:
        """
        Check if a transition between two statuses is allowed.
        
        Rules:
        - Cannot transition FROM terminal states
        - Certain transitions are logical sequences
        """
        if from_status not in cls.all_values() or to_status not in cls.all_values():
            return False
        
        # Can't transition FROM terminal states
        if from_status in cls.get_terminal_states():
            return False
        
        # Allow transitions within active states
        if from_status in cls.get_active_states() and to_status in cls.get_active_states():
            return True
        
        # Allow transitions to terminal states
        if to_status in cls.get_terminal_states():
            return True
        
        return False


class ApprovalStatus(str, Enum):
    """Approval workflow status"""
    PENDING = "pending"            # Awaiting human review
    APPROVED = "approved"          # Human approved
    REJECTED = "rejected"          # Human rejected
    FLAGGED = "flagged"            # Needs further review


class PublishStatus(str, Enum):
    """Publication status"""
    DRAFT = "draft"                # Not published
    STAGED = "staged"              # Ready to publish
    PUBLISHED = "published"        # Live
    ARCHIVED = "archived"          # Archived
    UNPUBLISHED = "unpublished"    # Was published, now removed


class TaskPriority(str, Enum):
    """Task execution priority"""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TaskType(str, Enum):
    """Types of content tasks supported"""
    BLOG_POST = "blog_post"
    SOCIAL_MEDIA = "social_media"
    EMAIL = "email"
    NEWSLETTER = "newsletter"
    ARTICLE = "article"
    DOCUMENT = "document"
    PRODUCT_DESCRIPTION = "product_description"
    CONTENT_OUTLINE = "content_outline"
