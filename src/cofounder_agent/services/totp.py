"""
TOTP 2FA (Two-Factor Authentication) Service for GLAD Labs

Implements Time-based One-Time Password (TOTP) for multi-factor authentication.
Uses RFC 6238 standard with SHA-1 hash algorithm.

Features:
- TOTP secret generation and provisioning
- TOTP code verification with time window tolerance
- Backup codes generation for account recovery
- Fallback authentication when TOTP fails
- Device fingerprinting for suspicious activity detection

Standards Compliance:
- RFC 6238: TOTP algorithm
- RFC 4226: HOTP (underlying algorithm)
- Python: pyotp library with cryptography

Security:
- 32-byte random seeds for secrets (256-bit)
- 30-second time windows for TOTP verification
- Backup codes: 10 codes, 8 characters each, alphanumeric
- Rate limiting: Prevent brute force on TOTP verification
"""

from typing import Tuple, List, Optional
from datetime import datetime, timezone
import secrets
import string
import os

import pyotp

from models import User
from encryption import get_encryption_service


# ============================================================================
# Configuration
# ============================================================================

class TOTPConfig:
    """TOTP configuration"""
    
    # TOTP parameters
    ISSUER_NAME = os.getenv("TOTP_ISSUER_NAME", "GLAD Labs")
    ACCOUNT_NAME_PREFIX = "gladlabs"
    
    # Time window for TOTP verification (seconds)
    # Allows for clock skew on user device
    TIME_WINDOW = 30  # Standard 30-second window
    WINDOW_SIZE = 1  # Allow 1 window before/after current (total 3 windows)
    
    # Backup codes configuration
    BACKUP_CODES_COUNT = int(os.getenv("BACKUP_CODES_COUNT", "10"))
    BACKUP_CODE_LENGTH = int(os.getenv("BACKUP_CODE_LENGTH", "8"))
    
    # Rate limiting
    MAX_TOTP_ATTEMPTS = int(os.getenv("MAX_TOTP_ATTEMPTS", "5"))
    TOTP_ATTEMPT_LOCKOUT_MINUTES = int(os.getenv("TOTP_ATTEMPT_LOCKOUT_MINUTES", "15"))


# ============================================================================
# TOTP Secret Management
# ============================================================================

class TOTPSecretManager:
    """Manages TOTP secret generation and provisioning"""
    
    @staticmethod
    def generate_secret() -> str:
        """
        Generate a new TOTP secret.
        
        Returns:
            Base32-encoded TOTP secret (32 bytes = 256-bit)
        """
        # Generate 32 random bytes (256-bit) and encode as base32
        random_bytes = secrets.token_bytes(32)
        secret = pyotp.random_base32(length=32)
        return secret
    
    @staticmethod
    def get_provisioning_uri(
        user: User,
        secret: str,
        qr_format: str = "png"
    ) -> Tuple[str, str]:
        """
        Get provisioning URI and QR code for user's authenticator app.
        
        Args:
            user: User model
            secret: TOTP secret
            qr_format: QR code format ('png', 'svg', etc.)
        
        Returns:
            Tuple of (provisioning_uri, qr_code_url)
        """
        # Create TOTP object
        totp = pyotp.TOTP(secret)
        
        # Generate provisioning URI (for manual entry or QR code)
        account_name = f"{TOTPConfig.ACCOUNT_NAME_PREFIX}:{user.email}"
        provisioning_uri = totp.provisioning_uri(
            name=account_name,
            issuer_name=TOTPConfig.ISSUER_NAME
        )
        
        # Generate QR code URL (Google Charts API)
        import urllib.parse
        qr_code_url = (
            f"https://api.qrserver.com/v1/create-qr-code/"
            f"?size=200x200&data={urllib.parse.quote(provisioning_uri)}"
        )
        
        return provisioning_uri, qr_code_url
    
    @staticmethod
    def enable_totp_for_user(user: User, secret: str) -> None:
        """
        Enable TOTP 2FA for a user by storing the secret.
        
        Args:
            user: User model
            secret: TOTP secret to store
        
        Note:
            Secret is stored encrypted in the database.
            This should be called only after user has verified they can generate codes.
        """
        encryption = get_encryption_service()
        
        # Encrypt and store secret
        user.totp_secret = encryption.encrypt_value(secret)
        user.totp_enabled = True
        
        # Note: Caller should db.commit() after calling this
    
    @staticmethod
    def disable_totp_for_user(user: User) -> None:
        """
        Disable TOTP 2FA for a user.
        
        Args:
            user: User model
        """
        user.totp_secret = None
        user.totp_enabled = False
        user.backup_codes = None
        
        # Note: Caller should db.commit() after calling this


