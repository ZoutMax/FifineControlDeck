import json
import os
import sys
import tempfile
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TMP = tempfile.mkdtemp()
os.environ["HOME"] = TMP
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, ".config")
os.makedirs(os.environ["XDG_CONFIG_HOME"], exist_ok=True)

from fifine_deck import model  # noqa
model.CONFIG_DIR = os.path.join(TMP, "cfg")
model.CONFIG_PATH = os.path.join(model.CONFIG_DIR, "config.json")
model.ICONS_DIR = os.path.join(model.CONFIG_DIR, "icons")
os.makedirs(model.ICONS_DIR, exist_ok=True)

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa
app = QApplication.instance() or QApplication([])

# make every modal a no-op
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.critical = staticmethod(lambda *a, **k: None)

from fifine_deck.model import DeckConfig  # noqa
from fifine_deck.controller import DeckController  # noqa
from fifine_deck.gui.main_window import MainWindow  # noqa
from fifine_deck import actions as act_mod  # noqa
from tests._fuzz_fields import CASES, HOSTILE, base_config, setpath  # noqa

FAILS = []


class FakeCtx:
    def switch_profile(self, pid): pass
    def next_profile(self): pass
    def prev_profile(self): pass
    def goto_page(self, i): pass
    def next_page(self): pass
    def prev_page(self): pass
    def set_brightness(self, p): pass
    def adjust_brightness(self, d): pass
    def sleep_screen(self): pass


def drive(blob, path, value):
    p = os.path.join(model.CONFIG_DIR, "config.json")
    with open(p, "w") as f:
        f.write(blob)
    for stale in list(os.listdir(model.CONFIG_DIR)):
        if ".corrupt" in stale:
            os.remove(os.path.join(model.CONFIG_DIR, stale))
    stage = "load"
    try:
        cfg = DeckConfig.load(p)
        stage = "controller"
        ctl = DeckController(cfg)
        try:
            stage = "window"
            win = MainWindow(cfg, ctl)
            stage = "select key"
            win._on_key_selected(1)
            stage = "editor edit"
            win.editor._on_edit()
            stage = "drop action"
            win._on_action_dropped(1, "volume")
            stage = "select again"
            win._on_key_selected(1)
            stage = "preview"
            win._refresh_all_previews()
            stage = "monitor preview"
            win._monitor_preview(win._page().key(1), 96)
            stage = "cleartext scan"
            win._config_has_cleartext_password()
            stage = "iter cmds"
            from fifine_deck.model import iter_command_actions
            list(iter_command_actions(cfg))
            stage = "save"
            cfg.save(p)
            stage = "reload"
            DeckConfig.load(p)
            stage = "render"
            ctl.render_page()
            stage = "monitor tick"
            ctl.monitor_tick()
            stage = "execute key"
            kc = cfg.active_profile().pages[0].keys.get(1)
            if kc is not None:
                act_mod.execute(kc.action, FakeCtx())
                act_mod.execute(kc.hold_action, FakeCtx())
            stage = "execute knob"
            kn = cfg.active_profile().pages[0].knobs.get(1)
            if kn is not None:
                for s in ("press", "left", "right"):
                    act_mod.execute(getattr(kn, s), FakeCtx())
            stage = "del page/profile paths"
            win._reload_pages()
            win._reload_profiles()
            win._update_breadcrumb()
            stage = "quit"
        finally:
            try:
                ctl.stop()
            except Exception:
                pass
    except BaseException as e:
        FAILS.append((path, repr(value), stage, type(e).__name__, str(e)[:300],
                      traceback.format_exc()))
        return False
    return True


n = 0
for path in CASES:
    for value in HOSTILE:
        cfg = base_config()
        try:
            setpath(cfg, path, value)
            blob = json.dumps(cfg)
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        n += 1
        drive(blob, path, value)

print(f"ran {n} cases, {len(FAILS)} failures")
seen = set()
for path, value, stage, etype, msg, tb in FAILS:
    key = (tuple(map(str, path)), stage, etype, msg)
    if key in seen:
        continue
    seen.add(key)
    print("=" * 70)
    print("FIELD:", path, "VALUE:", value)
    print("STAGE:", stage, "->", etype, ":", msg)
