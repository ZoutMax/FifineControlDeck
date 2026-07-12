"""Portable icon references (lib:<name>) resolve independently of install path."""
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
