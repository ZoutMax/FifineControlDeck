"""Shutdown safety in the vendored StreamDock SDK.

Two defects from the pre-0.10.0 device audit, both on the close path:

  * GifController.close() joined its worker with a timeout and discarded the
    result, and StreamDock.close() then destroyed the transport gated only on
    the READ thread. That worker writes to the transport outside every lock, so
    a worker still inside a native write had its handle freed underneath it —
    a use-after-free in C, i.e. the process dies instead of disconnecting.

  * _heartbeat_worker slept in 10 s blocks, so clearing run_heartbeat_thread was
    not noticed for up to ten seconds and the join(timeout=2.0) in close()
    therefore always timed out. That is a guaranteed 2 s freeze on the Qt thread
    for every quit, and 2 s of the udev listener blocked on every unplug.

These drive the real SDK classes with fake transports; no device is touched.
"""
import threading
import time

import pytest

sd = pytest.importorskip("fifine_deck.backend.StreamDock.Devices.StreamDock")
gc_mod = pytest.importorskip("fifine_deck.backend.StreamDock.Devices.GifController")


class _FakeTransport:
    """Records whether the handle was destroyed, and can block a caller."""

    def __init__(self, block_writes=None):
        self.destroyed = False
        self.destroyed_at = None
        self.heartbeats = 0
        self._block = block_writes          # an Event to hold writers in, or None

    def close(self):
        self.destroyed = True
        self.destroyed_at = time.monotonic()

    def heartbeat(self):
        self.heartbeats += 1

    def disconnected(self):
        pass

    def set_key_image_stream(self, *a, **k):
        if self._block is not None:
            # Stand in for libusb blocking until its transfer times out.
            self._block.wait(5.0)
        if self.destroyed:
            raise AssertionError("write reached a DESTROYED transport (use-after-free)")


class _ConcreteDock(sd.StreamDock):
    """StreamDock is abstract; close() lives on the base and needs none of these."""

    def decode_input_event(self, *a, **k):
        return {}

    def get_image_key(self, *a, **k):
        return None

    def set_brightness(self, *a, **k):
        pass

    def set_device(self, *a, **k):
        pass

    def set_key_image(self, *a, **k):
        pass

    def set_touchscreen_image(self, *a, **k):
        pass


def _bare_dock(transport):
    """A StreamDock with our fake transport, without running __init__ (which
    would open a device). Only the attributes close() touches are set up."""
    dock = object.__new__(_ConcreteDock)
    dock.transport = transport
    dock.path = "/dev/hidraw-test"
    dock._callback_lock = threading.Lock()
    dock.key_callback = None
    dock.raw_read_callback = None
    dock.touchscreen_callback = None
    dock._notify_on_close = False
    dock.read_thread = None
    dock.run_read_thread = False
    dock.heartbeat_thread = None
    dock.run_heartbeat_thread = False
    dock._heartbeat_stop = threading.Event()
    dock.gif_controller = _CleanGif()       # tests override where it matters
    return dock


class _StuckGif:
    """A GIF controller whose worker refuses to stop in time."""

    def __init__(self):
        self.close_calls = 0

    def close(self, timeout=2.0):
        self.close_calls += 1
        return False                        # "still running"


class _CleanGif:
    def close(self, timeout=2.0):
        return True


# -- the use-after-free ------------------------------------------------------

def test_transport_is_not_destroyed_while_the_gif_worker_is_still_writing():
    """The whole bug in one assertion: a GIF worker that did not stop must not
    have its transport freed underneath it."""
    tr = _FakeTransport()
    dock = _bare_dock(tr)
    dock.gif_controller = _StuckGif()

    dock.close(notify=False)

    assert not tr.destroyed, (
        "transport_destroy ran while the GIF worker was still inside a native "
        "write — this is the use-after-free")


def test_transport_is_destroyed_when_every_worker_has_stopped():
    """Deferring must be conditional, or the handle leaks on every clean close."""
    tr = _FakeTransport()
    dock = _bare_dock(tr)
    dock.gif_controller = _CleanGif()

    dock.close(notify=False)

    assert tr.destroyed, "clean shutdown failed to release the device"


