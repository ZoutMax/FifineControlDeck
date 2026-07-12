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
