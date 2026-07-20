"""Sandbox-compatible secret storage via the XDG Secret portal.

Inside a Flatpak sandbox the SecretService D-Bus API is unreachable without
--talk-name=org.freedesktop.secrets, a permission Flathub review rejects. The
sanctioned route is org.freedesktop.portal.Secret: the portal hands the app a
per-application MASTER SECRET (stable across runs, provisioned by the host
keyring), and the app encrypts its own secrets with a key derived from it.

This module implements that store:
- retrieve the master secret once per process (Request/Response pattern,
  mirroring app._portal_autostart, with the fd-passing the portal requires);
- derive a Fernet key from it (HKDF-SHA256, via the `cryptography` package
  that the Flatpak bundles; deb/PPA installs never need this module);
- keep encrypted values in CONFIG_DIR/secrets.enc, one Fernet token per
  secret id, written 0600 with the same fsync discipline as the config.

secret_store chooses this store only when running inside Flatpak; outside,
the keyring/SecretService path is unchanged.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

_MASTER: bytes | None = None
_MASTER_TRIED = False


def _crypto():
    """The cryptography primitives, or None when the package is absent
    (deb/PPA installs; the Flatpak bundles it). Isolated for tests."""
    try:
        from cryptography.fernet import Fernet, InvalidToken
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        return Fernet, InvalidToken, hashes, HKDF
    except Exception:
        return None


def _secrets_path() -> str:
    from .model import CONFIG_DIR
    return os.path.join(CONFIG_DIR, "secrets.enc")


def _retrieve_master_secret(timeout_ms: int = 30_000) -> Optional[bytes]:
    """Ask org.freedesktop.portal.Secret for this app's master secret.

    The portal writes the secret into a pipe we pass by fd and confirms via
    a Response signal on a Request object. Subscribe before calling: the
    spec's documented race. Needs a Qt application object (the GUI always
    has one; a bare call creates and holds a QCoreApplication)."""
    import sys

    from PyQt6.QtCore import QCoreApplication, QEventLoop, QObject, QTimer, pyqtSlot
    from PyQt6.QtDBus import (QDBusConnection, QDBusInterface, QDBusMessage,
                              QDBusUnixFileDescriptor)

    from .app import _portal_token_seq

    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication(sys.argv[:1])    # held until we return
    _ = app
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        log.warning("secret portal: no D-Bus session bus")
        return None

    token = f"fifinedeck{os.getpid()}_{next(_portal_token_seq)}"
    sender = bus.baseService().lstrip(":").replace(".", "_")
    req_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

    loop = QEventLoop()

    class _Responder(QObject):
        answered = False
        ok = False

        @pyqtSlot(QDBusMessage)
        def handle(self, msg: QDBusMessage) -> None:
            args = msg.arguments()
            self.answered = True
            self.ok = bool(args) and int(args[0]) == 0
            loop.quit()

    responder = _Responder()
    bus.connect("org.freedesktop.portal.Desktop", req_path,
                "org.freedesktop.portal.Request", "Response",
                responder.handle)
    read_fd, write_fd = os.pipe()
    try:
        iface = QDBusInterface("org.freedesktop.portal.Desktop",
                               "/org/freedesktop/portal/desktop",
                               "org.freedesktop.portal.Secret", bus)
        qfd = QDBusUnixFileDescriptor(write_fd)
        reply = iface.call("RetrieveSecret", qfd, {"handle_token": token})
        # The portal duplicated the fd during the call; close our copy NOW or
        # the pipe never reaches EOF and the read below hangs until timeout.
        os.close(write_fd)
        write_fd = -1
        if reply.errorName():
            log.warning("secret portal: %s", reply.errorMessage())
            return None

        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        if not responder.answered:
            QDBusInterface("org.freedesktop.portal.Desktop", req_path,
                           "org.freedesktop.portal.Request", bus).call("Close")
            log.warning("secret portal: no response from the portal")
            return None
        if not responder.ok:
            log.warning("secret portal: request was denied")
            return None

        # Bounded, non-blocking read. The spec says the portal writes the
        # master secret to the fd, but implementations differ on whether
        # they close their duplicate afterwards — a blocking read-to-EOF
        # hangs forever against one that does not (observed live with the
        # GNOME backend on the dev machine). So: poll with select, take
        # what arrives, and treat a quiet gap after data as completion.
        import select
        import time as _time
        os.set_blocking(read_fd, False)
        chunks: list = []
        deadline = _time.monotonic() + 10.0
        while _time.monotonic() < deadline:
            ready, _, _ = select.select([read_fd], [], [], 0.25)
            if not ready:
                if chunks:
                    break              # data arrived, then silence: done
                continue
            try:
                chunk = os.read(read_fd, 4096)
            except BlockingIOError:
                continue
            if chunk == b"":
                break                  # EOF: the portal closed its copy
            chunks.append(chunk)
        master = b"".join(chunks)
        if not master:
            log.warning("secret portal: empty master secret")
            return None
        return master
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        os.close(read_fd)
        bus.disconnect("org.freedesktop.portal.Desktop", req_path,
                       "org.freedesktop.portal.Request", "Response",
                       responder.handle)


def _master() -> Optional[bytes]:
    """The master secret, retrieved once per process. A failed retrieval is
    also cached: the portal will not start answering mid-session, and
    retrying on every keystroke would hammer D-Bus.

    THREADING: the retrieval builds Qt/D-Bus objects and runs a nested event
    loop, which is only safe on the main thread. A password key press
    dispatches on the controller's action worker thread, so retrieving there
    would at best misbehave and at worst block that serial queue for the full
    portal timeout, freezing every later action. prime() is therefore called
    once at startup from the main thread; off the main thread this only ever
    returns an already-cached value."""
    global _MASTER, _MASTER_TRIED
    if not _MASTER_TRIED:
        import threading
        if threading.current_thread() is not threading.main_thread():
            log.warning("secret portal: not primed before use off the main "
                        "thread; skipping retrieval (see prime())")
            return None
        _MASTER_TRIED = True
        try:
            _MASTER = _retrieve_master_secret()
        except Exception as e:
            log.warning("secret portal: %s", e)
            _MASTER = None
    return _MASTER


def prime() -> bool:
    """Fetch the master secret up front, on the main thread. Call once at
    startup inside Flatpak so later reads (which happen on the action worker
    thread) are pure decryption with no Qt or D-Bus involved. Returns True
    when the store is usable afterwards."""
    return _master() is not None


def _fernet():
    """A Fernet primed with the HKDF-derived key, or None."""
    crypto = _crypto()
    master = _master()
    if crypto is None or master is None:
        return None
    Fernet, _invalid, hashes, HKDF = crypto
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"fifine-control-deck",
               info=b"secrets.enc v1").derive(master)
    return Fernet(base64.urlsafe_b64encode(key))


def available() -> bool:
    """True when this process can encrypt/decrypt via the portal."""
    return _fernet() is not None


def _load() -> dict:
    try:
        with open(_secrets_path()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    """0600 + fsync-before-rename, same durability discipline as the config
    (these are the user's passwords: a torn write must never eat them)."""
    from .model import ensure_dirs
    ensure_dirs()
    path = _secrets_path()
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(fd)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)


def store(secret_id: str, password: str) -> bool:
    fernet = _fernet()
    if fernet is None:
        return False
    try:
        data = _load()
        data[secret_id] = fernet.encrypt(password.encode()).decode("ascii")
        _save(data)
        return True
    except Exception as e:
        log.warning("secret portal store failed: %s", e)
        return False


def get(secret_id: str) -> Optional[str]:
    fernet = _fernet()
    if fernet is None:
        return None
    token = _load().get(secret_id)
    if not isinstance(token, str):
        return None
    try:
        return fernet.decrypt(token.encode("ascii")).decode()
    except Exception as e:
        log.warning("secret portal get failed: %s", e)
        return None


def delete(secret_id: str) -> None:
    try:
        data = _load()
        if secret_id in data:
            del data[secret_id]
            _save(data)
    except Exception:
        pass
