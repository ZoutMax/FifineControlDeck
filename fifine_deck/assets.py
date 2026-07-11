"""Locate bundled assets (app icon, icon library)."""
from __future__ import annotations

import json
import os

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
APP_ICON = os.path.join(ASSETS_DIR, "app", "fifine-deck.png")
LIBRARY_DIR = os.path.join(ASSETS_DIR, "icons", "library")
LIBRARY_INDEX = os.path.join(LIBRARY_DIR, "index.json")


def app_icon_path() -> str:
    return APP_ICON if os.path.exists(APP_ICON) else ""


LIB_PREFIX = "lib:"


def library_ref(name: str) -> str:
    """Portable reference to a built-in icon, e.g. 'lib:mute'."""
    return f"{LIB_PREFIX}{name}" if name else ""


def library_path(name: str) -> str:
    """Absolute path of a built-in library icon by name, or '' if missing."""
    if not name:
        return ""
    p = os.path.join(LIBRARY_DIR, f"{name}.png")
    return p if os.path.exists(p) else ""


def resolve_icon(icon: str) -> str:
    """Resolve a KeyConfig.icon value to a concrete filesystem path.
    'lib:<name>' -> the bundled icon (install-independent); anything else is
    treated as a literal path."""
    if not icon:
        return ""
    if icon.startswith(LIB_PREFIX):
        return library_path(icon[len(LIB_PREFIX):])
    return icon


def is_library_icon(icon: str) -> bool:
    """True if `icon` is one of our built-in library icons (safe to auto-swap).
    Accepts the portable 'lib:' form and legacy absolute paths in LIBRARY_DIR."""
    if not icon:
        return False
    if icon.startswith(LIB_PREFIX):
        return True
    try:
        return os.path.dirname(os.path.abspath(icon)) == os.path.abspath(LIBRARY_DIR)
    except Exception:
        return False


def load_library() -> list[dict]:
    """Return [{name, file (abs path), label, category}], sorted by category."""
    if not os.path.exists(LIBRARY_INDEX):
        return []
    with open(LIBRARY_INDEX) as f:
        idx = json.load(f)
    items = []
    for name, meta in idx.items():
        items.append({
            "name": name,
            "file": os.path.join(LIBRARY_DIR, meta["file"]),
            "label": meta.get("label", name),
            "category": meta.get("category", "Other"),
        })
    items.sort(key=lambda x: (x["category"], x["label"]))
    return items
