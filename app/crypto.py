import base64
import hashlib
import logging
import re
from typing import Optional
from cryptography.fernet import Fernet
from app.config import settings

logger = logging.getLogger(__name__)


def _get_fernet() -> Fernet:
    raw_key = settings.ENCRYPTION_KEY
    if not raw_key:
        raw_key = "default-text-to-sql-encryption-key-must-change"
    
    # Ensure key is valid Fernet 32-byte base64 key
    try:
        decoded = base64.urlsafe_b64decode(raw_key.encode("utf-8"))
        if len(decoded) == 32:
            return Fernet(raw_key.encode("utf-8"))
    except Exception:
        pass

    # Derive deterministic 32-byte urlsafe base64 key using sha256
    derived_bytes = hashlib.sha256(raw_key.encode("utf-8")).digest()
    fernet_key = base64.urlsafe_b64encode(derived_bytes)
    return Fernet(fernet_key)


def encrypt_connection_string(conn_str: str) -> str:
    """
    Encrypts a database connection string using symmetric Fernet encryption.
    """
    if not conn_str:
        raise ValueError("Connection string cannot be empty.")
    fernet = _get_fernet()
    encrypted = fernet.encrypt(conn_str.strip().encode("utf-8"))
    return encrypted.decode("utf-8")


def decrypt_connection_string(encrypted_str: str) -> str:
    """
    Decrypts an encrypted database connection string.
    """
    if not encrypted_str:
        raise ValueError("Encrypted connection string cannot be empty.")
    fernet = _get_fernet()
    decrypted = fernet.decrypt(encrypted_str.strip().encode("utf-8"))
    return decrypted.decode("utf-8")


def mask_connection_string(conn_str: str) -> str:
    """
    Masks credentials in a database URI for safe logging.
    Example: 'postgresql+asyncpg://user:password@localhost:5432/mydb'
    Returns: 'postgresql+asyncpg://user:***@localhost:5432/mydb'
    """
    if not conn_str:
        return ""
    masked = re.sub(r":([^:@/]+)@", r":***@", conn_str)
    return masked
