"""
Settings Audit Logging Middleware

Tracks all changes to settings for compliance, debugging, and rollback purposes.
Creates immutable audit trail of who changed what and when.

Audit Log Entry Fields:
- setting_id: ID of the setting that was changed
- changed_by_id: ID of the user who made the change
- changed_by_email: Email of the user for convenience
- action: The action performed (create, update, delete)
- change_description: Human-readable description of what changed
- old_value: Previous value (encrypted if setting is encrypted)
- new_value: New value (encrypted if setting is encrypted)
- timestamp: When the change occurred
- old_data_type: Previous data type (if changed)
- new_data_type: New data type (if changed)
- ip_address: IP address of the requesting client
- user_agent: User agent string from request headers
"""

from typing import Optional, Dict, Any
from datetime import datetime, timezone
import json
import logging

# Type aliases for common return types
SettingAuditLog = Dict[str, Any]  # Placeholder until models are imported

# Note: These imports will be resolved when dependencies are installed
# from sqlalchemy.orm import Session
# from models import Setting, SettingAuditLog, User
# from services.encryption import EncryptionService
# from middleware.jwt import JWTTokenVerifier

# Database imports for actual implementation
try:
    from database import SessionLocal
    from models import Log
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False


logger = logging.getLogger(__name__)


class SettingsAuditLogger:
    """Handles audit logging for all settings operations"""

    # Actions that are logged
    ACTION_CREATE = "CREATE"
    ACTION_UPDATE = "UPDATE"
    ACTION_DELETE = "DELETE"
    ACTION_EXPORT = "EXPORT"
    ACTION_ROLLBACK = "ROLLBACK"
    ACTION_BULK_UPDATE = "BULK_UPDATE"

    def __init__(
        self,
        # db: Session,
        # encryption_service: EncryptionService,
        enable_logging: bool = True,
    ):
        """
        Initialize audit logger.

        Args:
            db: Database session
            encryption_service: Encryption service for sensitive values
            enable_logging: Whether to enable logging
        """
        # self.db = db
        # self.encryption_service = encryption_service
        self.enable_logging = enable_logging

    @staticmethod
    def log_create_setting(
        # db: Session,
        user_id: int,
        user_email: str,
        setting,  # Setting
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Optional[SettingAuditLog]:
        """
        Log creation of a new setting.

        Args:
            db: Database session
            user_id: ID of user who created the setting
            user_email: Email of user for audit trail
            setting: The Setting object that was created
            ip_address: IP address of request
            user_agent: User agent string from request

        Returns:
            Created SettingAuditLog entry
        """
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            setting_key = getattr(setting, 'key', 'unknown')
            setting_category = getattr(setting, 'category', 'default')
            setting_value = getattr(setting, 'value', None)
            is_encrypted = getattr(setting, 'is_encrypted', False)

            change_description = f"Created setting '{setting_key}' in category '{setting_category}'"
            metadata = {
                "action": "CREATE",
                "setting_id": getattr(setting, 'id', None),
                "setting_key": setting_key,
                "setting_category": setting_category,
                "changed_by_id": user_id,
                "changed_by_email": user_email,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "is_encrypted": is_encrypted
            }

            message = f"[{timestamp}] AUDIT: {change_description} by {user_email}"

            if DB_AVAILABLE:
                try:
                    db = SessionLocal()
                    audit_log = Log(
                        level="INFO",
                        message=message,
                        timestamp=datetime.now(timezone.utc),
                        log_metadata=metadata
                    )
                    db.add(audit_log)
                    db.commit()
                    db.close()
                    logger.info(f"Setting created and audited: {setting_key} by {user_email}")
                except Exception as e:
                    logger.error(f"Failed to log setting creation to database: {str(e)}")
                    print(f"[{timestamp}] {message}")
            else:
                print(f"[{timestamp}] {message}")

            return metadata
        except Exception as e:
            logger.error(f"Error in log_create_setting: {str(e)}")
            return None

    @staticmethod
    def log_update_setting(
        # db: Session,
        user_id: int,
        user_email: str,
        setting,  # Setting
        changes: Dict[str, Any],
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Optional[SettingAuditLog]:
        """
        Log update to an existing setting.

        Args:
            db: Database session
            user_id: ID of user who updated the setting
            user_email: Email of user for audit trail
            setting: The Setting object that was updated
            changes: Dictionary of changes made (e.g., {'value': {'old': '10', 'new': '20'}})
            ip_address: IP address of request
            user_agent: User agent string from request

        Returns:
            Created SettingAuditLog entry
        """
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            setting_key = getattr(setting, 'key', 'unknown')
            is_encrypted = getattr(setting, 'is_encrypted', False)

            # Build change description from changes dict
            change_parts = []
            for field, change_data in changes.items():
                if isinstance(change_data, dict) and 'old' in change_data and 'new' in change_data:
                    old_val = change_data['old']
                    new_val = change_data['new']
                    change_parts.append(f"{field} from '{old_val}' to '{new_val}'")
                else:
                    change_parts.append(f"{field} updated")

            change_description = f"Updated {setting_key}: {', '.join(change_parts)}"
            metadata = {
                "action": "UPDATE",
                "setting_id": getattr(setting, 'id', None),
                "setting_key": setting_key,
                "changed_by_id": user_id,
                "changed_by_email": user_email,
                "changes": changes,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "is_encrypted": is_encrypted
            }

            message = f"[{timestamp}] AUDIT: {change_description} by {user_email}"

            if DB_AVAILABLE:
                try:
                    db = SessionLocal()
                    audit_log = Log(
                        level="INFO",
                        message=message,
                        timestamp=datetime.now(timezone.utc),
                        log_metadata=metadata
                    )
                    db.add(audit_log)
                    db.commit()
                    db.close()
                    logger.info(f"Setting updated and audited: {setting_key} by {user_email}")
                except Exception as e:
                    logger.error(f"Failed to log setting update to database: {str(e)}")
                    print(f"[{timestamp}] {message}")
            else:
                print(f"[{timestamp}] {message}")

            return metadata
        except Exception as e:
            logger.error(f"Error in log_update_setting: {str(e)}")
            return None

    @staticmethod
    def log_delete_setting(
        # db: Session,
        user_id: int,
        user_email: str,
        setting,  # Setting
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Optional[SettingAuditLog]:
        """
        Log deletion of a setting.

        Args:
            db: Database session
            user_id: ID of user who deleted the setting
            user_email: Email of user for audit trail
            setting: The Setting object that was deleted
            ip_address: IP address of request
            user_agent: User agent string from request

        Returns:
            Created SettingAuditLog entry
        """
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            setting_key = getattr(setting, 'key', 'unknown')
            setting_category = getattr(setting, 'category', 'default')
            is_encrypted = getattr(setting, 'is_encrypted', False)

            change_description = f"Deleted setting '{setting_key}' from category '{setting_category}'"
            metadata = {
                "action": "DELETE",
                "setting_id": getattr(setting, 'id', None),
                "setting_key": setting_key,
                "setting_category": setting_category,
                "changed_by_id": user_id,
                "changed_by_email": user_email,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "is_encrypted": is_encrypted
            }

            message = f"[{timestamp}] AUDIT: {change_description} by {user_email}"

            if DB_AVAILABLE:
                try:
                    db = SessionLocal()
                    audit_log = Log(
                        level="WARNING",
                        message=message,
                        timestamp=datetime.now(timezone.utc),
                        log_metadata=metadata
                    )
                    db.add(audit_log)
                    db.commit()
                    db.close()
                    logger.warning(f"Setting deleted and audited: {setting_key} by {user_email}")
                except Exception as e:
                    logger.error(f"Failed to log setting deletion to database: {str(e)}")
                    print(f"[{timestamp}] {message}")
            else:
                print(f"[{timestamp}] {message}")

            return metadata
        except Exception as e:
            logger.error(f"Error in log_delete_setting: {str(e)}")
            return None

    @staticmethod
    def log_bulk_update(
        # db: Session,
        user_id: int,
        user_email: str,
        updates: list,  # List of (setting, changes) tuples
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> list:  # List[SettingAuditLog]
        """
        Log bulk update of multiple settings.

        Args:
            db: Database session
            user_id: ID of user who performed bulk update
            user_email: Email of user for audit trail
            updates: List of (setting, changes) tuples
            ip_address: IP address of request
            user_agent: User agent string from request

        Returns:
            List of created SettingAuditLog entries
        """
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            audit_logs = []

            if DB_AVAILABLE:
                try:
                    db = SessionLocal()
                    for setting, changes in updates:
                        setting_key = getattr(setting, 'key', 'unknown')
                        
                        # Build change description
                        change_parts = []
                        for field, change_data in changes.items():
                            if isinstance(change_data, dict) and 'old' in change_data and 'new' in change_data:
                                change_parts.append(f"{field}: {change_data['old']} → {change_data['new']}")
                            else:
                                change_parts.append(f"{field} updated")

                        change_description = f"[BULK] {setting_key}: {', '.join(change_parts)}"
                        metadata = {
                            "action": "BULK_UPDATE",
                            "setting_id": getattr(setting, 'id', None),
                            "setting_key": setting_key,
                            "changed_by_id": user_id,
                            "changed_by_email": user_email,
                            "changes": changes,
                            "ip_address": ip_address,
                            "user_agent": user_agent,
                            "bulk_operation": True
                        }

                        audit_log = Log(
                            level="INFO",
                            message=f"[{timestamp}] AUDIT: {change_description}",
                            timestamp=datetime.now(timezone.utc),
                            log_metadata=metadata
                        )
                        db.add(audit_log)
                        audit_logs.append(metadata)

                    db.commit()
                    db.close()
                    logger.info(f"Bulk update of {len(updates)} settings audited by {user_email}")
                except Exception as e:
                    logger.error(f"Failed to log bulk update to database: {str(e)}")
                    print(f"[{timestamp}] Bulk update of {len(updates)} settings by {user_email}")
            else:
                print(f"[{timestamp}] Bulk update of {len(updates)} settings by {user_email}")
                for setting, _ in updates:
                    audit_logs.append({
                        "setting_key": getattr(setting, 'key', 'unknown'),
                        "action": "BULK_UPDATE"
                    })

            return audit_logs
        except Exception as e:
            logger.error(f"Error in log_bulk_update: {str(e)}")
            return []

    @staticmethod
    def log_rollback(
        # db: Session,
        user_id: int,
        user_email: str,
        setting,  # Setting
        previous_history_id: int,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Optional[SettingAuditLog]:
        """
        Log rollback of setting to previous value.

        Args:
            db: Database session
            user_id: ID of user who performed rollback
            user_email: Email of user for audit trail
            setting: The Setting object that was rolled back
            previous_history_id: ID of the history entry being rolled back to
            ip_address: IP address of request
            user_agent: User agent string from request

        Returns:
            Created SettingAuditLog entry
        """
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            setting_key = getattr(setting, 'key', 'unknown')
            is_encrypted = getattr(setting, 'is_encrypted', False)

            change_description = f"Rolled back setting '{setting_key}' to previous version (history ID: {previous_history_id})"
            metadata = {
                "action": "ROLLBACK",
                "setting_id": getattr(setting, 'id', None),
                "setting_key": setting_key,
                "changed_by_id": user_id,
                "changed_by_email": user_email,
                "previous_history_id": previous_history_id,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "is_encrypted": is_encrypted
            }

            message = f"[{timestamp}] AUDIT: {change_description} by {user_email}"

            if DB_AVAILABLE:
                try:
                    db = SessionLocal()
                    audit_log = Log(
                        level="WARNING",
                        message=message,
                        timestamp=datetime.now(timezone.utc),
                        log_metadata=metadata
                    )
                    db.add(audit_log)
                    db.commit()
                    db.close()
                    logger.warning(f"Setting rolled back and audited: {setting_key} to history ID {previous_history_id} by {user_email}")
                except Exception as e:
                    logger.error(f"Failed to log setting rollback to database: {str(e)}")
                    print(f"[{timestamp}] {message}")
            else:
                print(f"[{timestamp}] {message}")

            return metadata
        except Exception as e:
            logger.error(f"Error in log_rollback: {str(e)}")
            return None

    @staticmethod
    def log_export(
        # db: Session,
        user_id: int,
        user_email: str,
        setting_count: int,
        include_secrets: bool,
        format: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Optional[SettingAuditLog]:
        """
        Log export of settings (for compliance tracking).

        Args:
            db: Database session
            user_id: ID of user who exported settings
            user_email: Email of user for audit trail
            setting_count: Number of settings exported
            include_secrets: Whether secrets were included in export
            format: Export format (json, yaml, csv)
            ip_address: IP address of request
            user_agent: User agent string from request

        Returns:
            Created SettingAuditLog entry
        """
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            secrets_indicator = "WITH SECRETS" if include_secrets else "WITHOUT SECRETS"
            change_description = f"Exported {setting_count} settings as {format.upper()} {secrets_indicator}"
            
            metadata = {
                "action": "EXPORT",
                "setting_id": None,  # Not tied to single setting
                "setting_count": setting_count,
                "format": format,
                "include_secrets": include_secrets,
                "changed_by_id": user_id,
                "changed_by_email": user_email,
                "ip_address": ip_address,
                "user_agent": user_agent
            }

            message = f"[{timestamp}] AUDIT: {change_description} by {user_email}"

            if DB_AVAILABLE:
                try:
                    db = SessionLocal()
                    audit_log = Log(
                        level="WARNING",  # Compliance requirement
                        message=message,
                        timestamp=datetime.now(timezone.utc),
                        log_metadata=metadata
                    )
                    db.add(audit_log)
                    db.commit()
                    db.close()
                    logger.warning(f"Settings exported: {setting_count} items ({format}) by {user_email}, secrets={include_secrets}")
                except Exception as e:
                    logger.error(f"Failed to log settings export to database: {str(e)}")
                    print(f"[{timestamp}] {message}")
            else:
                print(f"[{timestamp}] {message}")

            return metadata
        except Exception as e:
            logger.error(f"Error in log_export: {str(e)}")
            return None

    @staticmethod
    def get_setting_history(
        # db: Session,
        setting_id: int,
        limit: int = 50,
        skip: int = 0,
    ) -> list:  # List[SettingAuditLog]
        """
        Get audit history for a specific setting.

        Args:
            db: Database session
            setting_id: ID of the setting
            limit: Maximum number of entries to return
            skip: Number of entries to skip (for pagination)

        Returns:
            List of SettingAuditLog entries sorted by timestamp DESC
        """
        try:
            if not DB_AVAILABLE:
                logger.warning("Database not available for querying setting history")
                return []

            db = SessionLocal()
            logs = db.query(Log).filter(
                Log.log_metadata['setting_id'].astext.cast(int) == setting_id
            ).order_by(
                Log.timestamp.desc()
            ).offset(skip).limit(limit).all()

            results = []
            for log in logs:
                results.append({
                    "id": log.id if hasattr(log, 'id') else None,
                    "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                    "action": log.log_metadata.get("action") if log.log_metadata else None,
                    "changed_by_email": log.log_metadata.get("changed_by_email") if log.log_metadata else None,
                    "message": log.message,
                    "metadata": log.log_metadata
                })

            db.close()
            logger.info(f"Retrieved {len(results)} history records for setting {setting_id}")
            return results
        except Exception as e:
            logger.error(f"Error retrieving setting history: {str(e)}")
            return []

    @staticmethod
    def get_user_actions(
        # db: Session,
        user_id: int,
        limit: int = 100,
        skip: int = 0,
    ) -> list:  # List[SettingAuditLog]
        """
        Get all actions performed by a specific user.

        Args:
            db: Database session
            user_id: ID of the user
            limit: Maximum number of entries to return
            skip: Number of entries to skip (for pagination)

        Returns:
            List of SettingAuditLog entries sorted by timestamp DESC
        """
        try:
            if not DB_AVAILABLE:
                logger.warning("Database not available for querying user actions")
                return []

            db = SessionLocal()
            logs = db.query(Log).filter(
                Log.log_metadata['changed_by_id'].astext.cast(int) == user_id
            ).order_by(
                Log.timestamp.desc()
            ).offset(skip).limit(limit).all()

            results = []
            for log in logs:
                results.append({
                    "id": log.id if hasattr(log, 'id') else None,
                    "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                    "action": log.log_metadata.get("action") if log.log_metadata else None,
                    "setting_key": log.log_metadata.get("setting_key") if log.log_metadata else None,
                    "message": log.message,
                    "level": log.level,
                    "metadata": log.log_metadata
                })

            db.close()
            logger.info(f"Retrieved {len(results)} action records for user {user_id}")
            return results
        except Exception as e:
            logger.error(f"Error retrieving user actions: {str(e)}")
            return []

    @staticmethod
    def get_recent_changes(
        # db: Session,
        setting_id: Optional[int] = None,
        category: Optional[str] = None,
        hours: int = 24,
        limit: int = 100,
    ) -> list:  # List[SettingAuditLog]
        """
        Get recent changes to settings.

        Args:
            db: Database session
            setting_id: Optional filter by specific setting
            category: Optional filter by setting category
            hours: How many hours back to look (default 24)
            limit: Maximum number of entries to return

        Returns:
            List of recent SettingAuditLog entries
        """
        try:
            if not DB_AVAILABLE:
                logger.warning("Database not available for querying recent changes")
                return []

            from datetime import timedelta
            db = SessionLocal()
            
            # Calculate cutoff time
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
            
            # Build query
            query = db.query(Log).filter(Log.timestamp >= cutoff_time)
            
            # Add optional filters
            if setting_id is not None:
                query = query.filter(
                    Log.log_metadata['setting_id'].astext.cast(int) == setting_id
                )
            
            if category is not None:
                query = query.filter(
                    Log.log_metadata['setting_category'].astext == category
                )
            
            # Execute query
            logs = query.order_by(Log.timestamp.desc()).limit(limit).all()

            results = []
            for log in logs:
                results.append({
                    "id": log.id if hasattr(log, 'id') else None,
                    "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                    "action": log.log_metadata.get("action") if log.log_metadata else None,
                    "setting_key": log.log_metadata.get("setting_key") if log.log_metadata else None,
                    "changed_by_email": log.log_metadata.get("changed_by_email") if log.log_metadata else None,
                    "message": log.message,
                    "metadata": log.log_metadata
                })

            db.close()
            logger.info(f"Retrieved {len(results)} recent changes from last {hours} hours")
            return results
        except Exception as e:
            logger.error(f"Error retrieving recent changes: {str(e)}")
            return []

    @staticmethod
    def get_setting_current_value_before(
        # db: Session,
        setting_id: int,
        before_timestamp: datetime,
    ) -> Optional[str]:
        """
        Get the value of a setting as of a specific point in time.

        Useful for understanding what changed over time.

        Args:
            db: Database session
            setting_id: ID of the setting
            before_timestamp: Timestamp to query

        Returns:
            The setting value as of before_timestamp, or None if not found
        """
        try:
            if not DB_AVAILABLE:
                logger.warning("Database not available for querying historical value")
                return None

            db = SessionLocal()
            
            # Query for the most recent change before the given timestamp
            log = db.query(Log).filter(
                Log.log_metadata['setting_id'].astext.cast(int) == setting_id,
                Log.timestamp <= before_timestamp
            ).order_by(
                Log.timestamp.desc()
            ).first()

            db.close()

            if log and log.log_metadata:
                # The value depends on the action
                action = log.log_metadata.get("action")
                if action == "DELETE":
                    # If it was deleted, return the old_value from the delete entry
                    return log.log_metadata.get("old_value")
                else:
                    # For other actions, return the new_value (which is current at that time)
                    return log.log_metadata.get("new_value")

            logger.info(f"No historical value found for setting {setting_id} before {before_timestamp}")
            return None
        except Exception as e:
            logger.error(f"Error retrieving historical value: {str(e)}")
            return None

    @staticmethod
    def get_audit_statistics(
        # db: Session,
        days: int = 30,
    ) -> dict:
        """
        Get audit logging statistics for the past N days.

        Args:
            db: Database session
            days: Number of days to analyze

        Returns:
            Dictionary with statistics like:
            {
                'total_changes': 1234,
                'total_creates': 45,
                'total_updates': 1100,
                'total_deletes': 50,
                'total_exports': 39,
                'most_active_user': {'user_id': 5, 'email': 'user@example.com', 'changes': 342},
                'most_changed_setting': {'setting_id': 12, 'key': 'api_timeout', 'changes': 67},
                'top_categories': {...}
            }
        """
        try:
            if not DB_AVAILABLE:
                logger.warning("Database not available for audit statistics")
                return {"error": "Database not available"}

            from datetime import timedelta
            
            db = SessionLocal()
            cutoff_time = datetime.now(timezone.utc) - timedelta(days=days)
            
            # Get all logs in the time period
            logs = db.query(Log).filter(Log.timestamp >= cutoff_time).all()
            
            # Initialize statistics
            stats = {
                "period_days": days,
                "period_start": cutoff_time.isoformat(),
                "period_end": datetime.now(timezone.utc).isoformat(),
                "total_changes": len(logs),
                "by_action": {},
                "by_user": {},
                "by_setting": {},
                "by_category": {}
            }
            
            # Aggregate by action
            action_count = {}
            user_count = {}
            setting_count = {}
            category_count = {}
            
            for log in logs:
                if log.log_metadata:
                    action = log.log_metadata.get("action")
                    if action:
                        action_count[action] = action_count.get(action, 0) + 1
                    
                    user_email = log.log_metadata.get("changed_by_email")
                    if user_email:
                        if user_email not in user_count:
                            user_count[user_email] = {"count": 0, "user_id": log.log_metadata.get("changed_by_id")}
                        user_count[user_email]["count"] += 1
                    
                    setting_key = log.log_metadata.get("setting_key")
                    if setting_key:
                        if setting_key not in setting_count:
                            setting_count[setting_key] = {"count": 0, "setting_id": log.log_metadata.get("setting_id")}
                        setting_count[setting_key]["count"] += 1
                    
                    category = log.log_metadata.get("setting_category")
                    if category:
                        category_count[category] = category_count.get(category, 0) + 1
            
            # Build results
            stats["by_action"] = action_count
            stats["top_users"] = sorted(user_count.items(), key=lambda x: x[1]["count"], reverse=True)[:10]
            stats["top_settings"] = sorted(setting_count.items(), key=lambda x: x[1]["count"], reverse=True)[:10]
            stats["by_category"] = category_count
            
            db.close()
            logger.info(f"Audit statistics retrieved for {days} days: {stats['total_changes']} changes")
            return stats
        except Exception as e:
            logger.error(f"Error retrieving audit statistics: {str(e)}")
            return {"error": str(e)}

    @staticmethod
    def cleanup_old_logs(
        # db: Session,
        retention_days: int = 365,
    ) -> int:
        """
        Delete audit logs older than retention period.

        Args:
            db: Database session
            retention_days: Keep logs from last N days

        Returns:
            Number of logs deleted
        """
        try:
            if not DB_AVAILABLE:
                logger.warning("Database not available for log cleanup")
                return 0

            from datetime import timedelta
            db = SessionLocal()
            
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)
            
            # Delete logs older than cutoff
            deleted_count = db.query(Log).filter(Log.timestamp < cutoff_date).delete()
            
            db.commit()
            db.close()
            
            logger.warning(f"Deleted {deleted_count} audit logs older than {retention_days} days")
            return deleted_count
        except Exception as e:
            logger.error(f"Error cleaning up old logs: {str(e)}")
            return 0


class AuditLoggingMiddleware:
    """
    FastAPI middleware for automatic audit logging of settings endpoints.

    Wraps setting endpoints to automatically log changes.
    """

    def __init__(
        self,
        audit_logger: SettingsAuditLogger,
    ):
        """
        Initialize middleware.

        Args:
            audit_logger: SettingsAuditLogger instance
        """
        self.audit_logger = audit_logger

    @staticmethod
    def extract_client_info(request) -> tuple[str, str]:
        """
        Extract IP address and user agent from request.

        Args:
            request: FastAPI Request object

        Returns:
            Tuple of (ip_address, user_agent)
        """
        # Get IP from X-Forwarded-For header (if behind proxy) or client
        ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")

        # Get user agent
        user_agent = request.headers.get("User-Agent", "unknown")

        return ip, user_agent

    @staticmethod
    def get_change_description(
        action: str,
        setting_key: str,
        changes: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Build human-readable description of a change.

        Args:
            action: Action performed (CREATE, UPDATE, DELETE, etc.)
            setting_key: Key of the setting
            changes: Dictionary of changes

        Returns:
            Formatted description string
        """
        if action == SettingsAuditLogger.ACTION_CREATE:
            return f"Created setting '{setting_key}'"

        elif action == SettingsAuditLogger.ACTION_DELETE:
            return f"Deleted setting '{setting_key}'"

        elif action == SettingsAuditLogger.ACTION_UPDATE:
            if not changes:
                return f"Updated setting '{setting_key}'"

            parts = [f"Updated setting '{setting_key}':"]
            for field, values in changes.items():
                if isinstance(values, dict) and 'old' in values and 'new' in values:
                    parts.append(f"  - {field}: {values['old']!r} → {values['new']!r}")
                else:
                    parts.append(f"  - {field} changed")

            return "\n".join(parts)

        elif action == SettingsAuditLogger.ACTION_BULK_UPDATE:
            count = len(changes) if changes else 0
            return f"Bulk updated {count} settings"

        elif action == SettingsAuditLogger.ACTION_ROLLBACK:
            return f"Rolled back setting '{setting_key}'"

        elif action == SettingsAuditLogger.ACTION_EXPORT:
            return f"Exported settings"

        else:
            return f"{action} on setting '{setting_key}'"
