"""
JWT Authentication Middleware for GLAD Labs

Provides:
- Token verification and validation
- JWT dependency injection for FastAPI routes
- Rate limiting middleware
- Request/response logging for authentication events

This middleware integrates with the auth service to provide
secure token-based authentication across all protected endpoints.
"""

import time
from typing import Dict, Optional, Tuple
from collections import defaultdict
from datetime import datetime, timezone
import logging

from services.auth import validate_access_token, AuthConfig
from models import Log
from database import get_session

logger = logging.getLogger(__name__)


# ============================================================================
# Rate Limiting
# ============================================================================

class RateLimiter:
    """
    In-memory rate limiter for request throttling.
    
    Tracks requests per IP address and user to prevent abuse.
    """
    
    def __init__(self):
        """Initialize rate limiter with empty tracking"""
        self.ip_requests: Dict[str, list] = defaultdict(list)
        self.user_requests: Dict[str, list] = defaultdict(list)
        self.cleanup_interval = 3600  # Clean old records every hour
        self.last_cleanup = time.time()
    
    def is_rate_limited(
        self,
        ip_address: str,
        limit: int = 60,
        window: int = 60
    ) -> bool:
        """
        Check if IP address is rate limited.
        
        Args:
            ip_address: Client IP address
            limit: Max requests per window (default: 60 per minute)
            window: Time window in seconds (default: 60)
        
        Returns:
            True if rate limited, False otherwise
        """
        current_time = time.time()
        
        # Clean old records periodically
        if current_time - self.last_cleanup > self.cleanup_interval:
            self._cleanup_old_records(current_time, window)
            self.last_cleanup = current_time
        
        # Get requests within window
        requests = self.ip_requests[ip_address]
        cutoff_time = current_time - window
        
        # Remove requests outside window
        self.ip_requests[ip_address] = [
            req_time for req_time in requests
            if req_time > cutoff_time
        ]
        
        # Check if limit exceeded
        if len(self.ip_requests[ip_address]) >= limit:
            return True
        
        # Record this request
        self.ip_requests[ip_address].append(current_time)
        return False
    
    def track_failed_login(self, email: str, ip_address: str) -> int:
        """
        Track failed login attempt for user/IP combination.
        
        Args:
            email: User email
            ip_address: Client IP address
        
        Returns:
            Number of failed attempts in current window
        """
        current_time = time.time()
        key = f"{email}:{ip_address}"
        
        # Get failed attempts within window
        attempts = self.user_requests[key]
        cutoff_time = current_time - 3600  # 1 hour window
        
        # Remove old attempts
        self.user_requests[key] = [
            attempt_time for attempt_time in attempts
            if attempt_time > cutoff_time
        ]
        
        # Record new attempt
        self.user_requests[key].append(current_time)
        
        return len(self.user_requests[key])
    
    def _cleanup_old_records(self, current_time: float, window: int) -> None:
        """Remove old rate limit records"""
        cutoff_time = current_time - (window * 10)  # Keep 10x window of data
        
        # Clean IP records
        for ip in list(self.ip_requests.keys()):
            self.ip_requests[ip] = [
                req_time for req_time in self.ip_requests[ip]
                if req_time > cutoff_time
            ]
            if not self.ip_requests[ip]:
                del self.ip_requests[ip]
        
        # Clean user records
        for key in list(self.user_requests.keys()):
            self.user_requests[key] = [
                attempt_time for attempt_time in self.user_requests[key]
                if attempt_time > cutoff_time
            ]
            if not self.user_requests[key]:
                del self.user_requests[key]


# Global rate limiter instance
rate_limiter = RateLimiter()


# ============================================================================
# Token Verification
# ============================================================================

