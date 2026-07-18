"""End-to-end journeys against the REAL device + REAL GUI, on a sandbox config.

Run MANUALLY with the deck connected and the normal app stopped
(`fifine-control-deck --quit` first):

    python3 tools/e2e_live.py

Covers the full user journeys (drop/edit/icon provenance/monitor keys/
persistence/folders) plus the 0.7.0 monitor metrics and the 0.8.0
press-and-hold on the physical keys. Uses a throwaway XDG_CONFIG_HOME, so
the real config is never touched; deck brightness is restored at the end.
Expected output: every line PASS and "RESULT: ALL PASS"."""
import os, sys, tempfile, json
os.environ["QT_QPA_PLATFORM"] = "offscreen"
HOME = tempfile.mkdtemp(prefix="fifine-e2e-")
os.environ["XDG_CONFIG_HOME"] = HOME
import os as _os; sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication, QToolButton
app = QApplication([])
from fifine_deck.gui.style import STYLESHEET
app.setStyleSheet(STYLESHEET)
from fifine_deck.model import DeckConfig, Action, Page
from fifine_deck.controller import DeckController
from fifine_deck.gui.main_window import MainWindow
from fifine_deck.gui import widgets
from fifine_deck import assets

fails = []
def check(name, cond, extra=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' — ' + extra) if extra else ''}")
    if not cond: fails.append(name)

cfg = DeckConfig.load()
ctrl = DeckController(cfg)
ctrl.start()
win = MainWindow(cfg, ctrl)
check("device connected", ctrl.connected and bool(getattr(ctrl.device, "firmware_version", "")),
      f"fw={getattr(ctrl.device,'firmware_version',None)}")

# --- journey 1: drop an action, set label, pick icon -----------------------
win._on_action_dropped(1, "volume")
kc1 = ctrl.page().key(1)
check("drop assigns action", kc1.action.type == "volume")
check("drop assigns default icon", bool(kc1.icon), kc1.icon)
win.editor.label_edit.setText("My Volume")
check("label edit reaches model", kc1.label == "My Volume")
check("label edit kept icon", bool(kc1.icon), kc1.icon)
win.editor.icon_edit.setText(assets.library_ref("star"))
check("library pick sticks (1st)", kc1.icon == assets.library_ref("star"), kc1.icon)
check("icon pick kept label", kc1.label == "My Volume")
win.editor.icon_edit.setText(assets.library_ref("lock"))
check("library pick sticks (2nd)", kc1.icon == assets.library_ref("lock"), kc1.icon)

# --- journey 2: change the action -> icon follows --------------------------
combo = win.editor.params._params["cmd"]
combo.setCurrentIndex(combo.findText("mute"))
check("action change applied", kc1.action.params["cmd"] == "mute")

# --- journey 3: custom file icon is respected ------------------------------
custom = os.path.join(HOME, "mine.png")
open(custom, "wb").close()
win.editor.icon_edit.setText(custom)
combo.setCurrentIndex(combo.findText("down"))
check("custom icon survives action change", kc1.icon == custom, kc1.icon)

# --- journey 4: second key independent -------------------------------------
win._on_key_selected(2)
win.editor.label_edit.setText("Key Two")
kc2 = ctrl.page().key(2)
check("key 2 edited", kc2.label == "Key Two")
check("key 1 untouched by key 2 edit", kc1.label == "My Volume", kc1.label)

# --- journey 5: colours ----------------------------------------------------
win._on_key_selected(1)
win.editor.bg_btn.set_color("#123456"); win.editor.bg_btn.changed.emit("#123456")
check("bg colour reaches model", kc1.bg_color == "#123456", kc1.bg_color)
check("colour edit kept icon", kc1.icon == custom, kc1.icon)

# --- journey 6: monitor key -------------------------------------------------
win._on_action_dropped(3, "monitor")
kc3 = ctrl.page().key(3)
win.editor.params._params["metric"].setCurrentText("ram")
check("monitor metric saved", kc3.action.params.get("metric") == "ram", str(kc3.action.params))
ctrl.monitor_tick(now=1e6)
check("monitor key painted on device", 3 in ctrl.device.key_images if hasattr(ctrl.device,'key_images') else True)

