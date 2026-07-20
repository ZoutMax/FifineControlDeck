"""Action engine: catalog integrity, icon mapping, hotkey parsing."""
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



def test_environment_summary_shape():
    s = actions.environment_summary()
    assert "session=" in s and "audio=" in s and "keytool=" in s


def test_execute_none_is_safe():
    # a 'none' action must be a no-op and never raise
    actions.execute(Action("none", {}))
