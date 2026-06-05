import base64
import hashlib

from cryptography.fernet import Fernet

from app.core.config import get_settings


def _fernet() -> Fernet:
    key = get_settings().slack_webhook_encryption_key.encode()
    digest = hashlib.sha256(key).digest()
    fernet_key = base64.urlsafe_b64encode(digest)
    return Fernet(fernet_key)


def encrypt_text(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_text(encrypted: str) -> str:
    return _fernet().decrypt(encrypted.encode()).decode()
