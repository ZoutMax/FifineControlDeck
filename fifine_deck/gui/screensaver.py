"""Watch the desktop screen-blank / screensaver state over D-Bus, so the deck
can blank when the monitor does and light up again when it returns.

GUI-only: it needs a running Qt/D-Bus event loop, so the headless daemon does
not use it. Connecting to a service that is not present is harmless — the signal
simply never arrives — so we subscribe to both the freedesktop and GNOME
interfaces and let whichever the desktop actually emits drive it.
"""
from __future__ import annotations

import logging
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSlot
from PyQt6.QtDBus import QDBusConnection

log = logging.getLogger(__name__)

# (service, object path, interface) triples that emit ActiveChanged(bool) when
# the screen blanks (True) or wakes (False). freedesktop covers KDE and most
# others (two paths seen in the wild); GNOME emits its own.
_SERVICES = [
    ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver",
     "org.freedesktop.ScreenSaver"),
    ("org.freedesktop.ScreenSaver", "/ScreenSaver",
     "org.freedesktop.ScreenSaver"),
    ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver",
     "org.gnome.ScreenSaver"),
]


class ScreenSaverWatcher(QObject):
    """Calls on_change(active: bool) when the screen blanks / unblanks.

    Deduplicated against repeated signals (freedesktop + GNOME can both fire).
    Always listens; the caller decides whether to act on the callback, so a
    runtime on/off toggle needs no reconnection. Returns cleanly and does
    nothing when there is no session bus (headless, no desktop).
    """

    def __init__(self, on_change: Callable[[bool], None], parent=None):
        super().__init__(parent)
        self._on_change = on_change
        self._active = False
        self.connected = False
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            log.info("no D-Bus session bus; the deck will not follow the screen")
            return
        for service, path, iface in _SERVICES:
            if bus.connect(service, path, iface, "ActiveChanged",
                           self._on_active_changed):
                self.connected = True
        log.debug("screensaver watch connected=%s", self.connected)

    @pyqtSlot(bool)
    def _on_active_changed(self, active: bool):
        active = bool(active)
        if active == self._active:
            return
        self._active = active
        try:
            self._on_change(active)
        except Exception:
            log.error("screen-change handler failed", exc_info=True)