class JWTTokenVerifier:
    """
    JWT token verification and claim extraction.
    
    Validates token signature, expiration, and type.
    Extracts claims for use in FastAPI dependencies.
    """
    
    @staticmethod
    def verify_and_extract(token: str) -> Tuple[bool, Optional[Dict]]:
        """
        Verify JWT token and extract claims.
        
        Args:
            token: JWT token string
        
        Returns:
            (is_valid, claims) tuple
            - is_valid: Boolean indicating if token is valid
            - claims: Dict with token claims if valid, None otherwise
        """
        is_valid, claims = validate_access_token(token)
        return is_valid, claims
    
    @staticmethod
    def get_token_expiration(claims: Dict) -> datetime:
        """
        Get token expiration datetime from claims.
        
        Args:
            claims: Token claims dict
        
        Returns:
            Expiration datetime
        """
        exp_timestamp = claims.get("exp")
        if exp_timestamp:
            return datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
        return None
    
    @staticmethod
    def is_token_expired(claims: Dict) -> bool:
        """
        Check if token is expired.
        
        Args:
            claims: Token claims dict
        
        Returns:
            True if expired, False otherwise
        """
        expiration = JWTTokenVerifier.get_token_expiration(claims)
        if expiration:
            return datetime.now(timezone.utc) > expiration
        return False
    
    @staticmethod
    def get_user_id(claims: Dict) -> Optional[str]:
        """Extract user ID from claims"""
        return claims.get("user_id")
    
    @staticmethod
    def get_user_roles(claims: Dict) -> list:
        """Extract user roles from claims"""
        return claims.get("role_ids", [])
    
    @staticmethod
    def has_role(claims: Dict, role_id: str) -> bool:
        """Check if user has specific role"""
        return role_id in JWTTokenVerifier.get_user_roles(claims)


# ============================================================================
# Permission Checking
# ============================================================================

class PermissionChecker:
    """
    Authorization checker for role-based access control.
    
    Validates user permissions based on roles and claims.
    """
    
    # Permission mappings (can be extended)
    ROLE_PERMISSIONS = {
        "admin": [
            "read_settings",
            "write_settings",
            "manage_users",
            "view_analytics",
            "configure_system",
        ],
        "editor": [
            "read_settings",
            "write_settings",
            "view_analytics",
        ],
        "viewer": [
            "read_settings",
            "view_analytics",
        ],
    }
    
    @staticmethod
    def check_permission(claims: Dict, permission: str) -> bool:
        """
        Check if user has specific permission.
        
        Args:
            claims: Token claims dict
            permission: Permission to check (e.g., "read_settings")
        
        Returns:
            True if user has permission, False otherwise
        """
        user_roles = JWTTokenVerifier.get_user_roles(claims)
        
        for role_id in user_roles:
            role_name = role_id.lower()
            if role_name in PermissionChecker.ROLE_PERMISSIONS:
                if permission in PermissionChecker.ROLE_PERMISSIONS[role_name]:
                    return True
        
        return False
    
    @staticmethod
    def check_permissions(claims: Dict, permissions: list) -> bool:
        """
        Check if user has any of the specified permissions.
        
        Args:
            claims: Token claims dict
            permissions: List of permissions (any one can satisfy)
        
        Returns:
            True if user has any permission, False otherwise
        """
        return any(
            PermissionChecker.check_permission(claims, perm)
            for perm in permissions
        )
    
    @staticmethod
    def add_role_permission(role: str, permission: str) -> None:
        """
        Add permission to role (runtime configuration).
        
        Args:
            role: Role name
            permission: Permission to add
        """
        if role not in PermissionChecker.ROLE_PERMISSIONS:
            PermissionChecker.ROLE_PERMISSIONS[role] = []
        
        if permission not in PermissionChecker.ROLE_PERMISSIONS[role]:
            PermissionChecker.ROLE_PERMISSIONS[role].append(permission)


# ============================================================================
# Audit Logging
# ============================================================================

