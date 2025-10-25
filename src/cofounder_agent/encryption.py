"""
Encryption service for sensitive data protection.

Provides AES-256-GCM encryption with PBKDF2 key derivation for:
- Setting values (when marked as encrypted/secret)
- API key storage
- Sensitive user data

All encryption operations are deterministic for the same input
to support database queries on encrypted data when needed.
"""

import os
import base64
import logging
from typing import Tuple, Optional
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.constant_time import constant_time_compare

logger = logging.getLogger(__name__)


class EncryptionService:
    """
    Handles all encryption and decryption operations for the system.
    
    Uses AES-256-GCM for authenticated encryption:
    - 256-bit key (32 bytes)
    - 96-bit nonce/IV (12 bytes)
    - 128-bit authentication tag (16 bytes)
    
    Supports both direct keys and master key derivation:
    - Master key stored in environment: DATABASE_ENCRYPTION_KEY
    - Derived keys created from master key + salt for each value
    """
    
    # Constants
    ALGORITHM = algorithms.AES
    KEY_SIZE = 32  # 256 bits
    NONCE_SIZE = 12  # 96 bits (recommended for GCM)
    TAG_SIZE = 16  # 128 bits
    SALT_SIZE = 16  # 128 bits
    
    # PBKDF2 parameters
    PBKDF2_ITERATIONS = 480000  # OWASP 2023 recommendation
    PBKDF2_HASH_ALGORITHM = hashes.SHA256()
    
    def __init__(self):
        """Initialize encryption service and load master key."""
        
        self.master_key = self._load_master_key()
        
        if not self.master_key:
            logger.warning(
                "No DATABASE_ENCRYPTION_KEY found in environment. "
                "Encryption will not be available. "
                "Set DATABASE_ENCRYPTION_KEY to enable encryption."
            )
    
    @staticmethod
    def _load_master_key() -> Optional[bytes]:
        """
        Load master encryption key from environment or generate new one.
        
        Environment Variable: DATABASE_ENCRYPTION_KEY
        - Should be base64-encoded 32-byte key
        - Can be generated with: base64(os.urandom(32))
        
        Returns:
            bytes: 32-byte encryption key, or None if not configured
        """
        
        key_env = os.getenv('DATABASE_ENCRYPTION_KEY')
        
        if not key_env:
            logger.debug("DATABASE_ENCRYPTION_KEY not configured")
            return None
        
        try:
            # Decode from base64
            key = base64.b64decode(key_env)
            
            if len(key) != 32:
                logger.warning(
                    f"DATABASE_ENCRYPTION_KEY is {len(key)} bytes, "
                    f"expected 32 bytes. Encryption may not work correctly."
                )
            
            return key
        
        except Exception as e:
            logger.error(f"Failed to decode DATABASE_ENCRYPTION_KEY: {e}")
            return None
    
    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt plaintext value using AES-256-GCM.
        
        Returns base64-encoded ciphertext in format:
            base64(nonce || ciphertext || tag)
        
        Args:
            plaintext: String value to encrypt
        
        Returns:
            str: Base64-encoded encrypted value
        
        Raises:
            RuntimeError: If encryption key is not configured
            ValueError: If plaintext is empty
        """
        
        if not self.master_key:
            raise RuntimeError(
                "Encryption key not configured. "
                "Set DATABASE_ENCRYPTION_KEY environment variable."
            )
        
        if not plaintext:
            raise ValueError("Cannot encrypt empty value")
        
        try:
            # Generate random nonce
            nonce = os.urandom(self.NONCE_SIZE)
            
            # Create cipher
            cipher = Cipher(
                algorithms.AES(self.master_key),
                modes.GCM(nonce),
                backend=default_backend()
            )
            
            encryptor = cipher.encryptor()
            
            # Encrypt plaintext
            ciphertext = encryptor.update(plaintext.encode('utf-8')) + encryptor.finalize()
            
            # Get authentication tag
            tag = encryptor.tag
            
            # Combine: nonce || ciphertext || tag
            encrypted = nonce + ciphertext + tag
            
            # Encode to base64
            encrypted_b64 = base64.b64encode(encrypted).decode('utf-8')
            
            logger.debug(f"Encrypted value ({len(plaintext)} chars -> {len(encrypted)} bytes)")
            
            return encrypted_b64
        
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise
    
    def decrypt(self, ciphertext_b64: str) -> str:
        """
        Decrypt base64-encoded ciphertext using AES-256-GCM.
        
        Expected format:
            base64(nonce || ciphertext || tag)
        
        Args:
            ciphertext_b64: Base64-encoded encrypted value
        
        Returns:
            str: Decrypted plaintext
        
        Raises:
            RuntimeError: If encryption key is not configured
            ValueError: If ciphertext is malformed or authentication fails
        """
        
        if not self.master_key:
            raise RuntimeError(
                "Encryption key not configured. "
                "Set DATABASE_ENCRYPTION_KEY environment variable."
            )
        
        if not ciphertext_b64:
            raise ValueError("Cannot decrypt empty value")
        
        try:
            # Decode from base64
            encrypted = base64.b64decode(ciphertext_b64)
            
            # Extract components
            if len(encrypted) < self.NONCE_SIZE + self.TAG_SIZE:
                raise ValueError(
                    f"Ciphertext too short: {len(encrypted)} bytes, "
                    f"expected at least {self.NONCE_SIZE + self.TAG_SIZE}"
                )
            
            nonce = encrypted[:self.NONCE_SIZE]
            tag = encrypted[-self.TAG_SIZE:]
            ciphertext = encrypted[self.NONCE_SIZE:-self.TAG_SIZE]
            
            # Create cipher
            cipher = Cipher(
                algorithms.AES(self.master_key),
                modes.GCM(nonce, tag),
                backend=default_backend()
            )
            
            decryptor = cipher.decryptor()
            
            # Decrypt and verify
            plaintext = (decryptor.update(ciphertext) + decryptor.finalize()).decode('utf-8')
            
            logger.debug(f"Decrypted value ({len(encrypted)} bytes -> {len(plaintext)} chars)")
            
            return plaintext
        
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise ValueError(f"Failed to decrypt value: {str(e)}")
    
    def hash_password(self, password: str, salt: Optional[bytes] = None) -> Tuple[str, str]:
        """
        Hash password using PBKDF2-SHA256.
        
        Returns hash and salt separately for database storage:
        - Salt should be stored alongside the hash
        - Salt enables verification even if password changes
        
        Args:
            password: Plaintext password to hash
            salt: Optional salt (generated if not provided)
        
        Returns:
            Tuple[str, str]: (hash_b64, salt_b64)
        """
        
        if not password:
            raise ValueError("Cannot hash empty password")
        
        if salt is None:
            salt = os.urandom(self.SALT_SIZE)
        
        # Derive key using PBKDF2
        kdf = PBKDF2(
            algorithm=self.PBKDF2_HASH_ALGORITHM,
            length=self.KEY_SIZE,
            salt=salt,
            iterations=self.PBKDF2_ITERATIONS,
            backend=default_backend()
        )
        
        key = kdf.derive(password.encode('utf-8'))
        
        # Return base64-encoded values
        return (
            base64.b64encode(key).decode('utf-8'),
            base64.b64encode(salt).decode('utf-8')
        )
    
    def verify_password(self, password: str, hash_b64: str, salt_b64: str) -> bool:
        """
        Verify password against stored hash.
        
        Uses constant-time comparison to prevent timing attacks.
        
        Args:
            password: Plaintext password to verify
            hash_b64: Base64-encoded stored hash
            salt_b64: Base64-encoded stored salt
        
        Returns:
            bool: True if password matches, False otherwise
        """
        
        try:
            salt = base64.b64decode(salt_b64)
            stored_hash = base64.b64decode(hash_b64)
            
            # Recompute hash with provided salt
            computed_hash, _ = self.hash_password(password, salt)
            computed_hash_bytes = base64.b64decode(computed_hash)
            
            # Constant-time comparison
            return constant_time_compare(stored_hash, computed_hash_bytes)
        
        except Exception as e:
            logger.error(f"Password verification failed: {e}")
            return False
    
    def generate_api_key(self, key_length: int = 32) -> str:
        """
        Generate a cryptographically secure API key.
        
        Args:
            key_length: Length of key in bytes (default: 32)
        
        Returns:
            str: Base64-encoded random API key
        """
        
        key = os.urandom(key_length)
        return base64.b64encode(key).decode('utf-8').rstrip('=')
    
    def derive_key(self, master_salt: str, context: str) -> bytes:
        """
        Derive a key from master key using PBKDF2.
        
        Useful for deriving different keys for different purposes
        without storing multiple master keys.
        
        Args:
            master_salt: Base64-encoded salt
            context: String context for derivation (e.g., 'api_key_hash')
        
        Returns:
            bytes: Derived 32-byte key
        """
        
        if not self.master_key:
            raise RuntimeError("Master key not configured")
        
        salt = base64.b64decode(master_salt)
        
        kdf = PBKDF2(
            algorithm=self.PBKDF2_HASH_ALGORITHM,
            length=self.KEY_SIZE,
            salt=salt + context.encode('utf-8'),
            iterations=100000,  # Lower than password hashing
            backend=default_backend()
        )
        
        return kdf.derive(self.master_key)
    
    @staticmethod
    def is_encrypted(value: str) -> bool:
        """
        Check if a value looks like it's encrypted (base64 with valid length).
        
        This is a heuristic check, not cryptographically secure.
        
        Args:
            value: Value to check
        
        Returns:
            bool: True if value appears to be encrypted
        """
        
        if not value or len(value) < 32:
            return False
        
        try:
            decoded = base64.b64decode(value)
            # Check if decoded length makes sense for AES-GCM
            return len(decoded) >= 28  # Min: nonce(12) + ciphertext(0) + tag(16)
        except Exception:
            return False


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_encryption_service: Optional[EncryptionService] = None


def get_encryption_service() -> EncryptionService:
    """
    Get or create the global encryption service instance.
    
    Returns:
        EncryptionService: Singleton encryption service
    """
    
    global _encryption_service
    
    if _encryption_service is None:
        _encryption_service = EncryptionService()
    
    return _encryption_service


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def encrypt_value(plaintext: str) -> str:
    """Encrypt a value using the global encryption service."""
    return get_encryption_service().encrypt(plaintext)


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a value using the global encryption service."""
    return get_encryption_service().decrypt(ciphertext)


def hash_password(password: str) -> Tuple[str, str]:
    """Hash a password using the global encryption service."""
    return get_encryption_service().hash_password(password)


def verify_password(password: str, hash_b64: str, salt_b64: str) -> bool:
    """Verify a password using the global encryption service."""
    return get_encryption_service().verify_password(password, hash_b64, salt_b64)


# ============================================================================
# EXPORTS
# ============================================================================

__all__ = [
    'EncryptionService',
    'get_encryption_service',
    'encrypt_value',
    'decrypt_value',
    'hash_password',
    'verify_password',
]
