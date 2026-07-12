"""Secret storage for the 'type password' action.

Passwords are kept in the OS keyring (via the `keyring` library / SecretService)
keyed by an opaque id; the config on disk stores only that id. If no keyring
backend is available, callers fall back to storing the value in the config
(with a warning) so the feature still works — just not secured.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

log = logging.getLogger(__name__)

SERVICE = "fifine-control-deck"


def _keyring():
    """Return the keyring module if importable, else None. Isolated so tests
    can monkeypatch it."""
    try:
        import keyring
        return keyring
    except Exception:
        return None


def available() -> bool:
    """True if a keyring backend can be used to store secrets."""
    return _keyring() is not None


def new_id() -> str:
    return "pw-" + uuid.uuid4().hex[:12]


def store(secret_id: str, password: str) -> bool:
    """Store `password` under `secret_id`. Returns True if kept in the keyring."""
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.set_password(SERVICE, secret_id, password)
        return True
    except Exception as e:
        log.warning("keyring store failed: %s", e)
        return False


def get(secret_id: str) -> Optional[str]:
    """Fetch the password for `secret_id`, or None if unavailable/missing."""
    kr = _keyring()
    if kr is None:
        return None
    try:
        return kr.get_password(SERVICE, secret_id)
    except Exception as e:
        log.warning("keyring get failed: %s", e)
        return None


def delete(secret_id: str) -> None:
    kr = _keyring()
    if kr is None:
        return
    try:
        kr.delete_password(SERVICE, secret_id)
    except Exception:
        pass
