import copy
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

import logging  # noqa
logging.disable(logging.CRITICAL)

from PyQt6.QtWidgets import QApplication, QMessageBox, QFileDialog  # noqa
app = QApplication.instance() or QApplication([])
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.critical = staticmethod(lambda *a, **k: None)

from fifine_deck.model import DeckConfig, iter_command_actions, iter_config_secret_ids  # noqa
from fifine_deck.controller import DeckController  # noqa
from fifine_deck.gui.main_window import MainWindow  # noqa
from fifine_deck import actions as act_mod  # noqa
from fifine_deck.actions import ACTION_TYPES  # noqa

HOSTILE = [None, True, 0, -1, 10 ** 30, 1.5, float("inf"), "", " ", "\n",
           "a\nb", "\x00", "x" * 3000, [], [1], {}, {"a": 1}, "1e999", "-5",
           "#-1-1-1"]

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


def make(action, hold=None, folder=None, knob_action=None, extra_keys=None):
    keys = {"1": {"label": "L", "icon": "", "bg_color": "#101020",
                  "text_color": "#ffffff", "action": action,
                  "hold_action": hold or {"type": "none", "params": {}}}}
    if folder is not None:
        keys["1"]["folder"] = folder
    if extra_keys:
        keys.update(extra_keys)
    return {
        "version": 1, "brightness": 80, "glow": True,
        "active_profile_id": "p1",
        "profiles": [{"name": "P", "id": "p1", "wm_class": "", "pages": [{
            "name": "Main", "id": "pg1", "keys": keys,
            "knobs": {"1": {"label": "K",
                            "press": knob_action or {"type": "none", "params": {}},
                            "left": {"type": "none", "params": {}},
                            "right": {"type": "none", "params": {}}}},
        }]}],
    }


def drive(cfgdict, tag):
    p = model.CONFIG_PATH
    try:
        blob = json.dumps(cfgdict)
    except (TypeError, ValueError):
        return
    with open(p, "w") as f:
        f.write(blob)
    for stale in list(os.listdir(model.CONFIG_DIR)):
        if ".corrupt" in stale or ".bak" in stale:
            os.remove(os.path.join(model.CONFIG_DIR, stale))
    stage = "load"
    ctl = None
    try:
        cfg = DeckConfig.load(p)
        if os.path.exists(p + ".corrupt"):
            return  # quarantined by design
        stage = "controller"
        ctl = DeckController(cfg)
        stage = "window"
        win = MainWindow(cfg, ctl)
        stage = "select"
        win._on_key_selected(1)
        stage = "edit"
        win.editor._on_edit()
        stage = "hold edit"
        win.editor.hold_params.get_action()
        stage = "open folder"
        win._on_open_folder(1)
        stage = "reload after folder"
        win._reload_pages(); win._update_breadcrumb(); win._refresh_all_previews()
        stage = "back"
        win._folder_back()
        stage = "drop"
        win._on_action_dropped(1, "multi")
        win._on_key_selected(1)
        stage = "key move"
        win._on_key_moved(1, 2)
        stage = "clear key"
        win._on_key_selected(2)
        win.editor._clear_key()
        stage = "add page"
        win._add_page()
        stage = "del page"
        win._del_page()
        stage = "secrets"
        set(iter_config_secret_ids(cfg))
        list(iter_command_actions(cfg))
        win._config_has_cleartext_password()
        stage = "export"
        exp = os.path.join(TMP, "exp.json")
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (exp, ""))
        win._export_config()
        stage = "import"
        QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (exp, ""))
        win._import_config()
        stage = "save/reload"
        cfg.save(p)
        DeckConfig.load(p)
        stage = "render"
        ctl.render_page(); ctl.monitor_tick()
        stage = "execute"
        for pr in cfg.profiles:
            for pg in pr.pages:
                for kc in pg.keys.values():
                    act_mod.execute(kc.action, FakeCtx())
                    act_mod.execute(kc.hold_action, FakeCtx())
                for kn in pg.knobs.values():
                    for s in ("press", "left", "right"):
                        act_mod.execute(getattr(kn, s), FakeCtx())
        stage = "reap"
        win._reap_orphan_secrets()
        stage = "quit"
        win._quit()
    except BaseException as e:
        FAILS.append((tag, stage, type(e).__name__, str(e)[:200],
                      traceback.format_exc()))
    finally:
        if ctl is not None:
            try:
                ctl.stop()
            except Exception:
                pass