def test_a_live_heartbeat_thread_also_defers_the_destroy():
    """The heartbeat calls transport.heartbeat(); it is on the handle too."""
    tr = _FakeTransport()
    dock = _bare_dock(tr)
    dock.gif_controller = _CleanGif()

    stuck = threading.Event()
    t = threading.Thread(target=lambda: stuck.wait(10), daemon=True)
    t.start()
    dock.heartbeat_thread = t
    try:
        dock.close(notify=False)
        assert not tr.destroyed, "destroy ran with the heartbeat thread still live"
    finally:
        stuck.set()
        t.join(timeout=5)


def test_gif_close_reports_whether_the_worker_actually_exited():
    """The signal StreamDock.close depends on, checked on the real class."""
    blocker = threading.Event()
    tr = _FakeTransport(block_writes=blocker)

    ctl = object.__new__(gc_mod.GifController)
    ctl._running = True
    ctl._loop_enabled = False
    ctl._wake_event = threading.Event()
    ctl._lock = threading.Lock()
    ctl._gif_map = {}
    # a worker wedged exactly like one blocked in libusb
    ctl._thread = threading.Thread(target=lambda: blocker.wait(10), daemon=True)
    ctl._thread.start()
    try:
        assert ctl.close(timeout=0.2) is False, "a stuck worker reported as stopped"
    finally:
        blocker.set()
        ctl._thread.join(timeout=5)

    assert ctl.close(timeout=0.2) is True, "a stopped worker reported as stuck"


# -- the 2 second quit freeze ------------------------------------------------

def test_close_does_not_block_on_the_heartbeats_ten_second_sleep():
    """Before the fix this took the full 2 s join timeout every single time,
    because the worker was parked in time.sleep(10) and never saw the flag."""
    tr = _FakeTransport()
    dock = _bare_dock(tr)
    dock.gif_controller = _CleanGif()

    dock.run_heartbeat_thread = True
    dock._heartbeat_stop.clear()
    t = threading.Thread(target=sd.StreamDock._heartbeat_worker, args=(dock,),
                         daemon=True)
    dock.heartbeat_thread = t
    t.start()
    # Wait past the 1.0 s settling delay on purpose. Stopping during the settle
    # would exit quickly even on the old code, which made this pass by luck; the
    # bug only shows once the worker is parked in the long inter-beat wait.
    time.sleep(1.3)
    assert tr.heartbeats >= 1, "worker never got past its settling delay"

    started = time.monotonic()
    dock.close(notify=False)
    elapsed = time.monotonic() - started

    assert not t.is_alive(), "heartbeat thread outlived close()"
    assert elapsed < 1.0, f"close() blocked {elapsed:.2f}s waiting on the heartbeat"
    assert tr.destroyed, "a promptly-stopped heartbeat should not defer the destroy"


def test_heartbeat_wakes_immediately_rather_than_sleeping_out_its_interval():
    """Directly: setting the stop Event must end the worker at once, not in 10 s."""
    tr = _FakeTransport()
    dock = _bare_dock(tr)
    dock.run_heartbeat_thread = True
    dock._heartbeat_stop.clear()
    t = threading.Thread(target=sd.StreamDock._heartbeat_worker, args=(dock,),
                         daemon=True)
    t.start()
    time.sleep(0.05)

    started = time.monotonic()
    dock.run_heartbeat_thread = False
    dock._heartbeat_stop.set()
    t.join(timeout=3.0)
    elapsed = time.monotonic() - started

    assert not t.is_alive(), "worker ignored the stop event"
    assert elapsed < 0.5, f"worker took {elapsed:.2f}s to notice the stop"