# --- journey 7: persistence -------------------------------------------------
cfg.save()
reloaded = DeckConfig.load()
r1 = reloaded.active_profile().pages[0].keys.get(1)
check("persisted label", r1 and r1.label == "My Volume", r1.label if r1 else None)
check("persisted icon", r1 and r1.icon == custom, r1.icon if r1 else None)
check("persisted colour", r1 and r1.bg_color == "#123456", r1.bg_color if r1 else None)
r3 = reloaded.active_profile().pages[0].keys.get(3)
check("persisted monitor params", r3 and r3.action.params.get("metric") == "ram", str(r3.action.params) if r3 else None)

# --- journey 8: clear key ---------------------------------------------------
win._on_key_selected(1)
win.editor._clear_key()
check("clear wipes label", kc1.label == "")
check("clear wipes icon", kc1.icon == "")
check("clear wipes action", kc1.action.type == "none")

# --- journey 9: page add/switch preserves keys ------------------------------
prof = cfg.active_profile()
prof.pages.append(Page(name="P2"))
ctrl.page_index = 1
ctrl.render_page()
ctrl.page_index = 0
ctrl.render_page()
check("key 2 survived page round-trip", ctrl.page().key(2).label == "Key Two")

# --- journey 10 (0.7.0): temp / gpu / clock monitor keys --------------------
win._on_key_selected(4)
win._on_action_dropped(4, "monitor")
ed = win.editor
kc4 = ctrl.page().key(4)
def set_metric(m):
    combo = ed.params._params.get("metric")
    if combo is None or combo.findText(m) < 0:
        return False
    combo.setCurrentText(m)
    return True
check("editor offers temp metric", set_metric("temp"))
check("temp metric reached model", kc4.action.params.get("metric") == "temp", str(kc4.action.params))
ctrl.monitor_tick(now=2e6)
check("temp key painted on device", 4 in ctrl.device.key_images if hasattr(ctrl.device, "key_images") else True)
from fifine_deck import monitors as mon
spec4 = mon.MonitorSpec.from_params(kc4.action.params)
r = ctrl._sampler.last(spec4)
check("temp sample looks like celsius", r.ok and r.text.endswith("\u00b0C"), r.text)
check("editor offers clock metric", set_metric("clock"))
check("clock metric reached model", kc4.action.params.get("metric") == "clock")
ctrl.monitor_tick(now=3e6)
rc = ctrl._sampler.last(mon.MonitorSpec.from_params(kc4.action.params))
check("clock shows a time", rc.ok and ":" in rc.text, rc.text)
check("editor offers gpu metric", set_metric("gpu"))
check("gpu metric reached model", kc4.action.params.get("metric") == "gpu")
ctrl.monitor_tick(now=4e6)

# --- journey 11 (0.8.0): press-and-hold on the real deck --------------------
import time as _time
from fifine_deck.model import Action as _Action
orig_brightness = cfg.brightness
kc5 = ctrl.page().key(5)
kc5.action = _Action("brightness", {"mode": "set", "value": "30"})
kc5.hold_action = _Action("brightness", {"mode": "set", "value": "77"})
win._on_key_selected(5)
check("editor shows hold action", win.editor.hold_params.get_action(peek=True).type == "brightness")
from StreamDock.InputTypes import ButtonKey as _BK, EventType as _ET, InputEvent as _IE
def _press(state):
    ctrl._key_callback(ctrl.device, _IE(event_type=_ET.BUTTON, key=_BK(5), state=state))
_press(1)                      # hold it down...
_time.sleep(0.8)               # ...past the 0.5 s threshold
_press(0)
deadline = _time.time() + 2
while _time.time() < deadline and cfg.brightness != 77:
    _time.sleep(0.05)
check("LONG hold fired hold action on real deck", cfg.brightness == 77, f"brightness={cfg.brightness}")
_press(1)                      # quick press
_time.sleep(0.05)
_press(0)
deadline = _time.time() + 2
while _time.time() < deadline and cfg.brightness != 30:
    _time.sleep(0.05)
check("SHORT press fired primary on real deck", cfg.brightness == 30, f"brightness={cfg.brightness}")
ctrl.set_brightness(orig_brightness)    # restore the user's brightness

ctrl.stop()
print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}")
