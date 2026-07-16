"""Device profile invariants (the 293V3 key map is an involution)."""
import pytest

# The device module imports the vendored transport SDK; skip cleanly if the
# native lib can't load in this environment (the smoke test covers importability).
device = pytest.importorskip("fifine_deck.device")


def test_vid_pid():
    assert (device.VID, device.PID) == (0x3142, 0x0060)


def test_profile_geometry():
    p = device.DEVICE_PROFILE
    assert p["key_count"] == p["cols"] * p["rows"] == 15


def test_image_map_is_permutation_and_involution():
    m = device._image_map()
    assert set(m.keys()) == set(range(1, 16))
    assert set(m.values()) == set(range(1, 16))
    # applying the map twice must be the identity (that's why input decodes 1:1)
    for k, v in m.items():
        assert m[v] == k


def test_image_map_falls_back_to_identity_when_unmapped(monkeypatch):
    """A profile for a device with no known map must still address every key."""
    monkeypatch.setitem(device.DEVICE_PROFILE, "image_key_map", {})
    assert device._image_map() == {i: i for i in range(1, 16)}


# -- key addressing / input decoding -----------------------------------------

def _deck():
    """A FifineDeck carrying only the attributes the mapping logic needs.

    The real __init__ requires a live transport, and the SDK's __del__ would
    then try to tear one down at GC — hence the local subclass.
    """
    class _MapOnlyDeck(device.FifineDeck):
        def __del__(self):
            pass

    d = _MapOnlyDeck.__new__(_MapOnlyDeck)
    d.KEY_COUNT = device.DEVICE_PROFILE["key_count"]
    d._map = device._image_map()
    d._rmap = {v: k for k, v in d._map.items()}
    return d


def test_get_image_key_addresses_through_the_map():
    d = _deck()
    # top-left logical key is hardware key 11 on the 293V3 family
    assert d.get_image_key(1) == 11
    assert d.get_image_key(11) == 1
    assert d.get_image_key(6) == 6      # middle row is fixed by the map


def test_get_image_key_passes_through_unknown_keys():
    assert _deck().get_image_key(99) == 99


def test_input_decodes_identity_not_through_the_map():
    """Presses arrive in plain reading order. Running them through the image
    map — which is an involution — would double-map and swap rows 1-5 <-> 11-15,
    so pressing the top-left key would fire the bottom-left key's action."""
    from StreamDock.InputTypes import EventType

    d = _deck()
    ev = d.decode_input_event(1, 0x01)
    assert ev.event_type == EventType.BUTTON
    assert ev.key == 1                  # NOT 11
    assert ev.state == 1


def test_input_press_and_release_states():
    d = _deck()
    assert d.decode_input_event(5, 0x01).state == 1
    assert d.decode_input_event(5, 0x00).state == 0


@pytest.mark.parametrize("code", [0, 16, 99, -1])
def test_out_of_range_input_is_unknown(code):
    """The reader thread must not turn bus noise into a key action."""
    from StreamDock.InputTypes import EventType

    assert _deck().decode_input_event(code, 0x01).event_type == EventType.UNKNOWN


def test_every_key_decodes_to_itself():
    d = _deck()
    for k in range(1, device.DEVICE_PROFILE["key_count"] + 1):
        assert d.decode_input_event(k, 0x01).key == k


# -- product registration -----------------------------------------------------

def test_register_is_idempotent():
    """register() runs on every DeckController construction; it must not stack
    duplicate (VID, PID) entries in the SDK's product table."""
    from StreamDock import ProductIDs

    device.register()
    device.register()
    matches = [e for e in ProductIDs.g_products
               if e[0] == device.VID and e[1] == device.PID]
    assert len(matches) == 1
    assert matches[0][2] is device.FifineDeck