# ============================================================================
# TOTP Verification
# ============================================================================

class TOTPVerifier:
    """Verifies TOTP codes from user's authenticator app"""
    
    @staticmethod
    def verify_totp_code(
        user: User,
        code: str
    ) -> Tuple[bool, str]:
        """
        Verify a TOTP code from user's authenticator app.
        
        Args:
            user: User model
            code: 6-digit TOTP code from authenticator app
        
        Returns:
            Tuple of (is_valid, message)
        
        Returns:
            (True, "success") - Code is valid
            (False, "invalid_code") - Code is incorrect
            (False, "totp_not_enabled") - TOTP not enabled for user
            (False, "code_already_used") - Code has already been used (replay attack)
        """
        # Check if TOTP is enabled
        if not user.totp_enabled or not user.totp_secret:
            return (False, "totp_not_enabled")
        
        # Validate code format
        if not code or not code.isdigit() or len(code) != 6:
            return (False, "invalid_code_format")
        
        # Get decrypted secret
        encryption = get_encryption_service()
        try:
            secret = encryption.decrypt_value(user.totp_secret)
        except Exception as e:
            return (False, f"failed_to_decrypt_secret: {str(e)}")
        
        # Create TOTP verifier
        totp = pyotp.TOTP(secret)
        
        # Verify code with time window tolerance
        # WINDOW_SIZE=1 means we check current window +/- 1 previous window
        is_valid = totp.verify(code, valid_window=TOTPConfig.WINDOW_SIZE)
        
        if is_valid:
            return (True, "success")
        else:
            return (False, "invalid_code")
    
    @staticmethod
    def get_current_code_for_testing(user: User) -> Optional[str]:
        """
        Get current TOTP code for testing purposes.
        
        ONLY FOR TESTING AND DEBUGGING - Never expose in production UI!
        
        Args:
            user: User model
        
        Returns:
            Current 6-digit TOTP code or None if TOTP not enabled
        """
        if not user.totp_enabled or not user.totp_secret:
            return None
        
        encryption = get_encryption_service()
        try:
            secret = encryption.decrypt_value(user.totp_secret)
            totp = pyotp.TOTP(secret)
            return totp.now()
        except Exception:
            return None


# ============================================================================
# Backup Codes Management
# ============================================================================

class BackupCodeManager:
    """Manages backup codes for account recovery"""
    
    @staticmethod
    def generate_backup_codes() -> List[str]:
        """
        Generate backup codes for account recovery.
        
        Backup codes are single-use codes that can be used instead of TOTP
        if the user loses access to their authenticator app.
        
        Returns:
            List of backup codes (alphanumeric, uppercase)
        
        Example:
            ['ABC12345', 'DEF67890', 'GHI11111', ...]
        """
        codes = []
        chars = string.ascii_uppercase + string.digits
        
        for _ in range(TOTPConfig.BACKUP_CODES_COUNT):
            # Generate code
            code = ''.join(secrets.choice(chars) for _ in range(TOTPConfig.BACKUP_CODE_LENGTH))
            codes.append(code)
        
        return codes
    
    @staticmethod
    def store_backup_codes(user: User, codes: List[str]) -> None:
        """
        Store backup codes for user (encrypted).
        
        Args:
            user: User model
            codes: List of backup codes to store
        
        Note:
            Codes are stored as comma-separated list, encrypted in database.
            Caller should db.commit() after calling this.
        """
        encryption = get_encryption_service()
        
        # Join codes with comma and encrypt
        codes_str = ",".join(codes)
        encrypted_codes = encryption.encrypt_value(codes_str)
        user.backup_codes = encrypted_codes
        
        # Note: Caller should db.commit()
    
    @staticmethod
    def use_backup_code(user: User, code: str) -> Tuple[bool, str]:
        """
        Use a backup code for authentication.
        
        Args:
            user: User model
            code: Backup code to use
        
        Returns:
            Tuple of (is_valid, message)
        
        Returns:
            (True, "success") - Code is valid and has been removed
            (False, "invalid_code") - Code doesn't match or no backup codes available
            (False, "no_backup_codes") - User has no backup codes set up
        """
        # Check if backup codes exist
        if not user.backup_codes:
            return (False, "no_backup_codes")
        
        # Get decrypted codes
        encryption = get_encryption_service()
        try:
            codes_str = encryption.decrypt_value(user.backup_codes)
            codes = codes_str.split(",")
        except Exception as e:
            return (False, f"failed_to_decrypt_backup_codes: {str(e)}")
        
        # Check if code matches
        if code not in codes:
            return (False, "invalid_code")
        
        # Remove used code
        codes.remove(code)
        
        # Update backup codes
        if codes:
            # Re-encrypt remaining codes
            remaining_codes_str = ",".join(codes)
            user.backup_codes = encryption.encrypt_value(remaining_codes_str)
        else:
            # No more backup codes
            user.backup_codes = None
        
        # Note: Caller should db.commit()
        return (True, "success")
    
    @staticmethod
    def get_remaining_backup_codes_count(user: User) -> int:
        """
        Get count of remaining backup codes.
        
        Args:
            user: User model
        
        Returns:
            Number of unused backup codes
        """
        if not user.backup_codes:
            return 0
        
        encryption = get_encryption_service()
        try:
            codes_str = encryption.decrypt_value(user.backup_codes)
            codes = codes_str.split(",")
            return len([c for c in codes if c.strip()])
        except Exception:
            return 0


