"""try_open(): runtime (re)connection, driven by a mock device.

try_open() is what the GUI calls right after the udev rule is installed, so it
has to recover a deck that is present but was previously unopenable — without
a relaunch and without spawning a second listener thread.
"""
import pytest

controller = pytest.importorskip("fifine_deck.controller")

from fifine_deck.controller import DeckController          # noqa: E402
from fifine_deck.model import DeckConfig                    # noqa: E402
from tests.test_controller import MockDevice                # noqa: E402


class FakeManager:
    """Stands in for the SDK's DeviceManager; counts enumerations."""

    def __init__(self, devices):
        self.devices = list(devices)
        self.enumerated = 0

    def enumerate(self):
        self.enumerated += 1
        return list(self.devices)


@pytest.fixture
def deck(monkeypatch):
    """Factory: hand it the devices USB should report, get (controller, manager)."""
    made = []

    def factory(devices):
        mgr = FakeManager(devices)
        # try_open() filters on isinstance(dev, FifineDeck); the mock isn't a
        # real one, so point the check at the mock class instead.
        monkeypatch.setattr(controller, "FifineDeck", MockDevice)
        monkeypatch.setattr(controller, "DeviceManager", lambda: mgr)
        c = DeckController(DeckConfig())
        made.append(c)
        return c, mgr

    yield factory
    for c in made:
        c.stop()


def _false_connected():
    """A libusb false-connect: a handle that opens but reads no firmware."""
    dev = MockDevice()
    dev.firmware_version = ""
    return dev


# -- the regression this function exists for --------------------------------

def test_drops_a_false_connected_handle_and_reopens(deck):
    """The bug: a snap locked out of /dev/hidraw still gets a handle back over
    libusb, but with empty firmware — no real I/O. try_open() used to see a
    non-None device and return True, so after the user granted access the keys
    stayed dead until they relaunched the app."""
    stale, fresh = _false_connected(), MockDevice()
    c, mgr = deck([fresh])
    c.device = stale

    assert c.try_open() is True
    assert stale.closed is True            # the dud handle is released
    assert c.device is fresh               # and genuinely replaced
    assert fresh.callback is not None      # keys live again, no relaunch
    assert mgr.enumerated == 1


def test_keeps_a_working_handle_untouched(deck):
    """A handle that reads firmware is real; don't churn the device."""
    working = MockDevice()                 # firmware_version == "MOCK"
    c, mgr = deck([MockDevice()])
    assert c._setup_device(working) is True

    assert c.try_open() is True
    assert c.device is working
    assert working.closed is False
    assert mgr.enumerated == 0             # no re-enumeration at all


def test_reopens_even_if_closing_the_stale_handle_fails(deck):
    """A vanished device can throw from close(); that must not block recovery."""
    stale, fresh = _false_connected(), MockDevice()

    def boom():
        raise OSError("device vanished")
    stale.close = boom

    c, _ = deck([fresh])
    c.device = stale
    assert c.try_open() is True
    assert c.device is fresh


# -- cold open --------------------------------------------------------------

def test_opens_from_cold(deck):
    fresh = MockDevice()
    c, mgr = deck([fresh])
    assert c.device is None

    assert c.try_open() is True
    assert c.device is fresh
    assert fresh.opened is True
    assert mgr.enumerated == 1


def test_false_when_no_deck_is_present(deck):
    c, _ = deck([])
    assert c.try_open() is False
    assert c.connected is False


def test_false_when_open_is_denied(deck):
    """Before the udev rule exists, open() fails. try_open() must report that
    honestly rather than leave a half-connected controller behind."""
    class Denied(MockDevice):
        def open(self):
            return False

    c, _ = deck([Denied()])
    assert c.try_open() is False
    assert c.connected is False


def test_ignores_devices_that_are_not_decks(deck):
    """enumerate() returns every Stream-Dock-family device on the bus."""
    class SomethingElse:
        pass

    fresh = MockDevice()
    c, _ = deck([SomethingElse(), fresh])
    assert c.try_open() is True
    assert c.device is fresh


def test_does_not_spawn_a_listener_thread(deck):
    """start() owns the hotplug listener; try_open() must not start a second
    one, or every rule-install click would leak a thread."""
    c, _ = deck([MockDevice()])
    assert c.try_open() is True
    assert c._listen_thread is None
