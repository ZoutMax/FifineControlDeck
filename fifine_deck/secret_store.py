"""Secret storage for the 'type password' action.

Passwords are stored keyed by an opaque id; the config on disk keeps only
that id. The secure backend is the OS keyring (SecretService via the
`keyring` library).

If the keyring is unavailable, callers fall back to storing the value in the
config (with a warning) so the feature still works, just not secured.
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
    """True if a secure backend can store secrets."""
    return _keyring() is not None


def new_id() -> str:
    return "pw-" + uuid.uuid4().hex[:12]


def store(secret_id: str, password: str) -> bool:
    """Store `password` under `secret_id`. Returns True if kept securely."""
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


def delete(secret_id: str) -> bool:
    """Remove a secret. Returns True when it is gone (deleted, or was never
    there), False on a real failure (e.g. the keyring is locked) so the
    caller's reap can keep the id and retry on a later save — swallowing the
    failure meant a locked keyring orphaned the secret forever."""
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.delete_password(SERVICE, secret_id)
        return True
    except Exception as e:
        # "No such password" means it is already gone — that IS success.
        if type(e).__name__ == "PasswordDeleteError":
            return True
        log.warning("could not delete secret %s from the keyring: %s",
                    secret_id, e)
        return False
