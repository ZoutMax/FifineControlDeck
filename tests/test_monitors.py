"""System-monitor keys: sampler, renderer, and controller tick behaviour.

The invariants pinned here come straight from issue #2's acceptance criteria:
- monitor keys repaint only when their displayed value changes
- a page without monitor keys never samples a metric at all
- VRAM degrades gracefully when no dedicated GPU exists
- a broken target (bad mount, unknown interface) yields "n/a", never a crash
"""
from collections import deque, namedtuple

import pytest

from PIL import Image

from fifine_deck import monitors
from fifine_deck.actions import ACTION_CATALOG, ACTION_TYPES, execute
from fifine_deck.model import Action
from fifine_deck.monitors import MonitorSpec, Reading, Sampler, render_monitor


# ---------------------------------------------------------------------------
# MonitorSpec parsing
# ---------------------------------------------------------------------------
def test_spec_defaults_and_validation():
    s = MonitorSpec.from_params({})
    assert (s.metric, s.style, s.interval, s.target) == ("cpu", "number", 1.0, "")
    s = MonitorSpec.from_params(
        {"metric": " RAM ", "style": "GAUGE", "interval": "2.5", "target": " / "})
    assert (s.metric, s.style, s.interval, s.target) == ("ram", "gauge", 2.5, "/")


def test_spec_rejects_garbage_without_raising():
    s = MonitorSpec.from_params(
        {"metric": "nonsense", "style": "3d", "interval": "banana"})
    assert (s.metric, s.style, s.interval) == ("cpu", "number", 1.0)
    assert MonitorSpec.from_params(None).metric == "cpu"


def test_spec_interval_is_clamped():
    assert MonitorSpec.from_params({"interval": "0.01"}).interval == 0.5
    assert MonitorSpec.from_params({"interval": "9999"}).interval == 60.0


# ---------------------------------------------------------------------------
# Sampler
# ---------------------------------------------------------------------------
def test_cpu_ram_disk_produce_sane_percentages():
    pytest.importorskip("psutil")
    s = Sampler()
    for metric in ("cpu", "ram", "disk"):
        r = s.sample(MonitorSpec.from_params({"metric": metric}))
        assert r.ok, metric
        assert r.pct is not None and 0.0 <= r.pct <= 100.0, metric
        assert r.text.endswith("%"), metric


def test_bad_disk_target_degrades_to_na():
    pytest.importorskip("psutil")
    r = Sampler().sample(MonitorSpec.from_params(
        {"metric": "disk", "target": "/definitely/not/a/mount"}))
    assert not r.ok and r.text == "n/a"


def test_net_rate_from_counter_deltas(monkeypatch):
    psutil = pytest.importorskip("psutil")
    IO = namedtuple("IO", "bytes_recv bytes_sent")
    samples = [IO(1000, 500), IO(1_001_000, 500)]
    monkeypatch.setattr(psutil, "net_io_counters",
                        lambda pernic=False: samples.pop(0) if samples else IO(0, 0))
    # Inexhaustible fake clock: +1 s per call (leaked controller threads from
    # other tests may also call it, so it must never raise).
    t = {"v": 100.0}
    def _mono():
        t["v"] += 1.0
        return t["v"]
    monkeypatch.setattr(monitors.time, "monotonic", _mono)
    s = Sampler()
    spec = MonitorSpec.from_params({"metric": "net"})
    first = s.sample(spec)              # no previous sample -> no rate yet
    assert first.pct is None
    second = s.sample(spec)             # 1 MB in 1 s
    assert second.text == "↓ 1.0 MB/s"
    assert second.sub == "↑ 0 B/s"
    assert second.rate == pytest.approx(1_000_000.0)


def test_net_unknown_interface_degrades_to_na(monkeypatch):
    psutil = pytest.importorskip("psutil")
    monkeypatch.setattr(psutil, "net_io_counters", lambda pernic=False: {})
    r = Sampler().sample(MonitorSpec.from_params(
        {"metric": "net", "target": "nosuch0"}))
    assert not r.ok and r.text == "n/a"


def test_vram_none_backend_degrades(monkeypatch):
    s = Sampler()
    monkeypatch.setattr(monitors, "_probe_vram", lambda: ("none",))
    r = s.sample(MonitorSpec.from_params({"metric": "vram"}))
    assert not r.ok and r.text == "n/a" and "GPU" in r.sub