def test_the_initial_settling_delay_is_interruptible_too():
    """close() during the first second must not wait that second out."""
    tr = _FakeTransport()
    dock = _bare_dock(tr)
    dock.run_heartbeat_thread = True
    dock._heartbeat_stop.clear()
    t = threading.Thread(target=sd.StreamDock._heartbeat_worker, args=(dock,),
                         daemon=True)
    t.start()

    started = time.monotonic()
    dock._heartbeat_stop.set()              # while still in the 1.0 s settle
    t.join(timeout=3.0)
    elapsed = time.monotonic() - started

    assert not t.is_alive()
    assert elapsed < 0.5, f"settling delay was not interruptible ({elapsed:.2f}s)"
    assert tr.heartbeats == 0, "a heartbeat was sent after the stop was requested"


# -- hotplug reconciliation --------------------------------------------------

def _device_manager_module():
    """The SDK is imported as a top-level `StreamDock` package, and it is
    fifine_deck.controller that puts it on sys.path — importing the vendored
    path directly raises ModuleNotFoundError and silently skipped these tests."""
    pytest.importorskip("fifine_deck.controller")
    return pytest.importorskip("StreamDock.DeviceManager")


def test_a_change_uevent_is_handed_to_the_owner():
    """0.10.2 audit: `udevadm trigger` — the command our own docs tell users to
    run after installing the udev rule — emits "change", which the handler
    dropped. A device that had failed to open stayed cached forever (the add
    path skips any path already held), so the documented fix could not take
    effect without restarting or physically replugging."""
    dm = _device_manager_module()
    mgr = object.__new__(dm.DeviceManager)
    seen = []
    mgr._on_device_changed = lambda d: seen.append(d)
    mgr._on_device_added = None
    mgr._on_device_removed = None

    mgr._handle_device_event("change", "the-device", [])

    assert seen == ["the-device"], "a change uevent was dropped on the floor"


def test_an_unknown_action_is_still_ignored():
    """The change branch must not turn into a catch-all."""
    dm = _device_manager_module()
    mgr = object.__new__(dm.DeviceManager)
    seen = []
    mgr._on_device_changed = lambda d: seen.append(d)
    mgr._handle_device_event("bind", "d", [])
    mgr._handle_device_event("unbind", "d", [])
    assert seen == []


def test_controller_reopens_on_change_only_when_it_needs_to():
    """try_open is the documented recovery, and is a no-op when a working
    handle is already held — so reacting to every change event is cheap."""
    from fifine_deck.controller import DeckController
    from fifine_deck.model import DeckConfig
    from tests.test_controller import MockDevice

    c = DeckController(DeckConfig())
    c._running = True
    calls = []
    c.try_open = lambda: (calls.append(1), True)[1]

    c.device = None                       # nothing open -> must attempt
    c._on_changed()
    assert len(calls) == 1

    c.device = MockDevice()               # working handle -> must not churn
    c._on_changed()
    assert len(calls) == 1, "reopened a device that was already working"

    dead = MockDevice()
    dead.firmware_version = ""            # false-connect -> must attempt
    c.device = dead
    c._on_changed()
    assert len(calls) == 2


def test_the_rescan_is_time_gated_not_idle_gated():
    """0.10.2 audit: the safety-net rescan ran only on the poll() timeout
    branch, i.e. only after 60 consecutive seconds with zero USB uevents of any
    kind. The filter is subsystem-wide, so a webcam or dock re-enumerating kept
    resetting that window and the rescan could go hours without running."""
    import inspect
    dm = _device_manager_module()
    src = inspect.getsource(dm.DeviceManager._listen_linux)
    assert "last_rescan" in src, "no wall-clock rescan schedule"
    assert "time.monotonic()" in src, "rescan schedule is not monotonic"
    # the rescan must NOT be inside an `if device is None` branch any more
    assert "if device is None:\n                    self._remove_missing_devices" not in src


def test_pyudev_setup_failure_falls_back_instead_of_killing_the_thread():
    """Context()/from_netlink() sat outside every try, so a failure there (no
    netlink in a container or confined session) killed the listener thread and
    left hotplug dead for the session with no retry."""
    import inspect
    dm = _device_manager_module()
    src = inspect.getsource(dm.DeviceManager._listen_linux)
    head = src[:src.index("while True")]
    assert "try:" in head and "_fallback_polling" in head, (
        "pyudev setup is still unguarded")
