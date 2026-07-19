"""Secret storage for the 'type password' action.

Passwords are stored keyed by an opaque id; the config on disk keeps only
that id. Two secure backends, tried in order:

1. Secret portal (Flatpak only): org.freedesktop.portal.Secret hands the app
   a master secret and values live encrypted in CONFIG_DIR/secrets.enc, the
   route Flathub review sanctions (SecretService needs a talk-name the
   review rejects). See portal_secret.
2. OS keyring (SecretService via the `keyring` library): the deb/PPA path,
   unchanged.

If neither backend is available, callers fall back to storing the value in
the config (with a warning) so the feature still works, just not secured.
GETS try both backends regardless of which one is currently preferred, so a
secret stored before an upgrade keeps resolving after one.
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


def _portal():
    """The portal-backed store when it can serve this process, else None.
    Only ever active inside Flatpak. Isolated so tests can monkeypatch it."""
    from .actions import IN_FLATPAK
    if not IN_FLATPAK:
        return None
    try:
        from . import portal_secret
        return portal_secret if portal_secret.available() else None
    except Exception:
        return None


def available() -> bool:
    """True if some secure backend can store secrets."""
    return _portal() is not None or _keyring() is not None


def new_id() -> str:
    return "pw-" + uuid.uuid4().hex[:12]


def store(secret_id: str, password: str) -> bool:
    """Store `password` under `secret_id`. Returns True if kept securely."""
    portal = _portal()
    if portal is not None and portal.store(secret_id, password):
        return True
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
    """Fetch the password for `secret_id`, or None if unavailable/missing.
    Both backends are consulted: a secret stored via the keyring before an
    upgrade (or via the portal before a backend change) must keep working."""
    portal = _portal()
    if portal is not None:
        val = portal.get(secret_id)
        if val is not None:
            return val
    kr = _keyring()
    if kr is None:
        return None
    try:
        return kr.get_password(SERVICE, secret_id)
    except Exception as e:
        log.warning("keyring get failed: %s", e)
        return None


def delete(secret_id: str) -> None:
    portal = _portal()
    if portal is not None:
        portal.delete(secret_id)
    kr = _keyring()
    if kr is None:
        return
    try:
        kr.delete_password(SERVICE, secret_id)
    except Exception:
        pass