n = 0
# 1. every action type with default params, as action / hold / knob / step
for t in ACTION_TYPES:
    for slot in ("action", "hold", "knob"):
        a = {"type": t, "params": {}}
        if slot == "action":
            drive(make(a), f"type={t} slot=action")
        elif slot == "hold":
            drive(make({"type": "none", "params": {}}, hold=a), f"type={t} slot=hold")
        else:
            drive(make({"type": "none", "params": {}}, knob_action=a),
                  f"type={t} slot=knob")
        n += 1

# 2. every action type x every declared param x hostile value
for t, meta in ACTION_TYPES.items():
    for key, kind, _lbl in meta["params"]:
        for v in HOSTILE:
            drive(make({"type": t, "params": {key: v}}),
                  f"type={t} param={key}={v!r}")
            n += 1

# 3. multi steps hostile shapes
STEPS = [
    None, 1, "abc", {}, {"a": 1}, [], [None], [1], ["x"], [[]],
    [{"action": None, "delay": 1}],
    [{"action": {"type": "text", "params": {"text": None}}, "delay": "x"}],
    [{"action": {"type": "multi", "params": {"steps": [{"action": {"type": "run_command", "params": {"command": ["ls"]}}}]}}}],
    [{"type": "password", "params": {"secret_id": []}}],
    [{"type": "password", "params": {"password": 12}}],
    [{"action": {"type": "monitor", "params": {"metric": []}}, "delay": float("inf")}],
    [{"action": {"type": "goto_page", "params": {"page": {}}}, "delay": -5}],
]
for s in STEPS:
    drive(make({"type": "multi", "params": {"steps": s}}), f"steps={s!r}"[:80])
    n += 1

# 4. folder shapes
FOLDERS = [
    {"name": None, "id": [], "pages": []},
    {"name": "F", "id": "f1", "pages": [{"name": 1, "id": None, "keys": {},
                                          "knobs": {}}]},
    {"name": "F", "id": "f1", "pages": [{"keys": {"1": {"action": {"type": "open_folder", "params": {}},
                                                         "folder": {"name": "G", "pages": [{"keys": {}}]}}}}]},
]
for f in FOLDERS:
    drive(make({"type": "open_folder", "params": {}}, folder=f), f"folder={f!r}"[:80])
    n += 1

# 5. weird key indices & duplicates
EXTRA = [
    {"0": {"label": "zero"}}, {"-3": {"label": "neg"}},
    {"99999999999999999999": {"label": "big"}},
    {"01": {"label": "dup"}},
    {" 1": {"label": "dup2"}},
    {"2": {"label": "two", "action": {"type": "monitor", "params": {"metric": "clock", "interval": -1}}}},
]
for e in EXTRA:
    drive(make({"type": "none", "params": {}}, extra_keys=e), f"extrakeys={e!r}"[:80])
    n += 1

# 6. duplicate / dangling ids
d = make({"type": "switch_profile", "params": {"profile_id": "nope"}})
d["profiles"].append(copy.deepcopy(d["profiles"][0]))
drive(d, "duplicate profile ids + dangling switch target")
d2 = make({"type": "none", "params": {}})
d2["active_profile_id"] = "does-not-exist"
drive(d2, "dangling active_profile_id")
d3 = make({"type": "none", "params": {}})
d3["profiles"][0]["pages"].append(copy.deepcopy(d3["profiles"][0]["pages"][0]))
drive(d3, "duplicate page ids")
n += 3

print(f"ran {n} cases, {len(FAILS)} failures")
seen = set()
for tag, stage, etype, msg, tb in FAILS:
    key = (stage, etype, msg)
    if key in seen:
        continue
    seen.add(key)
    print("=" * 70)
    print("CASE:", tag)
    print("STAGE:", stage, "->", etype, ":", msg)
    print(tb[-1800:])
