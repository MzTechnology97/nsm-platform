import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import settings


def _key(material: str) -> Fernet:
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def previous_keys() -> list[str]:
    """Retired master keys still accepted for decryption (``ENCRYPTION_PREVIOUS_KEYS``, comma separated)."""
    raw = os.getenv("ENCRYPTION_PREVIOUS_KEYS", "")
    return [item.strip() for item in raw.split(",") if item.strip() and item.strip() != settings.encryption_master_key]


def _fernet() -> Fernet:
    return _key(settings.encryption_master_key)


def _multi() -> MultiFernet:
    # Encrypts with the current key; decrypts with the current key or any previous one.
    return MultiFernet([_fernet(), *(_key(item) for item in previous_keys())])


def encrypt_text(value: str) -> str:
    if not value:
        raise ValueError("Cannot encrypt an empty secret")
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_text(value: str) -> str:
    if not value:
        raise ValueError("Encrypted secret is empty")
    try:
        return _multi().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise ValueError("Unable to decrypt stored secret") from exc


def key_state(value: str) -> str:
    """``current``, ``previous`` (readable only with a retired key) or ``unreadable``."""
    try:
        _fernet().decrypt(value.encode("ascii"))
        return "current"
    except (InvalidToken, UnicodeError, ValueError):
        pass
    for item in previous_keys():
        try:
            _key(item).decrypt(value.encode("ascii"))
            return "previous"
        except (InvalidToken, UnicodeError, ValueError):
            continue
    return "unreadable"


def reencrypt(value: str) -> str:
    """The same secret encrypted with the current key (raises ValueError when unreadable)."""
    try:
        return _multi().rotate(value.encode("ascii")).decode("ascii")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise ValueError("Unable to decrypt stored secret") from exc
