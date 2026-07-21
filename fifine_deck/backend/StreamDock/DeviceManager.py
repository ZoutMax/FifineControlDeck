import os
import platform
import threading
import time
from typing import Callable, Optional
# import pywinusb.hid as hid
from .ProductIDs import USBVendorIDs, USBProductIDs, g_products
from .Transport.LibUSBHIDAPI import LibUSBHIDAPI

# Platform-specific imports
if platform.system() == "Linux":
    try:
        import pyudev  # optional: enables netlink-based hotplug on Linux
        PYUDEV_SUPPORT = True
    except ImportError:
        pyudev = None
        PYUDEV_SUPPORT = False
elif platform.system() == "Windows":
    try:
        import wmi
        import pythoncom
        WINDOWS_SUPPORT = True
    except ImportError:
        print("Warning: wmi module not installed, using polling mode")
        WINDOWS_SUPPORT = False
elif platform.system() == "Darwin":
    # macOS specific imports can be added here if needed
    pass

class DeviceManager:
    @staticmethod
    def _get_transport(transport):
        return LibUSBHIDAPI()

    def __init__(self, transport=None):
        self.transport = self._get_transport(transport)
        self.streamdocks = []
        self._device_lock = threading.RLock()
        self._on_device_added = None
        self._on_device_removed = None
        self._on_device_changed = None      # LOCAL PATCH: see listen()
        self._auto_open = True
        self._auto_init = False

    def enumerate(self) -> list:
        # CRITICAL: Clear old list to avoid stale references
        with self._device_lock:
            self.streamdocks.clear()

            products = g_products
            for vid, pid, class_type in products:
                found_devices = self.transport.enumerate_devices(
                    vendor_id=vid, product_id=pid
                )
                for d in found_devices:
                    self.streamdocks.append(self._create_device(class_type, d))
            return self.streamdocks

    def set_device_change_callback(
        self,
        on_device_added: Optional[Callable] = None,
        on_device_removed: Optional[Callable] = None,
    ):
        """
        Register callbacks for hotplug events.

        on_device_added(device) is called after the device is added to the manager
        and after optional auto open/init has completed.

        on_device_removed(device) is called before the device is closed and after
        it is removed from the manager.
        """
        self._on_device_added = on_device_added
        self._on_device_removed = on_device_removed

    def listen(
        self,
        on_device_added: Optional[Callable] = None,
        on_device_removed: Optional[Callable] = None,
        auto_open: bool = True,
        auto_init: bool = False,
        on_device_changed: Optional[Callable] = None,
    ):
        """
        Listen for device hotplug events, cross-platform.

        Args:
            on_device_added: Optional callback called with the new device.
            on_device_removed: Optional callback called with the removed device.
            auto_open: Open newly attached devices automatically.
            auto_init: Initialize newly attached devices after opening.
        """
        if on_device_added is not None or on_device_removed is not None:
            self.set_device_change_callback(on_device_added, on_device_removed)
        # LOCAL PATCH: fired on a udev "change" uevent, which is what
        # `udevadm trigger` emits — the command our own docs tell users to run
        # after installing the udev rule. The manager cannot fix that case
        # itself (a device that failed to open stays cached, and the add path
        # skips any path already cached), so it hands the event to the owner,
        # which can drop its dead handle and reopen. See _handle_device_event.
        self._on_device_changed = on_device_changed
        self._auto_open = auto_open
        self._auto_init = auto_init

        products = g_products
        system = platform.system()

        if system == "Linux":
            self._listen_linux(products)
        elif system == "Windows":
            self._listen_windows(products)
        elif system == "Darwin":
            self._listen_macos(products)
        else:
            print(f"Unsupported operating system: {system}")

    @staticmethod
    def _node_identity(path):
        """Identity of the device NODE behind `path`, or None if unknowable.

        LOCAL PATCH. Reconciling by path string alone cannot see a replug:
        /dev/hidrawN is reused, so after unplug-and-back the same string names a
        different physical connection. devtmpfs destroys and recreates the node,
        so (st_dev, st_ino) changes even when the name does not — which is
        exactly the signal that a cached device object is now stale.

        Serial number is no help here: this deck reports a real one, but it is
        the same device, so it is identical before and after.
        """
        try:
            st = os.stat(path)
            return (st.st_dev, st.st_ino)
        except OSError:
            return None            # unknowable -> callers fall back to path-only

    def _create_device(self, class_type, device_info):
        device_info_struct = LibUSBHIDAPI.create_device_info_from_dict(device_info)
        device_transport = LibUSBHIDAPI(device_info_struct)
        device = class_type(device_transport, device_info)
        # Stamp the node identity as seen at creation, for _remove_missing_devices.
        try:
            device._node_identity = self._node_identity(device_info.get("path", ""))
        except Exception:
            device._node_identity = None
        return device

    def _device_exists(self, device_path):
        return any(device.getPath() == device_path for device in self.streamdocks)

    def _safe_callback(self, callback, device, event_name):
        if callback is None:
            return
        try:
            callback(device)
        except Exception as e:
            print(f"{event_name} callback error: {e}", flush=True)

    def _open_hotplug_device(self, device):
        if not self._auto_open:
            return
        device.open()
        if self._auto_init:
            device.init()

    def _add_device(self, class_type, device_info):
        device_path = device_info["path"]

        with self._device_lock:
            if self._device_exists(device_path):
                return None
            new_device = self._create_device(class_type, device_info)
            self.streamdocks.append(new_device)

        try:
            self._open_hotplug_device(new_device)
        except Exception as e:
            print(
                f"[WARNING] Failed to open hotplug device {device_path}: {e}",
                flush=True,
            )

        self._safe_callback(self._on_device_added, new_device, "Device added")
        return new_device

    def _remove_device_by_path(self, device_path):
        removed_devices = []
        with self._device_lock:
            for device in list(self.streamdocks):
                if device.getPath() == device_path:
                    self.streamdocks.remove(device)
                    removed_devices.append(device)

        for device in removed_devices:
            self._safe_callback(self._on_device_removed, device, "Device removed")
            try:
                device.close(notify=False)
            except Exception as e:
                print(
                    f"[WARNING] Error closing removed device {device.getPath()}: {e}",
                    flush=True,
                )
        return removed_devices

    def _add_missing_devices(self, products):
        added_devices = []
        for vid, pid, class_type in products:
            found_devices = self.transport.enumerate_devices(vendor_id=vid, product_id=pid)
            for device_info in found_devices:
                if not self._device_exists(device_info["path"]):
                    print(f"[add] path: {device_info['path']}", flush=True)
                    added_device = self._add_device(class_type, device_info)
                    if added_device is not None:
                        added_devices.append(added_device)
        return added_devices

    def _current_device_paths(self, products):
        current_paths = set()
        for vid, pid, _ in products:
            found_devices = self.transport.enumerate_devices(vendor_id=vid, product_id=pid)
            for device_info in found_devices:
                current_paths.add(device_info["path"])
        return current_paths

    def _remove_missing_devices(self, products):
        current_paths = self._current_device_paths(products)

        with self._device_lock:
            devices_to_remove = []
            for device in self.streamdocks:
                path = device.getPath()
                if path not in current_paths:
                    devices_to_remove.append(device)
                    continue
                # LOCAL PATCH: the path being present is NOT proof this object
                # is still valid. A replug that completes before the remove
                # retries finish (~0.6 s) leaves /dev/hidrawN in place, so the
                # old comparison removed nothing, the following "add" hit the
                # _device_exists(path) guard and announced nothing, and the
                # owner kept a handle onto a torn-down node: every write
                # silently swallowed, status still "connected", no key working,
                # and no recovery short of a restart. Compare the node identity
                # so a recreated node is recognised as a different connection.
                was = getattr(device, "_node_identity", None)
                now = self._node_identity(path)
                if was is not None and now is not None and was != now:
                    print(f"[stale] {path} was recreated (node identity changed)", flush=True)
                    devices_to_remove.append(device)

        for device in devices_to_remove:
            print(f"[remove] path: {device.getPath()}", flush=True)
            self._remove_device_by_path(device.getPath())

        return devices_to_remove

    def _listen_linux(self, products):
        """Linux uses pyudev to listen for device events (falls back to polling)."""
        if not PYUDEV_SUPPORT:
            self._fallback_polling(products)
            return
        try:
            context = pyudev.Context()
            monitor = pyudev.Monitor.from_netlink(context)
            monitor.filter_by(subsystem="usb")
        except Exception as e:
            # LOCAL PATCH: these three sat outside every try, so a failure here
            # (no netlink access in a container or a confined session) escaped
            # into the caller, which logs once and lets the listener thread die
            # — hotplug then stayed dead for the whole session with no retry.
            # _fallback_polling was only reachable for ImportError before.
            print(f"[WARNING] pyudev unavailable ({e}); falling back to polling",
                  flush=True)
            self._fallback_polling(products)
            return

        # LOCAL PATCH: the safety-net rescan below is TIME-gated, not
        # idle-gated. It used to run only on the poll() timeout branch, i.e.
        # only after 60 consecutive seconds with zero USB uevents of ANY kind —
        # and the filter is subsystem-wide, so a webcam, a dock or a phone
        # re-enumerating keeps resetting that window. On a busy machine it could
        # go hours without running. That matters because the add path gives up
        # after ~3 s (10 retries at 0.2 s), and this rescan is what used to
        # catch a deck whose hidraw node was not ready inside it.
        RESCAN_INTERVAL = 60.0
        last_rescan = time.monotonic()

        while True:
            try:
                # LOCAL PATCH (fifine Control Deck, 2026-07-20): the upstream
                # SDK polls with timeout=1, so on every idle second it runs a
                # full USB HID enumeration as a safety net. Each enumeration
                # costs ~105 ms here, which measured out at ~7% of a CPU core
                # burned continuously by an otherwise idle app (4456 CPU
                # seconds over one 18-hour run).
                #
                # pyudev already delivers add/remove events, and poll() returns
                # the instant one arrives, so hotplug stays immediate. Only the
                # redundant rescan is throttled, from once a second to once a
                # minute. See docs/PROVENANCE.md.
                device = monitor.poll(timeout=RESCAN_INTERVAL)
                if device is not None:
                    self._handle_device_event(device.action, device, products)
                # Rescan on a wall-clock schedule, whether poll() timed out or
                # returned an event we ignored. See the RESCAN_INTERVAL note.
                now = time.monotonic()
                if now - last_rescan >= RESCAN_INTERVAL:
                    last_rescan = now
                    self._remove_missing_devices(products)
                    self._add_missing_devices(products)
            except Exception as e:
                print(f"Linux device listener error: {e}", flush=True)

    def _listen_windows(self, products):
        """Windows uses WMI to listen for device events"""
        if not WINDOWS_SUPPORT:
            print("WMI unavailable, using polling mode")
            self._fallback_polling(products)
            return

        try:
            pythoncom.CoInitialize()
            c = wmi.WMI()
            watcher = c.Win32_DeviceChangeEvent.watch_for()

            while True:
                try:
                    event = watcher()
                    if event.EventType == 2:  # Device connected
                        self._check_new_devices_windows(products)
                    elif event.EventType == 3:  # Device disconnected
                        self._check_removed_devices_windows(products)
                except Exception as e:
                    print(f"Windows device listener error: {e}")
                    time.sleep(1)
        except Exception as e:
            print(f"Windows WMI initialization failed: {e}")
            self._fallback_polling(products)
        finally:
            pythoncom.CoUninitialize()

    def _listen_macos(self, products):
        """macOS uses polling to listen for device events"""
        self._fallback_polling(products)

    def _fallback_polling(self, products):
        """Fall back to polling mode for systems without real-time monitoring"""
        current_devices = self._current_device_paths(products)

        while True:
            try:
                new_devices = self._current_device_paths(products)

                added_devices = new_devices - current_devices
                for device_path in added_devices:
                    print(f"[add] path: {device_path}", flush=True)
                    self._handle_device_addition(device_path, products)

                removed_devices = current_devices - new_devices
                for device_path in removed_devices:
                    print(f"[remove] path: {device_path}", flush=True)
                    self._remove_device_by_path(device_path)

                current_devices = new_devices
                time.sleep(2)
            except Exception as e:
                print(f"Polling listener error: {e}")
                time.sleep(5)

    def _handle_device_event(self, action, device, products):
        """Handle device events (Linux)"""
        # LOCAL PATCH: "change" used to be dropped here, which is exactly the
        # action `udevadm trigger` emits — the command the README and our own
        # snap hint tell the user to run after installing the udev rule. So the
        # documented fix for "no device access" could never take effect in a
        # running app: the failed device stays cached in streamdocks, the add
        # path skips any path already there, and the only other reconnect route
        # (try_open) is reachable solely from the snap hint, which returns None
        # outside a snap. A .deb or source user had no in-app recovery at all.
        #
        # Treat it as a reconciliation trigger: drop anything that is no longer
        # usable, then re-add. Cheap, and only on a real uevent.
        if action == "change":
            self._safe_callback(self._on_device_changed, device, "device changed")
            return

        if action not in ["add", "remove"]:
            return

        if action == "remove":
            removed_devices = []
            for _ in range(3):
                removed_devices = self._remove_missing_devices(products)
                if removed_devices:
                    break
                time.sleep(0.2)
            # LOCAL PATCH: re-add immediately after a removal. On a fast replug
            # the "add" uevent can arrive while the stale entry is still cached,
            # so it gets skipped by the _device_exists guard; without this the
            # deck then waits for the next add event or the 60 s rescan even
            # though it is plugged in right now.
            if removed_devices:
                self._add_missing_devices(products)
            return

        for _ in range(10):
            added_devices = self._add_missing_devices(products)
            if added_devices:
                break
            time.sleep(0.2)

    def _check_new_devices_windows(self, products):
        """Check for new devices on Windows"""
        self._add_missing_devices(products)

    def _check_removed_devices_windows(self, products):
        """Check for removed devices on Windows"""
        self._remove_missing_devices(products)

    def _handle_device_addition(self, device_path, products):
        """Handle device addition events (polling mode)"""
        for vid, pid, class_type in products:
            found_devices = self.transport.enumerate_devices(vendor_id=vid, product_id=pid)
            for device_info in found_devices:
                if device_info["path"] == device_path:
                    self._add_device(class_type, device_info)
                    return

