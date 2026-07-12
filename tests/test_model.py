"""Config model: serialization round-trips, validation, corrupt-config recovery.

All persistence uses an explicit tmp path — the real user config is never touched.
"""
import os

from fifine_deck.model import Action, DeckConfig, Folder, KeyConfig


def test_action_roundtrip():
    a = Action("hotkey", {"keys": "ctrl+c"})
    assert Action.from_dict(a.to_dict()) == a
    assert Action.from_dict(None).type == "none"
    assert Action.from_dict({}).type == "none"


def test_keyconfig_is_empty():
    assert KeyConfig().is_empty()
    assert not KeyConfig(label="x").is_empty()
    assert not KeyConfig(action=Action("volume", {"cmd": "up"})).is_empty()


def test_config_roundtrip(tmp_path):
    cfg = DeckConfig(brightness=42, glow=False)
    page = cfg.profiles[0].pages[0]
    page.key(1).label = "Vol"
    page.key(1).action = Action("volume", {"cmd": "up"})
    page.key(1).bg_color = "#123456"
    p = str(tmp_path / "c.json")
    cfg.save(p)

    loaded = DeckConfig.load(p)
    assert loaded.brightness == 42
    assert loaded.glow is False
    k = loaded.profiles[0].pages[0].keys[1]
    assert k.label == "Vol"
    assert k.bg_color == "#123456"
    assert k.action.type == "volume" and k.action.params["cmd"] == "up"


def test_folder_roundtrip(tmp_path):
    cfg = DeckConfig()
    cfg.profiles[0].pages[0].key(2).folder = Folder(name="Apps")
    p = str(tmp_path / "c.json")
    cfg.save(p)
    loaded = DeckConfig.load(p)
    fld = loaded.profiles[0].pages[0].keys[2].folder
    assert fld is not None and fld.name == "Apps" and fld.pages


def test_active_profile_defaults():
    cfg = DeckConfig()
    assert cfg.active_profile_id == cfg.profiles[0].id
    assert cfg.active_profile() is cfg.profiles[0]


def test_looks_like_config():
    assert DeckConfig.looks_like_config({"profiles": [{"pages": []}]})
    assert not DeckConfig.looks_like_config("nope")
    assert not DeckConfig.looks_like_config({})
    assert not DeckConfig.looks_like_config({"profiles": []})
    assert not DeckConfig.looks_like_config({"profiles": [{"no": "pages"}]})


def test_load_missing_creates_default(tmp_path):
    p = str(tmp_path / "new.json")
    cfg = DeckConfig.load(p)
    assert os.path.exists(p)
    assert cfg.profiles


def test_load_corrupt_json_backs_up(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{ this is not valid json")
    cfg = DeckConfig.load(str(p))
    assert cfg.profiles                      # a fresh default was returned
    assert (tmp_path / "c.json.bak").exists()  # the corrupt file was preserved


def test_load_bad_types_backs_up(tmp_path):
    # valid JSON but a field that from_dict() can't coerce (int("abc")) -> recover
    p = tmp_path / "c.json"
    p.write_text('{"profiles": [{"pages": []}], "brightness": "not-an-int"}')
    cfg = DeckConfig.load(str(p))
    assert cfg.profiles
    assert (tmp_path / "c.json.bak").exists()


def test_save_is_private(tmp_path):
    p = str(tmp_path / "c.json")
    DeckConfig().save(p)
    mode = os.stat(p).st_mode & 0o777
    assert mode == 0o600
