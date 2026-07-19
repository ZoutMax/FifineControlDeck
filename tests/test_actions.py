"""Action engine: catalog integrity, icon mapping, hotkey parsing, sandbox routing."""
import pytest

from fifine_deck import actions
from fifine_deck.model import Action


def test_catalog_types_are_known():
    catalog = {t for _, types in actions.ACTION_CATALOG for t in types}
    assert catalog <= set(actions.ACTION_TYPES)
    assert "multi" in actions.ACTION_TYPES


def test_default_icon_for_every_type():
    for t in actions.ACTION_TYPES:
        icon, label = actions.default_icon_for(Action(t, {}))
        assert isinstance(icon, str) and isinstance(label, str)


def test_default_icon_subcommand_variants():
    assert actions.default_icon_for(Action("volume", {"cmd": "down"}))[0] == "volume_down"
    assert actions.default_icon_for(Action("volume", {"cmd": "mute"}))[0] == "mute"
    assert actions.default_icon_for(Action("media", {"cmd": "next"}))[0] == "next"
    assert actions.default_icon_for(Action("brightness", {"mode": "down"}))[0] == "brightness_down"


def test_ydotool_keycodes():
    assert actions._ydotool_keycodes("ctrl+shift+m") == [29, 42, 50]
    assert actions._ydotool_keycodes("a") == [30]
    assert actions._ydotool_keycodes("totallyboguskey") is None


def test_host_noop_outside_flatpak(monkeypatch):
    monkeypatch.setattr(actions, "IN_FLATPAK", False)
    assert actions._host(["ydotool", "key"]) == ["ydotool", "key"]


def test_host_wraps_in_flatpak(monkeypatch):
    monkeypatch.setattr(actions, "IN_FLATPAK", True)
    monkeypatch.setattr(actions, "_host_access", True)   # grant present
    assert actions._host(["ydotool"]) == ["flatpak-spawn", "--host", "ydotool"]


def test_environment_summary_shape():
    s = actions.environment_summary()
    assert "session=" in s and "audio=" in s and "keytool=" in s


def test_execute_none_is_safe():
    # a 'none' action must be a no-op and never raise
    actions.execute(Action("none", {}))


# ---------------------------------------------------------------------------
# 0.9.0: portals-first Flatpak — host access is an explicit user grant
# ---------------------------------------------------------------------------
def test_host_access_outside_flatpak_is_free(monkeypatch):
    monkeypatch.setattr(actions, "IN_FLATPAK", False)
    monkeypatch.setattr(actions, "_host_access", None)
    monkeypatch.setattr(actions.subprocess, "run",
                        lambda *a, **k: pytest.fail("must not probe outside flatpak"))
    assert actions.host_access_available() is True
    assert actions._host(["echo", "x"]) == ["echo", "x"]


def test_missing_host_grant_is_detected_once_and_explained(monkeypatch):
    """Without --talk-name=org.freedesktop.Flatpak the spawn probe fails: the
    result is cached (one probe per process) and every host-side path gives
    the exact enable-me instruction instead of failing mutely."""
    calls = []

    class _R:
        returncode = 1

    monkeypatch.setattr(actions, "IN_FLATPAK", True)
    monkeypatch.setattr(actions, "_host_access", None)
    monkeypatch.setattr(actions.subprocess, "run",
                        lambda *a, **k: (calls.append(a), _R())[1])
    assert actions.host_access_available() is False
    assert actions.host_access_available() is False
    assert len(calls) == 1                          # probed once, cached
    assert actions._has("playerctl") is False       # no extra probe storm
    assert len(calls) == 1
    with pytest.raises(RuntimeError) as e:
        actions._host(["true"])
    assert "flatpak override" in str(e.value)       # the exact fix, verbatim
    with pytest.raises(RuntimeError):
        actions._popen_detached("echo hi", shell=True, host=True)


def test_granted_host_access_prefixes_spawn(monkeypatch):
    class _R:
        returncode = 0

    monkeypatch.setattr(actions, "IN_FLATPAK", True)
    monkeypatch.setattr(actions, "_host_access", None)
    monkeypatch.setattr(actions.subprocess, "run", lambda *a, **k: _R())
    assert actions.host_access_available() is True
    assert actions._host(["true"]) == ["flatpak-spawn", "--host", "true"]