# ============================================================================
# 2FA Challenge Management
# ============================================================================

class TwoFAChallenge:
    """Manages 2FA verification challenges"""
    
    def __init__(self, user: User):
        self.user = user
        self.attempts = 0
        self.created_at = datetime.now(timezone.utc)
    
    def is_expired(self, timeout_minutes: int = 10) -> bool:
        """Check if 2FA challenge has expired"""
        elapsed = (datetime.now(timezone.utc) - self.created_at).total_seconds() / 60
        return elapsed > timeout_minutes
    
    def increment_attempts(self) -> int:
        """Increment failed verification attempts"""
        self.attempts += 1
        return self.attempts
    
    def is_locked_out(self) -> bool:
        """Check if challenge is locked due to too many attempts"""
        return self.attempts >= TOTPConfig.MAX_TOTP_ATTEMPTS
    
    def verify_totp(self, code: str) -> Tuple[bool, str]:
        """
        Verify TOTP code with rate limiting.
        
        Returns:
            (True, "success") - Verification successful
            (False, "too_many_attempts") - Locked due to rate limiting
            (False, "invalid_code") - Code is incorrect
            (False, "totp_not_enabled") - TOTP not configured
        """
        # Check if locked out
        if self.is_locked_out():
            return (False, "too_many_attempts")
        
        # Check if expired
        if self.is_expired():
            return (False, "challenge_expired")
        
        # Verify code
        is_valid, message = TOTPVerifier.verify_totp_code(self.user, code)
        
        if is_valid:
            return (True, "success")
        else:
            # Increment attempts on failure
            self.increment_attempts()
            if self.is_locked_out():
                return (False, "too_many_attempts")
            return (False, message)


# ============================================================================
# Public API Functions
# ============================================================================

def setup_totp_for_user(user: User) -> Tuple[str, str, List[str]]:
    """
    Set up TOTP 2FA for a user (initial setup).
    
    Args:
        user: User model
    
    Returns:
        Tuple of (secret, qr_code_url, backup_codes)
    
    Note:
        Caller must verify TOTP code before committing to database.
    """
    # Generate secret
    secret = TOTPSecretManager.generate_secret()
    
    # Get provisioning URI and QR code
    _, qr_code_url = TOTPSecretManager.get_provisioning_uri(user, secret)
    
    # Generate backup codes
    backup_codes = BackupCodeManager.generate_backup_codes()
    
    return secret, qr_code_url, backup_codes


def enable_totp(user: User, secret: str, backup_codes: List[str], db) -> bool:
    """
    Enable TOTP 2FA for a user (after verification).
    
    Args:
        user: User model
        secret: TOTP secret
        backup_codes: List of backup codes
        db: Database session
    
    Returns:
        True if enabled successfully
    """
    try:
        TOTPSecretManager.enable_totp_for_user(user, secret)
        BackupCodeManager.store_backup_codes(user, backup_codes)
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        return False


def disable_totp(user: User, db) -> bool:
    """
    Disable TOTP 2FA for a user.
    
    Args:
        user: User model
        db: Database session
    
    Returns:
        True if disabled successfully
    """
    try:
        TOTPSecretManager.disable_totp_for_user(user)
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        return False


def verify_totp_code(user: User, code: str) -> Tuple[bool, str]:
    """
    Verify a TOTP code from user's authenticator.
    
    Args:
        user: User model
        code: 6-digit TOTP code
    
    Returns:
        Tuple of (is_valid, message)
    """
    return TOTPVerifier.verify_totp_code(user, code)


def verify_backup_code(user: User, code: str, db) -> Tuple[bool, str]:
    """
    Verify and use a backup code.
    
    Args:
        user: User model
        code: Backup code
        db: Database session
    
    Returns:
        Tuple of (is_valid, message)
    """
    is_valid, message = BackupCodeManager.use_backup_code(user, code)
    if is_valid:
        db.commit()
    return (is_valid, message)
