"""
Input Validation Service

Provides centralized input validation functions for API endpoints.
Prevents SQL injection, XSS, oversized payloads, and invalid data.
"""

import re
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Custom exception for validation failures"""
    pass


class InputValidator:
    """Validates user input across all endpoints"""
    
    # SQL injection patterns (basic detection)
    SQL_PATTERNS = [
        r"(\b(UNION|SELECT|INSERT|UPDATE|DELETE|DROP|EXEC|EXECUTE)\b)",
        r"(--|#|\/\*|\*\/)",  # SQL comments
        r"(;\s*DROP|;\s*DELETE|;\s*TRUNCATE)",  # Dangerous statements
        r"(xp_|sp_)",  # SQL Server stored procedures
    ]
    
    # XSS patterns
    XSS_PATTERNS = [
        r"<script[^>]*>.*?</script>",
        r"javascript:",
        r"on(load|click|error|focus|blur|change|submit)\s*=",
        r"<iframe[^>]*>",
        r"<object[^>]*>",
        r"<embed[^>]*>",
    ]
    
    # Size limits
    MAX_STRING_LENGTH = 10000  # 10KB
    MAX_JSON_SIZE = 1000000  # 1MB
    MAX_ARRAY_LENGTH = 1000
    MAX_NESTED_DEPTH = 10
    
    # Email regex (RFC 5322 simplified)
    EMAIL_PATTERN = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    
    # URL regex
    URL_PATTERN = r"^https?://[^\s/$.?#].[^\s]*$"
    
    @classmethod
    def validate_string(
        cls,
        value: Any,
        field_name: str,
        min_length: int = 1,
        max_length: int = 10000,
        allow_html: bool = False,
        allow_sql: bool = False,
        pattern: Optional[str] = None,
    ) -> str:
        """
        Validate and sanitize string input.
        
        Args:
            value: The value to validate
            field_name: Name of the field (for error messages)
            min_length: Minimum string length
            max_length: Maximum string length
            allow_html: Allow HTML content
            allow_sql: Allow SQL keywords (generally not recommended)
            pattern: Optional regex pattern to match
            
        Returns:
            Validated and sanitized string
            
        Raises:
            ValidationError: If validation fails
        """
        if not isinstance(value, str):
            raise ValidationError(f"{field_name} must be a string")
        
        # Check length
        if len(value) < min_length:
            raise ValidationError(
                f"{field_name} must be at least {min_length} characters"
            )
        
        if len(value) > max_length:
            raise ValidationError(
                f"{field_name} must not exceed {max_length} characters"
            )
        
        # Check for SQL injection
        if not allow_sql and cls._contains_sql_injection(value):
            raise ValidationError(
                f"{field_name} contains invalid SQL keywords or syntax"
            )
        
        # Check for XSS
        if not allow_html and cls._contains_xss(value):
            raise ValidationError(
                f"{field_name} contains invalid HTML or JavaScript"
            )
        
        # Check pattern match if provided
        if pattern:
            if not re.match(pattern, value, re.IGNORECASE):
                raise ValidationError(
                    f"{field_name} does not match required pattern"
                )
        
        # Strip whitespace
        return value.strip()
    
    @classmethod
    def validate_email(cls, email: str) -> str:
        """Validate email address"""
        email = cls.validate_string(email, "email", min_length=5, max_length=254)
        
        if not re.match(cls.EMAIL_PATTERN, email):
            raise ValidationError("Invalid email address format")
        
        return email.lower()
    
    @classmethod
    def validate_url(cls, url: str) -> str:
        """Validate URL"""
        url = cls.validate_string(url, "url", min_length=10, max_length=2048)
        
        if not re.match(cls.URL_PATTERN, url, re.IGNORECASE):
            raise ValidationError("Invalid URL format (must start with http:// or https://)")
        
        return url
    
    @classmethod
    def validate_integer(
        cls,
        value: Any,
        field_name: str,
        min_value: Optional[int] = None,
        max_value: Optional[int] = None,
    ) -> int:
        """Validate integer input"""
        try:
            int_value = int(value)
        except (ValueError, TypeError):
            raise ValidationError(f"{field_name} must be an integer")
        
        if min_value is not None and int_value < min_value:
            raise ValidationError(
                f"{field_name} must be at least {min_value}"
            )
        
        if max_value is not None and int_value > max_value:
            raise ValidationError(
                f"{field_name} must not exceed {max_value}"
            )
        
        return int_value
    
    @classmethod
    def validate_dict(
        cls,
        value: Any,
        field_name: str,
        max_size: int = 1000000,
        max_depth: int = 10,
        allowed_keys: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Validate dictionary input.
        
        Args:
            value: The value to validate
            field_name: Name of the field
            max_size: Maximum size in bytes
            max_depth: Maximum nesting depth
            allowed_keys: List of allowed top-level keys (if None, all allowed)
            
        Returns:
            Validated dictionary
            
        Raises:
            ValidationError: If validation fails
        """
        if not isinstance(value, dict):
            raise ValidationError(f"{field_name} must be a dictionary")
        
        # Check size (rough estimate)
        import json
        size = len(json.dumps(value).encode('utf-8'))
        if size > max_size:
            raise ValidationError(
                f"{field_name} exceeds maximum size of {max_size} bytes"
            )
        
        # Check nesting depth
        if cls._get_dict_depth(value) > max_depth:
            raise ValidationError(
                f"{field_name} nesting is too deep (max {max_depth} levels)"
            )
        
        # Check allowed keys
        if allowed_keys:
            invalid_keys = set(value.keys()) - set(allowed_keys)
            if invalid_keys:
                raise ValidationError(
                    f"{field_name} contains invalid keys: {invalid_keys}"
                )
        
        # Validate all string values recursively
        for key, val in value.items():
            if isinstance(val, str):
                try:
                    cls.validate_string(
                        val,
                        f"{field_name}.{key}",
                        max_length=cls.MAX_STRING_LENGTH,
                    )
                except ValidationError:
                    raise
        
        return value
    
    @classmethod
    def validate_list(
        cls,
        value: Any,
        field_name: str,
        max_length: int = 1000,
        item_type: Optional[type] = None,
    ) -> List[Any]:
        """Validate list input"""
        if not isinstance(value, list):
            raise ValidationError(f"{field_name} must be a list")
        
        if len(value) > max_length:
            raise ValidationError(
                f"{field_name} must not exceed {max_length} items"
            )
        
        if item_type:
            for i, item in enumerate(value):
                if not isinstance(item, item_type):
                    raise ValidationError(
                        f"{field_name}[{i}] must be of type {item_type.__name__}"
                    )
        
        return value
    
    @classmethod
    def validate_datetime(
        cls,
        value: Any,
        field_name: str,
        allow_future: bool = True,
    ) -> datetime:
        """
        Validate datetime input.
        
        Args:
            value: The value to validate (datetime object or ISO string)
            field_name: Name of the field
            allow_future: Whether to allow future dates
            
        Returns:
            Validated datetime
            
        Raises:
            ValidationError: If validation fails
        """
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError:
                raise ValidationError(
                    f"{field_name} must be a valid ISO format datetime"
                )
        else:
            raise ValidationError(f"{field_name} must be a datetime object or ISO string")
        
        if not allow_future and dt > datetime.now():
            raise ValidationError(f"{field_name} cannot be in the future")
        
        return dt
    
    @classmethod
    def _contains_sql_injection(cls, value: str) -> bool:
        """Check if string contains SQL injection patterns"""
        value_upper = value.upper()
        
        for pattern in cls.SQL_PATTERNS:
            if re.search(pattern, value_upper, re.IGNORECASE):
                return True
        
        return False
    
    @classmethod
    def _contains_xss(cls, value: str) -> bool:
        """Check if string contains XSS patterns"""
        for pattern in cls.XSS_PATTERNS:
            if re.search(pattern, value, re.IGNORECASE):
                return True
        
        return False
    
    @classmethod
    def _get_dict_depth(cls, d: Dict[str, Any], current_depth: int = 0) -> int:
        """Calculate maximum nesting depth of a dictionary"""
        if not isinstance(d, dict) or current_depth > cls.MAX_NESTED_DEPTH:
            return current_depth
        
        max_depth = current_depth
        for value in d.values():
            if isinstance(value, dict):
                depth = cls._get_dict_depth(value, current_depth + 1)
                max_depth = max(max_depth, depth)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, dict):
                        depth = cls._get_dict_depth(item, current_depth + 1)
                        max_depth = max(max_depth, depth)
        
        return max_depth


class SanitizationHelper:
    """Helper functions for sanitizing user input"""
    
    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """Remove potentially dangerous characters from filename"""
        # Remove path separators
        filename = filename.replace("/", "_").replace("\\", "_")
        # Remove null bytes
        filename = filename.replace("\x00", "")
        # Keep only alphanumeric, dots, hyphens, underscores
        filename = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)
        return filename[:255]  # Max filename length
    
    @staticmethod
    def sanitize_html(html_content: str) -> str:
        """Remove potentially dangerous HTML/JavaScript"""
        # Remove script tags
        html_content = re.sub(
            r"<script[^>]*>.*?</script>",
            "",
            html_content,
            flags=re.IGNORECASE | re.DOTALL
        )
        
        # Remove event handlers
        html_content = re.sub(
            r"\s*on(load|click|error|focus|blur|change|submit)\s*=[^>]*",
            "",
            html_content,
            flags=re.IGNORECASE
        )
        
        return html_content
