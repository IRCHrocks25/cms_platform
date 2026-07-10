"""Fernet encryption for GHL tokens at rest.

Keyed by GHL_TOKEN_ENCRYPTION_KEY (urlsafe base64, 32 bytes). Fails closed
when the key is missing so we never silently store plaintext.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class TokenCryptoError(Exception):
    """Raised when encryption/decryption cannot proceed."""


def _fernet() -> Fernet:
    key = getattr(settings, "GHL_TOKEN_ENCRYPTION_KEY", "") or ""
    if not key:
        raise TokenCryptoError("GHL_TOKEN_ENCRYPTION_KEY is not set.")
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise TokenCryptoError(f"Invalid GHL_TOKEN_ENCRYPTION_KEY: {exc}") from exc


def encrypt_token(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise TokenCryptoError("Could not decrypt token (wrong key or corrupt data).") from exc