def test_vram_amdgpu_sysfs_backend(tmp_path, monkeypatch):
    used = tmp_path / "mem_info_vram_used"
    total = tmp_path / "mem_info_vram_total"
    used.write_text("2000000000\n")
    total.write_text("8000000000\n")
    monkeypatch.setattr(monitors, "_probe_vram",
                        lambda: ("amdgpu", str(used), str(total)))
    r = Sampler().sample(MonitorSpec.from_params({"metric": "vram"}))
    assert r.ok and r.pct == pytest.approx(25.0)
    assert r.text == "25%"


def test_history_is_bounded_and_last_is_cached():
    s = Sampler()
    spec = MonitorSpec.from_params({"metric": "cpu"})
    monitors_psutil = monitors.psutil
    if monitors_psutil is None:
        pytest.skip("psutil not available")
    for _ in range(monitors.HISTORY_LEN + 10):
        s.sample(spec)
    assert len(s.history(spec)) == monitors.HISTORY_LEN
    assert s.last(spec).text.endswith("%")


def test_last_without_any_sample_is_the_placeholder():
    s = Sampler()
    spec = MonitorSpec.from_params({"metric": "ram"})
    r = s.last(spec)
    assert r.text == "—" and r.sub == "RAM" and r.pct is None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("style", monitors.STYLES)
def test_render_all_styles_yield_key_sized_rgb(style):
    spec = MonitorSpec.from_params({"metric": "cpu", "style": style})
    img = render_monitor(100, spec, Reading(42.0, "42%", "8 cores"),
                         [10.0, 40.0, 42.0])
    assert isinstance(img, Image.Image)
    assert img.size == (100, 100) and img.mode == "RGB"


def test_render_gauge_without_percentage_falls_back_to_number():
    spec = MonitorSpec.from_params({"metric": "net", "style": "gauge"})
    img = render_monitor(100, spec, Reading(None, "↓ 1.0 MB/s", "↑ 0 B/s"), [])
    assert img.size == (100, 100)     # must not raise on pct=None


def test_render_graph_handles_empty_and_rate_history():
    spec = MonitorSpec.from_params({"metric": "net", "style": "graph"})
    r = Reading(None, "↓ 5 kB/s", rate=5000.0)
    assert render_monitor(100, spec, r, []).size == (100, 100)
    assert render_monitor(100, spec, r, [0.0, 5000.0, 2500.0]).size == (100, 100)


def test_render_actually_draws_something():
    spec = MonitorSpec.from_params({"metric": "cpu", "style": "gauge"})
    img = render_monitor(100, spec, Reading(80.0, "80%"), [], bg_color="#000000")
    assert len(img.getcolors(maxcolors=100000)) > 1   # not a flat fill


# ---------------------------------------------------------------------------
# Action-type integration
# ---------------------------------------------------------------------------
def test_monitor_is_a_registered_action_type():
    assert "monitor" in ACTION_TYPES
    keys = [k for _, kinds in ACTION_CATALOG for k in kinds]
    assert "monitor" in keys
    param_names = [p[0] for p in ACTION_TYPES["monitor"]["params"]]
    assert param_names == ["metric", "style", "interval", "target"]


def test_pressing_a_monitor_key_is_a_noop(caplog):
    execute(Action("monitor", {"metric": "cpu"}), context=None)
    assert "unhandled" not in caplog.text


# ---------------------------------------------------------------------------
# Controller tick behaviour (mock device, fake clock, stubbed sampler)
# ---------------------------------------------------------------------------
controller_mod = pytest.importorskip("fifine_deck.controller")
from fifine_deck.controller import DeckController          # noqa: E402
from fifine_deck.model import DeckConfig                   # noqa: E402
from tests.test_controller import MockDevice               # noqa: E402


class _ScriptedSampler(Sampler):
    """Returns a scripted series of readings and counts sample() calls."""

    def __init__(self, readings):
        super().__init__()
        self._readings = list(readings)
        self.calls = 0

    def sample(self, spec):
        self.calls += 1
        r = self._readings.pop(0) if self._readings else self._readings_last
        self._readings_last = r
        self._last[spec.key()] = r
        h = self._hist.setdefault(spec.key(), deque(maxlen=monitors.HISTORY_LEN))
        h.append(r.pct)
        return r