class AuthenticationAuditLogger:
    """
    Log authentication events for security audit trail.
    
    Tracks login attempts, token usage, permission checks, etc.
    """
    
    @staticmethod
    def log_login_attempt(
        email: str,
        ip_address: str,
        success: bool,
        reason: Optional[str] = None
    ) -> None:
        """
        Log login attempt.
        
        Args:
            email: User email
            ip_address: Client IP address
            success: Whether login succeeded
            reason: Failure reason if applicable
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        status = "SUCCESS" if success else "FAILED"
        
        message = f"[{timestamp}] LOGIN {status}: {email} from {ip_address}"
        if reason:
            message += f" ({reason})"
        
        # Store in database audit log
        try:
            db = get_session()
            audit_log = Log(
                level="INFO" if success else "WARNING",
                message=message,
                timestamp=datetime.now(timezone.utc),
                log_metadata={
                    "event_type": "login_attempt",
                    "email": email,
                    "ip_address": ip_address,
                    "success": success,
                    "reason": reason or ""
                }
            )
            db.add(audit_log)
            db.commit()
            db.close()
        except Exception as e:
            logger.error(f"Failed to log login attempt to database: {str(e)}")
            # Fallback to console logging
            print(f"[{timestamp}] {message}")
    
    @staticmethod
    def log_token_usage(
        user_id: str,
        endpoint: str,
        method: str,
        status_code: int
    ) -> None:
        """
        Log API endpoint access.
        
        Args:
            user_id: User ID
            endpoint: API endpoint path
            method: HTTP method
            status_code: Response status code
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        
        message = f"[{timestamp}] API {method} {endpoint} - User: {user_id}, Status: {status_code}"
        
        # Store in database audit log
        try:
            db = get_session()
            audit_log = Log(
                level="INFO",
                message=message,
                timestamp=datetime.now(timezone.utc),
                log_metadata={
                    "event_type": "api_access",
                    "user_id": user_id,
                    "endpoint": endpoint,
                    "method": method,
                    "status_code": status_code
                }
            )
            db.add(audit_log)
            db.commit()
            db.close()
        except Exception as e:
            logger.error(f"Failed to log API access to database: {str(e)}")
            # Fallback to console logging
            print(message)
    
    @staticmethod
    def log_permission_check(
        user_id: str,
        permission: str,
        allowed: bool
    ) -> None:
        """
        Log permission check result.
        
        Args:
            user_id: User ID
            permission: Permission being checked
            allowed: Whether permission was granted
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        status = "ALLOWED" if allowed else "DENIED"
        
        message = f"[{timestamp}] PERMISSION {status}: {permission} for user {user_id}"
        
        # Store in database audit log
        try:
            db = get_session()
            audit_log = Log(
                level="INFO" if allowed else "WARNING",
                message=message,
                timestamp=datetime.now(timezone.utc),
                log_metadata={
                    "event_type": "permission_check",
                    "user_id": user_id,
                    "permission": permission,
                    "allowed": allowed
                }
            )
            db.add(audit_log)
            db.commit()
            db.close()
        except Exception as e:
            logger.error(f"Failed to log permission check to database: {str(e)}")
            # Fallback to console logging
            print(message)
    
    @staticmethod
    def log_2fa_attempt(
        user_id: str,
        method: str,  # "totp" or "backup_code"
        success: bool,
        ip_address: str
    ) -> None:
        """
        Log 2FA verification attempt.
        
        Args:
            user_id: User ID
            method: 2FA method used
            success: Whether 2FA succeeded
            ip_address: Client IP address
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        status = "SUCCESS" if success else "FAILED"
        
        message = f"[{timestamp}] 2FA {status}: {method} for user {user_id} from {ip_address}"
        
        # Store in database audit log
        try:
            db = get_session()
            audit_log = Log(
                level="INFO" if success else "WARNING",
                message=message,
                timestamp=datetime.now(timezone.utc),
                log_metadata={
                    "event_type": "2fa_attempt",
                    "user_id": user_id,
                    "method": method,
                    "success": success,
                    "ip_address": ip_address
                }
            )
            db.add(audit_log)
            db.commit()
            db.close()
        except Exception as e:
            logger.error(f"Failed to log 2FA attempt to database: {str(e)}")
            # Fallback to console logging
            print(message)


# ============================================================================
# Public API
# ============================================================================

def verify_token(token: str) -> Tuple[bool, Optional[Dict]]:
    """
    Verify JWT token and return claims.
    
    Args:
        token: JWT token string
    
    Returns:
        (is_valid, claims) tuple
    """
    return JWTTokenVerifier.verify_and_extract(token)


def check_permission(claims: Dict, permission: str) -> bool:
    """
    Check if user has permission.
    
    Args:
        claims: Token claims
        permission: Permission name
    
    Returns:
        True if user has permission
    """
    return PermissionChecker.check_permission(claims, permission)


def is_rate_limited(ip_address: str) -> bool:
    """
    Check if IP is rate limited.
    
    Args:
        ip_address: Client IP address
    
    Returns:
        True if rate limited
    """
    return rate_limiter.is_rate_limited(ip_address)


def log_login_attempt(email: str, ip_address: str, success: bool, reason: str = None) -> None:
    """Log login attempt to audit trail"""
    AuthenticationAuditLogger.log_login_attempt(email, ip_address, success, reason)


def log_api_access(user_id: str, endpoint: str, method: str, status_code: int) -> None:
    """Log API endpoint access to audit trail"""
    AuthenticationAuditLogger.log_token_usage(user_id, endpoint, method, status_code)

