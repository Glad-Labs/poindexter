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
    from database import get_session
    from models import Log
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False


logger = logging.getLogger(__name__)


def log_audit(action: str, setting_id: str, user_id: str, old_value: Any = None, new_value: Any = None, **kwargs) -> None:
    """Log audit event for settings changes"""
    audit_entry = {
        "action": action,
        "setting_id": setting_id,
        "user_id": user_id,
        "old_value": old_value,
        "new_value": new_value,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **kwargs
    }
    logger.info(f"AUDIT: {action} on {setting_id} by {user_id}", extra=audit_entry)


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
                    db = get_session()
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
                    db = get_session()
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
                    db = get_session()
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
                    db = get_session()
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
                    db = get_session()
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
                    db = get_session()
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

            db = get_session()
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

            db = get_session()
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
            db = get_session()
            
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

            db = get_session()
            
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
            
            db = get_session()
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
            db = get_session()
            
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


# ============================================================================
# BUSINESS EVENT AUDIT METHODS
# Track critical business events for production debugging and compliance
# ============================================================================


class BusinessEventAuditLogger:
    """Tracks business-level events for production monitoring and debugging."""

    @staticmethod
    def log_task_created(
        task_id: str,
        task_type: str,
        created_by: str,
        description: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Log task creation event.

        Args:
            task_id: Unique task identifier
            task_type: Type of task (content_generation, analysis, etc.)
            created_by: User who created the task
            description: Optional task description

        Returns:
            Created log entry or None if failed
        """
        try:
            if not DB_AVAILABLE:
                logger.warning(f"Database not available for task creation logging")
                return None

            db = get_session()
            timestamp = datetime.now(timezone.utc)

            log_entry = Log(
                level="INFO",
                message=f"Task created: {task_id} (type: {task_type})",
                timestamp=timestamp,
                log_metadata={
                    "event_type": "task_created",
                    "task_id": task_id,
                    "task_type": task_type,
                    "created_by": created_by,
                    "description": description or "",
                },
            )
            db.add(log_entry)
            db.commit()
            db.close()

            logger.info(f"Task creation logged: {task_id}")
            return {
                "success": True,
                "log_id": getattr(log_entry, "id", None),
                "timestamp": timestamp.isoformat(),
            }
        except Exception as e:
            logger.error(f"Error logging task creation: {str(e)}")
            return None

    @staticmethod
    def log_task_updated(
        task_id: str,
        updated_by: str,
        changes: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Log task update event.

        Args:
            task_id: Task identifier
            updated_by: User who updated the task
            changes: Dictionary of what changed

        Returns:
            Created log entry or None if failed
        """
        try:
            if not DB_AVAILABLE:
                return None

            db = get_session()
            timestamp = datetime.now(timezone.utc)

            log_entry = Log(
                level="INFO",
                message=f"Task updated: {task_id}",
                timestamp=timestamp,
                log_metadata={
                    "event_type": "task_updated",
                    "task_id": task_id,
                    "updated_by": updated_by,
                    "changes": changes,
                },
            )
            db.add(log_entry)
            db.commit()
            db.close()

            logger.info(f"Task update logged: {task_id}")
            return {"success": True, "timestamp": timestamp.isoformat()}
        except Exception as e:
            logger.error(f"Error logging task update: {str(e)}")
            return None

    @staticmethod
    def log_task_completed(
        task_id: str,
        result_summary: str,
        execution_time_ms: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Log task completion event.

        Args:
            task_id: Task identifier
            result_summary: Summary of task result
            execution_time_ms: Execution time in milliseconds

        Returns:
            Created log entry or None if failed
        """
        try:
            if not DB_AVAILABLE:
                return None

            db = get_session()
            timestamp = datetime.now(timezone.utc)

            log_entry = Log(
                level="INFO",
                message=f"Task completed: {task_id} (Time: {execution_time_ms}ms)",
                timestamp=timestamp,
                log_metadata={
                    "event_type": "task_completed",
                    "task_id": task_id,
                    "result_summary": result_summary,
                    "execution_time_ms": execution_time_ms,
                },
            )
            db.add(log_entry)
            db.commit()
            db.close()

            logger.info(f"Task completion logged: {task_id}")
            return {"success": True, "timestamp": timestamp.isoformat()}
        except Exception as e:
            logger.error(f"Error logging task completion: {str(e)}")
            return None

    @staticmethod
    def log_task_failed(
        task_id: str,
        error_message: str,
        error_type: str,
        stack_trace: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Log task failure event.

        Args:
            task_id: Task identifier
            error_message: Error message
            error_type: Type of error (ValueError, TimeoutError, etc.)
            stack_trace: Optional full stack trace

        Returns:
            Created log entry or None if failed
        """
        try:
            if not DB_AVAILABLE:
                return None

            db = get_session()
            timestamp = datetime.now(timezone.utc)

            log_entry = Log(
                level="ERROR",
                message=f"Task failed: {task_id} - {error_type}: {error_message}",
                timestamp=timestamp,
                log_metadata={
                    "event_type": "task_failed",
                    "task_id": task_id,
                    "error_message": error_message,
                    "error_type": error_type,
                    "stack_trace": stack_trace or "",
                },
            )
            db.add(log_entry)
            db.commit()
            db.close()

            logger.error(f"Task failure logged: {task_id}")
            return {"success": True, "timestamp": timestamp.isoformat()}
        except Exception as e:
            logger.error(f"Error logging task failure: {str(e)}")
            return None

    @staticmethod
    def log_content_generated(
        content_type: str,
        content_id: str,
        length_words: int,
        agent_name: str,
        model_used: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Log content generation event.

        Args:
            content_type: Type of content (blog_post, social_media, email, etc.)
            content_id: ID of generated content
            length_words: Word count of generated content
            agent_name: Name of agent that generated content
            model_used: AI model used for generation

        Returns:
            Created log entry or None if failed
        """
        try:
            if not DB_AVAILABLE:
                return None

            db = get_session()
            timestamp = datetime.now(timezone.utc)

            log_entry = Log(
                level="INFO",
                message=f"Content generated: {content_type} ({length_words} words)",
                timestamp=timestamp,
                log_metadata={
                    "event_type": "content_generated",
                    "content_type": content_type,
                    "content_id": content_id,
                    "length_words": length_words,
                    "agent_name": agent_name,
                    "model_used": model_used,
                },
            )
            db.add(log_entry)
            db.commit()
            db.close()

            logger.info(f"Content generation logged: {content_type}:{content_id}")
            return {"success": True, "timestamp": timestamp.isoformat()}
        except Exception as e:
            logger.error(f"Error logging content generation: {str(e)}")
            return None

    @staticmethod
    def log_model_called(
        model_name: str,
        provider: str,
        tokens_used: int,
        response_time_ms: int,
        cost_usd: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Log AI model API call.

        Args:
            model_name: Name of model called (gpt-4, claude-3, etc.)
            provider: Provider (openai, anthropic, google, etc.)
            tokens_used: Number of tokens used
            response_time_ms: Response time in milliseconds
            cost_usd: Cost of API call in USD

        Returns:
            Created log entry or None if failed
        """
        try:
            if not DB_AVAILABLE:
                return None

            db = get_session()
            timestamp = datetime.now(timezone.utc)

            log_entry = Log(
                level="INFO",
                message=f"Model called: {model_name} ({provider}) - {tokens_used} tokens",
                timestamp=timestamp,
                log_metadata={
                    "event_type": "model_called",
                    "model_name": model_name,
                    "provider": provider,
                    "tokens_used": tokens_used,
                    "response_time_ms": response_time_ms,
                    "cost_usd": cost_usd or 0.0,
                },
            )
            db.add(log_entry)
            db.commit()
            db.close()

            logger.info(f"Model call logged: {model_name} from {provider}")
            return {"success": True, "timestamp": timestamp.isoformat()}
        except Exception as e:
            logger.error(f"Error logging model call: {str(e)}")
            return None

    @staticmethod
    def log_api_call(
        endpoint: str,
        method: str,
        user_id: str,
        status_code: int,
        response_time_ms: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Log external API call.

        Args:
            endpoint: API endpoint called
            method: HTTP method (GET, POST, etc.)
            user_id: User making the call
            status_code: HTTP response status code
            response_time_ms: Response time in milliseconds

        Returns:
            Created log entry or None if failed
        """
        try:
            if not DB_AVAILABLE:
                return None

            db = get_session()
            timestamp = datetime.now(timezone.utc)

            log_entry = Log(
                level="INFO",
                message=f"API call: {method} {endpoint} - {status_code}",
                timestamp=timestamp,
                log_metadata={
                    "event_type": "api_call",
                    "endpoint": endpoint,
                    "method": method,
                    "user_id": user_id,
                    "status_code": status_code,
                    "response_time_ms": response_time_ms,
                },
            )
            db.add(log_entry)
            db.commit()
            db.close()

            logger.info(f"API call logged: {method} {endpoint}")
            return {"success": True, "timestamp": timestamp.isoformat()}
        except Exception as e:
            logger.error(f"Error logging API call: {str(e)}")
            return None

    @staticmethod
    def log_permission_denied(
        user_id: str,
        permission: str,
        resource: str,
        action: str,
        reason: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Log permission denial event (security event).

        Args:
            user_id: User attempting access
            permission: Permission that was denied
            resource: Resource being accessed
            action: Action attempted (read, write, delete, etc.)
            reason: Reason for denial

        Returns:
            Created log entry or None if failed
        """
        try:
            if not DB_AVAILABLE:
                return None

            db = get_session()
            timestamp = datetime.now(timezone.utc)

            log_entry = Log(
                level="WARNING",
                message=f"Permission denied: {user_id} - {action} on {resource}",
                timestamp=timestamp,
                log_metadata={
                    "event_type": "permission_denied",
                    "user_id": user_id,
                    "permission": permission,
                    "resource": resource,
                    "action": action,
                    "reason": reason,
                },
            )
            db.add(log_entry)
            db.commit()
            db.close()

            logger.warning(f"Permission denied: {user_id} on {resource}")
            return {"success": True, "timestamp": timestamp.isoformat()}
        except Exception as e:
            logger.error(f"Error logging permission denial: {str(e)}")
            return None

    @staticmethod
    def log_error(
        error_type: str,
        error_message: str,
        component: str,
        user_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Log application error event.

        Args:
            error_type: Type of error (ValueError, TimeoutError, DatabaseError, etc.)
            error_message: Error message
            component: Component where error occurred
            user_id: User ID if applicable
            context: Additional context data

        Returns:
            Created log entry or None if failed
        """
        try:
            if not DB_AVAILABLE:
                return None

            db = get_session()
            timestamp = datetime.now(timezone.utc)

            log_entry = Log(
                level="ERROR",
                message=f"{error_type} in {component}: {error_message}",
                timestamp=timestamp,
                log_metadata={
                    "event_type": "error",
                    "error_type": error_type,
                    "error_message": error_message,
                    "component": component,
                    "user_id": user_id or "",
                    "context": context or {},
                },
            )
            db.add(log_entry)
            db.commit()
            db.close()

            logger.error(f"Error logged: {error_type} in {component}")
            return {"success": True, "timestamp": timestamp.isoformat()}
        except Exception as e:
            logger.error(f"Error logging error event: {str(e)}")
            return None

    @staticmethod
    def log_agent_executed(
        agent_name: str,
        task_type: str,
        status: str,
        execution_time_ms: int,
        result: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Log agent execution event.

        Args:
            agent_name: Name of agent (content_agent, financial_agent, etc.)
            task_type: Type of task executed
            status: Execution status (success, failed, timeout, etc.)
            execution_time_ms: Execution time in milliseconds
            result: Optional result summary

        Returns:
            Created log entry or None if failed
        """
        try:
            if not DB_AVAILABLE:
                return None

            db = get_session()
            timestamp = datetime.now(timezone.utc)

            log_entry = Log(
                level="INFO",
                message=f"Agent executed: {agent_name} - {status}",
                timestamp=timestamp,
                log_metadata={
                    "event_type": "agent_executed",
                    "agent_name": agent_name,
                    "task_type": task_type,
                    "status": status,
                    "execution_time_ms": execution_time_ms,
                    "result": result or "",
                },
            )
            db.add(log_entry)
            db.commit()
            db.close()

            logger.info(f"Agent execution logged: {agent_name}")
            return {"success": True, "timestamp": timestamp.isoformat()}
        except Exception as e:
            logger.error(f"Error logging agent execution: {str(e)}")
            return None

    @staticmethod
    def log_database_query(
        query_type: str,
        table_name: str,
        execution_time_ms: float,
        rows_affected: int,
        status: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Log database query execution (for performance monitoring).

        Args:
            query_type: Type of query (SELECT, INSERT, UPDATE, DELETE)
            table_name: Table being queried
            execution_time_ms: Execution time in milliseconds
            rows_affected: Number of rows affected
            status: Query status (success, slow, error, etc.)

        Returns:
            Created log entry or None if failed
        """
        try:
            if not DB_AVAILABLE:
                return None

            db = get_session()
            timestamp = datetime.now(timezone.utc)

            log_entry = Log(
                level="DEBUG",
                message=f"DB Query: {query_type} {table_name} - {execution_time_ms}ms",
                timestamp=timestamp,
                log_metadata={
                    "event_type": "database_query",
                    "query_type": query_type,
                    "table_name": table_name,
                    "execution_time_ms": execution_time_ms,
                    "rows_affected": rows_affected,
                    "status": status,
                },
            )
            db.add(log_entry)
            db.commit()
            db.close()

            logger.debug(f"Database query logged: {query_type} {table_name}")
            return {"success": True, "timestamp": timestamp.isoformat()}
        except Exception as e:
            logger.error(f"Error logging database query: {str(e)}")
            return None

    @staticmethod
    def log_cache_operation(
        operation: str,
        cache_key: str,
        status: str,
        hit_miss: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Log cache operation (get, set, delete, clear).

        Args:
            operation: Cache operation (get, set, delete, clear)
            cache_key: Key being accessed
            status: Operation status (success, failure, timeout)
            hit_miss: For get operations: hit or miss

        Returns:
            Created log entry or None if failed
        """
        try:
            if not DB_AVAILABLE:
                return None

            db = get_session()
            timestamp = datetime.now(timezone.utc)

            log_entry = Log(
                level="DEBUG",
                message=f"Cache {operation}: {cache_key} - {status}",
                timestamp=timestamp,
                log_metadata={
                    "event_type": "cache_operation",
                    "operation": operation,
                    "cache_key": cache_key,
                    "status": status,
                    "hit_miss": hit_miss or "",
                },
            )
            db.add(log_entry)
            db.commit()
            db.close()

            logger.debug(f"Cache operation logged: {operation} {cache_key}")
            return {"success": True, "timestamp": timestamp.isoformat()}
        except Exception as e:
            logger.error(f"Error logging cache operation: {str(e)}")
            return None

