"""Controller-level fuzz with a mock device attached (device write paths)."""
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

from fifine_deck.model import DeckConfig  # noqa
from fifine_deck.controller import DeckController  # noqa
from fifine_deck.actions import ACTION_TYPES  # noqa
from tests.test_controller import MockDevice  # noqa
from StreamDock.InputTypes import EventType  # noqa

HOSTILE = [None, True, 0, -1, 10 ** 30, 1.5, float("inf"), "", " ", "\n",
           "a\nb", "\x00", "x" * 3000, [], [1], {}, {"a": 1}, "1e999", "-5",
           "#-1-1-1", "abc.gif", "/nonexistent/x.gif"]

FAILS = []


class Ev:
    def __init__(self, et, idx, state):
        self.event_type = et

        class K:
            value = idx
        self.key = K()
        self.state = state


def make(keyd, knobd=None, glow=True):
    return {
        "version": 1, "brightness": 80, "glow": glow,
        "active_profile_id": "p1",
        "profiles": [{"name": "P", "id": "p1", "pages": [{
            "name": "Main", "id": "pg1",
            "keys": {"1": keyd},
            "knobs": {"1": knobd or {"type": "none"}},
        }]}],
    }


def drive(cfgdict, tag):
    p = model.CONFIG_PATH
    try:
        blob = json.dumps(cfgdict)
    except (TypeError, ValueError):
        return
    open(p, "w").write(blob)
    for s in list(os.listdir(model.CONFIG_DIR)):
        if ".corrupt" in s:
            os.remove(os.path.join(model.CONFIG_DIR, s))
    stage = "load"
    ctl = None
    try:
        cfg = DeckConfig.load(p)
        if os.path.exists(p + ".corrupt"):
            return
        stage = "ctl"
        ctl = DeckController(cfg)
        dev = MockDevice()
        ctl.device = dev
        stage = "apply_brightness"
        ctl.apply_brightness()
        stage = "render_page"
        ctl.render_page()
        stage = "flash"
        ctl.flash_key(1, True)
        ctl.flash_key(1, False)
        stage = "keypress"
        ctl._key_callback(dev, Ev(EventType.BUTTON, 1, 1))
        ctl._key_callback(dev, Ev(EventType.BUTTON, 1, 0))
        stage = "monitor_tick"
        ctl.monitor_tick()
        ctl.monitor_tick(now=99999.0)
        stage = "nav"
        ctl.next_page(); ctl.prev_page(); ctl.goto_page(5); ctl.goto_page(-3)
        ctl.next_profile(); ctl.prev_profile()
        stage = "brightness"
        ctl.set_brightness(50); ctl.adjust_brightness(-200)
        stage = "wake/sleep"
        ctl.wake_screen(); ctl.sleep_screen()
        stage = "render again"
        ctl.render_page()
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
FIELDS = ["label", "icon", "bg_color", "text_color"]
for f in FIELDS:
    for v in HOSTILE:
        d = {"label": "L", "icon": "", "bg_color": "#101020",
             "text_color": "#fff", "action": {"type": "none", "params": {}}}
        d[f] = v
        drive(make(d), f"key.{f}={v!r}"[:70]); n += 1

for t in ACTION_TYPES:
    d = {"label": "L", "action": {"type": t, "params": {}},
         "hold_action": {"type": t, "params": {}}}
    drive(make(d), f"action+hold={t}"); n += 1

for v in HOSTILE:
    for pk in ("metric", "style", "interval", "target", "clock_format",
               "clock_date"):
        d = {"label": "M", "action": {"type": "monitor", "params": {pk: v}}}
        drive(make(d), f"monitor.{pk}={v!r}"[:70]); n += 1

print(f"ran {n}, fails {len(FAILS)}")
seen = set()
for tag, stage, etype, msg, tb in FAILS:
    k = (stage, etype, msg)
    if k in seen:
        continue
    seen.add(k)
    print("=" * 70)
    print("CASE:", tag, "STAGE:", stage, "->", etype, ":", msg)
    print(tb[-1500:])
