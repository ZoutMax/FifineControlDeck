"""Portable icon references (lib:<name>) resolve independently of install path."""
import json
import os

from fifine_deck import assets


def test_library_ref():
    assert assets.library_ref("mute") == "lib:mute"
    assert assets.library_ref("") == ""


def test_resolve_icon_literal_path():
    assert assets.resolve_icon("") == ""
    assert assets.resolve_icon("/abs/path/x.png") == "/abs/path/x.png"


def test_resolve_icon_missing_lib_is_empty():
    # a lib: reference to a non-existent icon resolves to "" (not the raw ref)
    assert assets.resolve_icon("lib:__definitely_missing__") == ""


def test_is_library_icon():
    assert assets.is_library_icon("lib:mute")
    assert not assets.is_library_icon("")
    assert not assets.is_library_icon("/somewhere/else/x.png")


def test_is_library_icon_accepts_a_legacy_absolute_path():
    """Configs written before the lib: form stored absolute paths; those must
    still be recognised as ours so they can be auto-swapped."""
    legacy = os.path.join(assets.LIBRARY_DIR, "mute.png")
    assert assets.is_library_icon(legacy) is True


def test_is_library_icon_survives_a_junk_path():
    assert assets.is_library_icon("\0not/a/valid/path") is False


# -- bundled files are actually present --------------------------------------

def test_app_icon_is_bundled():
    """The .desktop entry and window icon both depend on this file shipping."""
    assert assets.app_icon_path() != ""
    assert os.path.exists(assets.app_icon_path())


def test_library_path_resolves_a_real_icon():
    p = assets.library_path("mute")
    assert p and os.path.exists(p)
    assert assets.resolve_icon("lib:mute") == p


def test_library_path_edge_cases():
    assert assets.library_path("") == ""
    assert assets.library_path("__missing__") == ""


# -- the icon library index --------------------------------------------------

def test_load_library_matches_the_shipped_icons():
    """Every index entry must point at a file that actually exists — a missing
    one renders as a blank key in the picker."""
    items = assets.load_library()
    assert items, "icon library is empty"
    for it in items:
        assert os.path.exists(it["file"]), f"{it['name']} -> {it['file']} missing"
        assert it["label"] and it["category"]


def test_load_library_is_sorted_by_category_then_label():
    items = assets.load_library()
    keys = [(i["category"], i["label"]) for i in items]
    assert keys == sorted(keys)


def test_load_library_without_an_index_returns_empty(monkeypatch, tmp_path):
    """Some install layouts may not ship the index; the picker should be empty
    rather than raise on startup."""
    monkeypatch.setattr(assets, "LIBRARY_INDEX", str(tmp_path / "nope.json"))
    assert assets.load_library() == []


def test_load_library_defaults_missing_metadata(monkeypatch, tmp_path):
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps({"solo": {"file": "solo.png"}}))
    monkeypatch.setattr(assets, "LIBRARY_INDEX", str(idx))
    monkeypatch.setattr(assets, "LIBRARY_DIR", str(tmp_path))
    item = assets.load_library()[0]
    assert item["label"] == "solo"          # falls back to the name
    assert item["category"] == "Other"
    assert item["file"] == str(tmp_path / "solo.png")
