"""
Runtime controller: connects a FifineDeck to a DeckConfig and the action engine.

- Renders the active page onto the physical keys.
- Dispatches key presses to bound actions.
- Implements the ActionContext (page/profile/brightness operations).
- Handles hotplug so unplug/replug re-applies the current page.

GUI-agnostic: optional callbacks (on_connect / on_disconnect / on_key_event /
on_page_changed) let a GUI observe state without this module importing Qt.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

import os
import queue

from . import actions, monitors, rendering
from .device import DEVICE_PROFILE, FifineDeck, register
from .model import DeckConfig, Profile, Page, KeyConfig

from StreamDock.DeviceManager import DeviceManager
from StreamDock.InputTypes import EventType

log = logging.getLogger(__name__)


class DeckController:
    def __init__(self, config: DeckConfig):
        self.config = config
        self.manager: Optional[DeviceManager] = None
        self.device: Optional[FifineDeck] = None
        self.page_index = 0
        self._lock = threading.RLock()
        self._listen_thread: Optional[threading.Thread] = None
        self._running = False
        self._gif_keys: set[int] = set()   # logical keys currently animated
        self._nav: list = []               # folder navigation stack
        self._container = None             # current Folder, or None at profile root

        # Actions run on a dedicated worker thread so a slow action (e.g. a
        # multi-action with delays, or a blocking command) never stalls the
        # SDK reader thread that delivers key events.
        self._action_queue: queue.Queue = queue.Queue()
        self._action_thread = threading.Thread(target=self._action_worker, daemon=True)
        self._action_thread.start()

        # Monitor keys are sampled + repainted on their own thread, so a slow
        # metric read (a stalling disk, a sick GPU driver) can never delay
        # actions or the SDK reader.
        self._sampler = monitors.Sampler()
        self._monitor_state: dict[int, tuple] = {}  # key index -> (last_t, signature)
        self._monitor_stop = threading.Event()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

        # observer callbacks (optional)
        self.on_connect: Optional[Callable[[FifineDeck], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None
        self.on_key_event: Optional[Callable[[int, bool], None]] = None
        self.on_page_changed: Optional[Callable[[], None]] = None
        self.on_monitor_image: Optional[Callable[[int, object], None]] = None

        register()

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> bool:
        """Enumerate + open the first device, then listen for hotplug."""
        self._running = True
        self.manager = DeviceManager()
        found = self.manager.enumerate()
        opened = False
        for dev in found:
            if isinstance(dev, FifineDeck):
                if self._setup_device(dev):
                    opened = True
                    break
        self._listen_thread = threading.Thread(target=self._listen, daemon=True)
        self._listen_thread.start()
        return opened

    def try_open(self) -> bool:
        """Re-enumerate and open the deck if not already connected.

        Safe to call at runtime — e.g. right after installing the udev rule,
        where `udevadm trigger` fires a 'change' (not 'add') event that the
        hotplug listener ignores. Does not spawn another listener thread.

        If we already hold a *functional* handle (firmware read) we keep it. A
        non-functional handle — the libusb false-connect with empty firmware
        that a locked-out snap gets before the rule is installed — is dropped
        and the device re-opened fresh, so keys work immediately without a
        relaunch.
        """
        if self.device is not None and self.device.firmware_version:
            return True
        if self.device is not None:
            try:
                self.device.close()
            except Exception:
                pass
            with self._lock:
                self.device = None
        if self.manager is None:
            self.manager = DeviceManager()
        for dev in self.manager.enumerate():
            if isinstance(dev, FifineDeck) and self._setup_device(dev):
                return True
        return False

    def _listen(self):
        try:
            self.manager.listen(
                on_device_added=self._on_added,
                on_device_removed=self._on_removed,
                auto_open=False,
            )
        except Exception as e:
            log.error("hotplug listener stopped: %s", e)

    def _on_added(self, dev):
        if self._running and isinstance(dev, FifineDeck) and self.device is None:
            self._setup_device(dev)

    def _on_removed(self, dev):
        if dev is self.device:
            with self._lock:
                self.device = None
            if self.on_disconnect:
                self.on_disconnect()

    def _setup_device(self, dev: FifineDeck) -> bool:
        try:
            if not dev.open():
                log.warning("open() failed (permissions? udev rule installed?)")
                from .actions import snap_usb_hint
                hint = snap_usb_hint()
                if hint:
                    log.warning("%s", hint)
                return False
            dev.init()
            with self._lock:
                self.device = dev
                self.page_index = 0
            dev.set_key_callback(self._key_callback)
            self.apply_brightness()
            self.render_page()
            if self.on_connect:
                self.on_connect(dev)
            log.info("connected: fw=%r keys=%s", dev.firmware_version, dev.KEY_COUNT)
            return True
        except Exception as e:
            log.error("device setup failed: %s", e)
            return False

    def stop(self):
        self._running = False
        self._monitor_stop.set()
        self._action_queue.put(None)   # unblock + end the action worker
        with self._lock:
            dev = self.device
            if dev:
                try:
                    dev.set_key_callback(None)
                    time.sleep(0.05)
                    # Stop animations first so the GIF loop can't repaint the
                    # keys we are about to clear.
                    dev.stop_gif_loop()
                    self._gif_keys.clear()
                    dev.clearAllIcon()
                    dev.refresh()
                    dev.close()
                except Exception:
                    pass
            self.device = None

    @property
    def connected(self) -> bool:
        return self.device is not None

    # -- config helpers ----------------------------------------------------
    def profile(self) -> Profile:
        return self.config.active_profile()

    def container(self):
        """The page-holder currently shown: the active profile at the root, or
        a Folder when navigated into one."""
        return self._container if self._container is not None else self.config.active_profile()

    def at_root(self) -> bool:
        return not self._nav

    def page(self) -> Page:
        pages = self.container().pages
        self.page_index = max(0, min(self.page_index, len(pages) - 1))
        return pages[self.page_index]

    # -- folder navigation -------------------------------------------------
    def enter_folder(self, folder) -> None:
        if folder is None:
            return
        self._nav.append((self._container, self.page_index))
        self._container = folder
        self.page_index = 0
        self.render_page()

    def go_back(self) -> None:
        if not self._nav:
            return
        container, page_index = self._nav.pop()
        self._container = container
        self.page_index = page_index
        self.render_page()

    def reset_nav(self) -> None:
        """Return to the root of the active profile (used on profile switch)."""
        self._nav = []
        self._container = None
        self.page_index = 0

    # -- rendering ---------------------------------------------------------
    def render_key(self, index: int) -> None:
        # Hold the lock for the whole body: all app-initiated device writes and
        # _gif_keys mutations must be serialized across the GUI thread (edits)
        # and the action-worker thread (page/profile switches). RLock keeps
        # render_page -> render_key reentrant.
        with self._lock:
            dev = self.device
            if not dev:
                return
            kc = self.page().keys.get(index, KeyConfig())
            # A monitor readout replaces the key face entirely, icon included.
            is_monitor = kc.action.type == "monitor"
            is_gif = (not is_monitor
                      and kc.icon.lower().endswith(".gif") and os.path.exists(kc.icon))
            try:
                if is_gif:
                    dev.set_key_gif(index, kc.icon)
                    self._gif_keys.add(index)
                else:
                    if index in self._gif_keys:
                        dev.clear_key_gif(index)
                        self._gif_keys.discard(index)
                    if is_monitor:
                        spec = monitors.MonitorSpec.from_params(kc.action.params)
                        img = monitors.render_monitor(
                            dev.KEY_PIXEL_WIDTH, spec, self._sampler.last(spec),
                            self._sampler.history(spec), kc.bg_color, kc.text_color)
                        # force a fresh sample on the next tick so the cached
                        # value we just painted goes live quickly
                        self._monitor_state.pop(index, None)
                    else:
                        img = rendering.render_key(
                            dev.KEY_PIXEL_WIDTH, kc.label, kc.icon, kc.bg_color, kc.text_color)
                    dev.set_key_image_pil(index, img)
                self._sync_gif_loop()
            except Exception as e:
                log.error("render key %s failed: %s", index, e)

    def _sync_gif_loop(self) -> None:
        dev = self.device
        if not dev:
            return
        try:
            if self._gif_keys:
                dev.start_gif_loop()
            else:
                dev.stop_gif_loop()
        except Exception as e:
            log.error("gif loop sync failed: %s", e)

    def render_page(self) -> None:
        with self._lock:
            # A page render is always a context change (page/profile/folder
            # switch, import, reconnect): drop every monitor gate so the new
            # page's monitor keys sample + paint on the next tick instead of
            # inheriting a stale timestamp/signature from the old page.
            self._monitor_state.clear()
            dev = self.device
            if dev:
                # drop animations from the previous page before re-rendering
                for k in list(self._gif_keys):
                    try:
                        dev.clear_key_gif(k)
                    except Exception:
                        pass
                self._gif_keys.clear()
                for i in range(1, dev.KEY_COUNT + 1):
                    self.render_key(i)
                self._sync_gif_loop()
                try:
                    dev.refresh()
                except Exception as e:
                    log.error("refresh failed: %s", e)
        # Fire even with no device so the GUI resyncs (e.g. editing folders offline).
        if self.on_page_changed:
            self.on_page_changed()

    # -- monitor keys ------------------------------------------------------
    def _monitor_loop(self):
        # 0.5 s scheduler granularity. With no monitor keys on the visible
        # page a tick is a single dict scan — no metric is ever sampled.
        while not self._monitor_stop.wait(0.5):
            try:
                self.monitor_tick()
            except Exception as e:      # a tick must never kill the thread
                log.error("monitor tick failed: %s", e)

    def monitor_tick(self, now: float | None = None) -> None:
        """Sample and repaint the monitor keys of the visible page that are
        due per their refresh interval. Runs on the monitor thread; callable
        directly with a fake clock in tests."""
        now = time.monotonic() if now is None else now
        with self._lock:
            page = self.page()
            dev = self.device
            size = dev.KEY_PIXEL_WIDTH if dev else int(DEVICE_PROFILE["key_size"])
            entries = [(i, kc) for i, kc in list(page.keys.items())
                       if kc.action.type == "monitor"]
        if not entries:
            if self._monitor_state:
                self._monitor_state.clear()
            return
        live = {i for i, _ in entries}
        # Which keys are due? (last_t None = never sampled — an explicit
        # sentinel, because time.monotonic() is small right after boot and a
        # 0.0 sentinel would wrongly look "recently sampled".)
        due = []
        for index, kc in entries:
            spec = monitors.MonitorSpec.from_params(kc.action.params)
            last_t = self._monitor_state.get(index, (None, None))[0]
            if last_t is None or now - last_t >= spec.interval:
                due.append((index, kc, spec))
        # Sample each STREAM once, shared by all its keys. cpu_percent and the
        # net counters are since-last-call deltas with global state — sampling
        # once per key would hand every key after the first a garbage ~0.
        readings: dict[tuple, monitors.Reading] = {}
        for _, _, spec in due:
            if spec.key() not in readings:
                readings[spec.key()] = self._sampler.sample(spec)
        pushed = False
        for index, kc, spec in due:
            reading = readings[spec.key()]
            # Only repaint when something visible changed (graphs always move).
            sig = (spec, kc.bg_color, kc.text_color, reading.text, reading.sub,
                   None if reading.pct is None else int(reading.pct))
            last_sig = self._monitor_state.get(index, (None, None))[1]
            if sig == last_sig and spec.style != "graph":
                self._monitor_state[index] = (now, sig)
                continue
            img = monitors.render_monitor(size, spec, reading,
                                          self._sampler.history(spec),
                                          kc.bg_color, kc.text_color)
            emit = False
            with self._lock:
                dev = self.device
                cur = self.page().keys.get(index)
                # The page may have switched, or the key been retyped/cleared/
                # swapped, while we sampled outside the lock. Never paint (or
                # emit, or stamp state for) a stale frame — the state entry
                # would suppress the NEW key's first repaint for a full
                # interval, and the frame would show the wrong metric.
                if (self.page() is page and cur is kc
                        and cur.action.type == "monitor"):
                    self._monitor_state[index] = (now, sig)
                    emit = True
                    if dev and index not in self._gif_keys:
                        try:
                            dev.set_key_image_pil(index, img)
                            pushed = True
                        except Exception as e:
                            log.error("monitor key %s failed: %s", index, e)
            if emit and self.on_monitor_image:
                try:
                    self.on_monitor_image(index, img)
                except Exception as e:
                    log.error("monitor image callback failed: %s", e)
        # forget keys that stopped being monitors (cleared / retyped / swapped)
        for stale in set(self._monitor_state) - live:
            self._monitor_state.pop(stale, None)
        if pushed:
            self.refresh()

    # -- input dispatch ----------------------------------------------------
    def _enqueue(self, task):
        """Queue a 0-arg callable to run on the worker thread."""
        self._action_queue.put(task)

    def _dispatch(self, action):
        """Queue an action (or folder navigation) on the worker thread so a slow
        action can't stall the SDK reader thread."""
        if not action or action.type == "none":
            return
        if action.type == "folder_back":
            self._enqueue(self.go_back)
        else:
            self._enqueue(lambda a=action: actions.execute(a, self))

    def _action_worker(self):
        while True:
            task = self._action_queue.get()
            if task is None:
                return
            try:
                task()
            except Exception as e:   # never let the worker die
                log.error("action worker error: %s", e)

    def flash_key(self, index: int, pressed: bool) -> None:
        """Flash a key (brightened) on the device while pressed; restore it on
        release. Skips animated (GIF) keys, which the GIF loop keeps repainting."""
        if not self.config.glow:
            return
        with self._lock:
            dev = self.device
            if not dev or index in self._gif_keys:
                return
            kc = self.page().keys.get(index, KeyConfig())
            if kc.action.type == "monitor":
                # a static flash frame would overpaint the live readout until
                # the next tick; monitor keys don't react to presses anyway
                return
            try:
                img = rendering.render_key(
                    dev.KEY_PIXEL_WIDTH, kc.label, kc.icon, kc.bg_color,
                    kc.text_color, pressed=pressed)
                dev.set_key_image_pil(index, img)
                dev.refresh()
            except Exception as e:
                log.error("flash key %s failed: %s", index, e)

    def _key_callback(self, device, event):
        if event.event_type == EventType.BUTTON:
            index = int(event.key.value)
            pressed = event.state == 1
            if self.on_key_event:
                self.on_key_event(index, pressed)
            self.flash_key(index, pressed)
            if pressed:
                kc = self.page().keys.get(index)
                if kc:
                    if kc.action.type == "open_folder" and kc.folder is not None:
                        folder = kc.folder
                        self._enqueue(lambda f=folder: self.enter_folder(f))
                    else:
                        self._dispatch(kc.action)
        elif event.event_type in (EventType.KNOB_ROTATE, EventType.KNOB_PRESS):
            self._knob_event(event)

    def _knob_event(self, event):
        # knob index is device-specific; map knob_1.. to 1..
        try:
            kid = int(str(event.knob_id.value).split("_")[-1])
        except Exception:
            return
        kn = self.page().knobs.get(kid)
        if not kn:
            return
        if event.event_type == EventType.KNOB_PRESS and event.state == 1:
            self._dispatch(kn.press)
        elif event.event_type == EventType.KNOB_ROTATE:
            act = kn.right if getattr(event.direction, "value", "") == "right" else kn.left
            self._dispatch(act)

    # -- ActionContext implementation -------------------------------------
    def switch_profile(self, profile_id: str) -> None:
        if self.config.profile_by_id(profile_id):
            self.config.active_profile_id = profile_id
            self.reset_nav()
            self.render_page()

    def _rotate_profile(self, step: int) -> None:
        """Scene Shift: move to the next/previous profile (wrapping)."""
        profiles = self.config.profiles
        if len(profiles) < 2:
            return
        ids = [p.id for p in profiles]
        try:
            i = ids.index(self.config.active_profile_id)
        except ValueError:
            i = 0
        self.config.active_profile_id = ids[(i + step) % len(ids)]
        self.reset_nav()
        self.render_page()

    def next_profile(self) -> None:
        self._rotate_profile(1)

    def prev_profile(self) -> None:
        self._rotate_profile(-1)

    def sleep_screen(self) -> None:
        with self._lock:
            if self.device:
                try:
                    self.device.transport.sleep()
                except Exception as e:
                    log.error("sleep failed: %s", e)

    def goto_page(self, index: int) -> None:
        self.page_index = index
        self.render_page()

    def next_page(self) -> None:
        n = len(self.profile().pages)
        self.page_index = (self.page_index + 1) % n
        self.render_page()

    def prev_page(self) -> None:
        n = len(self.profile().pages)
        self.page_index = (self.page_index - 1) % n
        self.render_page()

    def refresh(self) -> None:
        """Push pending image changes to the device (thread-safe)."""
        with self._lock:
            if self.device:
                try:
                    self.device.refresh()
                except Exception as e:
                    log.error("refresh failed: %s", e)

    def apply_brightness(self) -> None:
        with self._lock:
            if self.device:
                try:
                    self.device.set_brightness(self.config.brightness)
                except Exception as e:
                    log.error("brightness failed: %s", e)

    def set_brightness(self, percent: int) -> None:
        self.config.brightness = max(0, min(100, int(percent)))
        self.apply_brightness()

    def adjust_brightness(self, delta: int) -> None:
        self.set_brightness(self.config.brightness + delta)
