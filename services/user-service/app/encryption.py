"""Field-level encryption for PII columns (full_name, phone).

Fernet is symmetric and non-deterministic (random IV per call), so encrypted
columns can't be equality-queried. Only fields never looked up by value are
encrypted here — email stays plaintext since login/uniqueness depend on it.
"""
from cryptography.fernet import Fernet

from app.config import get_settings

_fernet = Fernet(get_settings().fernet_key.encode())


def encrypt_field(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_field(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()
