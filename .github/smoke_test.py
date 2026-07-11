"""Offscreen CI smoke test: imports, config round-trip, rendering, icon
library, and GUI construction (no physical device required)."""
import os
import sys
import tempfile

sys.path.insert(0, os.getcwd())

from fifine_deck import model, actions, rendering, assets

# --- config model round-trip --------------------------------------------
cfg = model.DeckConfig()
page = cfg.active_profile().pages[0]
page.key(1).label = "Test"
page.key(1).action = model.Action("volume", {"cmd": "mute"})
path = os.path.join(tempfile.gettempdir(), "ci_cfg.json")
cfg.save(path)
reloaded = model.DeckConfig.load(path)
assert reloaded.active_profile().pages[0].key(1).label == "Test"
assert reloaded.active_profile().pages[0].key(1).action.type == "volume"

# --- rendering ------------------------------------------------------------
img = rendering.render_key(100, "Hi", "", "#123456", "#ffffff")
assert img.size == (100, 100)
jpg = rendering.to_device_jpeg(img, rotation=180)
assert jpg[:2] == b"\xff\xd8", "expected a JPEG"

# --- icon library ---------------------------------------------------------
assert len(assets.load_library()) >= 10, "icon library is missing/empty"
assert assets.app_icon_path(), "app icon is missing"

# --- action engine (harmless) --------------------------------------------
actions.execute(model.Action("none"))
print("env:", actions.environment_summary())

# --- GUI construction (offscreen) ----------------------------------------
from PyQt6.QtWidgets import QApplication
from fifine_deck.controller import DeckController
from fifine_deck.gui.main_window import MainWindow
from fifine_deck.gui.style import STYLESHEET

app = QApplication([])
app.setStyleSheet(STYLESHEET)
controller = DeckController(cfg)          # registers the device (no open)
win = MainWindow(cfg, controller)
win.show()
win._on_key_selected(1)
win._on_action_dropped(2, "media")        # exercise drag-to-key handler
app.processEvents()
assert cfg.active_profile().pages[0].key(2).action.type == "media"

print("SMOKE TEST OK")