def _quiesce(c: DeckController):
    """Stop the background monitor thread so tests drive ticks by hand —
    otherwise a real-clock tick could race the fake-clock assertions."""
    c._monitor_stop.set()
    c._monitor_thread.join(timeout=5)
    c._monitor_state.clear()


def _monitored_controller(readings, style="number"):
    cfg = DeckConfig()
    kc = cfg.active_profile().pages[0].key(1)
    kc.action = Action("monitor", {"metric": "cpu", "style": style,
                                   "interval": "1"})
    c = DeckController(cfg)
    _quiesce(c)
    dev = MockDevice()
    assert c._setup_device(dev)
    c._sampler = _ScriptedSampler(readings)
    c._monitor_state.clear()
    dev.key_images.clear()
    dev.refreshes = 0
    return c, dev


def test_tick_paints_a_due_monitor_key_and_refreshes():
    c, dev = _monitored_controller([Reading(10.0, "10%")])
    try:
        c.monitor_tick(now=100.0)
        assert 1 in dev.key_images and dev.refreshes == 1
    finally:
        c.stop()


def test_unchanged_value_is_not_repushed_but_change_is():
    c, dev = _monitored_controller(
        [Reading(10.0, "10%"), Reading(10.0, "10%"), Reading(55.0, "55%")])
    try:
        c.monitor_tick(now=100.0)
        dev.key_images.clear()
        c.monitor_tick(now=101.0)          # same value -> no device write
        assert dev.key_images == {}
        c.monitor_tick(now=102.0)          # changed -> repainted
        assert 1 in dev.key_images
    finally:
        c.stop()


def test_graph_style_repaints_every_sample():
    c, dev = _monitored_controller(
        [Reading(10.0, "10%"), Reading(10.0, "10%")], style="graph")
    try:
        c.monitor_tick(now=100.0)
        dev.key_images.clear()
        c.monitor_tick(now=101.0)
        assert 1 in dev.key_images         # graphs always advance
    finally:
        c.stop()


def test_interval_gates_sampling():
    c, dev = _monitored_controller([Reading(10.0, "10%"), Reading(20.0, "20%")])
    try:
        c.monitor_tick(now=100.0)
        c.monitor_tick(now=100.4)          # 0.4 s < 1 s interval
        assert c._sampler.calls == 1
        c.monitor_tick(now=101.1)
        assert c._sampler.calls == 2
    finally:
        c.stop()


def test_page_without_monitor_keys_never_samples():
    cfg = DeckConfig()
    c = DeckController(cfg)
    _quiesce(c)
    dev = MockDevice()
    assert c._setup_device(dev)
    c._sampler = _ScriptedSampler([])
    try:
        c.monitor_tick(now=100.0)
        assert c._sampler.calls == 0       # acceptance: zero sampling overhead
    finally:
        c.stop()


def test_clearing_the_key_drops_monitor_state():
    c, dev = _monitored_controller([Reading(10.0, "10%")])
    try:
        c.monitor_tick(now=100.0)
        assert c._monitor_state
        c.page().keys[1].action = Action()          # key cleared by the user
        c.monitor_tick(now=101.0)
        assert not c._monitor_state
    finally:
        c.stop()


def test_render_key_paints_monitor_face_not_static_icon():
    c, dev = _monitored_controller([Reading(33.0, "33%")])
    try:
        c.monitor_tick(now=100.0)
        before = dev.key_images[1]
        c.render_key(1)                    # e.g. after a page re-render
        assert isinstance(dev.key_images[1], Image.Image)
        assert dev.key_images[1].size == before.size
        # and the next tick is forced to resample immediately
        assert 1 not in c._monitor_state
    finally:
        c.stop()


def test_flash_skips_monitor_keys():
    c, dev = _monitored_controller([Reading(33.0, "33%")])
    try:
        c.monitor_tick(now=100.0)
        painted = dev.key_images[1]
        c.flash_key(1, pressed=True)
        assert dev.key_images[1] is painted    # untouched by the flash
    finally:
        c.stop()


def test_monitor_callback_receives_frames_and_survives_errors():
    c, dev = _monitored_controller([Reading(10.0, "10%"), Reading(90.0, "90%")])
    try:
        got = []
        def cb(index, img):
            got.append(index)
            raise RuntimeError("GUI went away")
        c.on_monitor_image = cb
        c.monitor_tick(now=100.0)
        c.monitor_tick(now=101.0)          # callback error must not stop ticks
        assert got == [1, 1]
    finally:
        c.stop()
