"""
System-monitor keys: live CPU / RAM / VRAM / GPU / temperature / network /
disk readouts — plus a clock face — rendered onto a key's LCD (like the
monitor widgets in the official Stream Dock app).

Two halves:
- Sampler   — polls the metrics (psutil for CPU/RAM/disk/network/temps,
              per-vendor sources for VRAM and GPU load) and keeps the short
              history a sparkline needs.
- render_monitor — draws a Reading as a key image in one of three styles
              (number / gauge / graph), reusing the app font + colour helpers.

The controller owns one Sampler and ticks it on a background thread; the GUI
only ever renders placeholders (live frames arrive from the controller), so
nothing here may touch Qt.
"""
from __future__ import annotations

import glob
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import NamedTuple

from PIL import Image, ImageDraw

from .rendering import _font, _hex

log = logging.getLogger(__name__)

try:
    import psutil
except ImportError:          # packaged builds depend on it; source runs may not
    psutil = None            # type: ignore[assignment]

METRICS = {
    "cpu": "CPU",
    "ram": "RAM",
    "vram": "VRAM",
    "gpu": "GPU",
    "gputemp": "GPU°C",
    "temp": "TEMP",
    "net": "NET",
    "disk": "DISK",
    "clock": "CLOCK",
}
STYLES = ("number", "gauge", "graph")
# Clock faces: "auto" keeps the 0.7.0 behavior (seconds iff refreshing under
# 5 s); explicit choices pin the format regardless of interval.
CLOCK_FORMATS = ("auto", "24h", "24h+seconds", "12h", "12h+seconds")
CLOCK_DATES = ("auto", "iso", "us", "none")
_CLOCK_STRF = {"24h": "%H:%M", "24h+seconds": "%H:%M:%S",
               "12h": "%I:%M", "12h+seconds": "%I:%M:%S"}
_CLOCK_DATE_STRF = {"auto": "%a %d %b", "iso": "%Y-%m-%d",
                    "us": "%a, %b %d", "none": None}
# Metrics on a fixed 0..100 axis (gauge + graph scale). temp/gputemp are °C,
# not percentages, but share the axis: 0-100°C covers consumer hardware and
# the 90 warn threshold doubles as a sensible thermal alarm.
PERCENT_METRICS = frozenset({"cpu", "ram", "vram", "gpu", "disk", "temp", "gputemp"})
# the only metrics a target applies to (disk mount, net iface, temp sensor)
TARGETED_METRICS = frozenset({"disk", "net", "temp"})

HISTORY_LEN = 32             # sparkline points kept per metric
# Sampled streams kept before the least recently used half is dropped. A deck
# has 15 keys, so anything beyond a couple of dozen distinct streams is churn
# from the editor rather than real configuration.
MAX_STREAMS = 64

ACCENT = (64, 158, 255)      # matches the GUI accent #409eff
WARN = (255, 92, 92)         # gauge/graph turn red above WARN_PCT
WARN_PCT = 90.0


@dataclass
class Reading:
    """One sampled value. pct is 0..100 where the metric has a natural
    percentage (CPU/RAM/VRAM/disk), None otherwise (network rates, errors)."""
    pct: float | None
    text: str                # big value line ("37%", "1.2 MB/s")
    sub: str = ""            # small detail line ("6.2/16 GB", "↑ 340 kB/s")
    ok: bool = True
    # What the sparkline records for this sample. None = a gap (failed sample,
    # warm-up), which the graph must SKIP — recording 0.0 instead would draw a
    # false dip to zero.
    sample: float | None = None


@dataclass(frozen=True)
class MonitorSpec:
    """Validated monitor parameters for one key (parsed from Action.params)."""
    metric: str = "cpu"
    style: str = "number"
    interval: float = 1.0
    target: str = ""         # disk mount point or network interface ("" = auto)
    clock_format: str = "auto"   # only meaningful for metric == "clock"
    clock_date: str = "auto"

    @classmethod
    def from_params(cls, params: dict | None) -> "MonitorSpec":
        p = params or {}
        metric = str(p.get("metric", "cpu")).strip().lower()
        if metric not in METRICS:
            metric = "cpu"
        style = str(p.get("style", "number")).strip().lower()
        if style not in STYLES:
            style = "number"
        try:
            interval = float(str(p.get("interval", "") or "1").strip())
        except (TypeError, ValueError):
            interval = 1.0
        interval = max(0.5, min(60.0, interval))
        target = str(p.get("target", "")).strip()
        if metric not in TARGETED_METRICS:
            # A stray target on cpu/ram/vram would needlessly split the shared
            # sample stream (and with it psutil's global delta state).
            target = ""
        clock_format = str(p.get("clock_format", "auto")).strip().lower()
        if clock_format not in CLOCK_FORMATS:
            clock_format = "auto"
        clock_date = str(p.get("clock_date", "auto")).strip().lower()
        if clock_date not in CLOCK_DATES:
            clock_date = "auto"
        if metric != "clock":
            # stray clock params on other metrics must not split their streams
            clock_format = "auto"
            clock_date = "auto"
        return cls(metric=metric, style=style, interval=interval, target=target,
                   clock_format=clock_format, clock_date=clock_date)

    def resolved_clock(self) -> tuple[str, str]:
        """The concrete (time format, date style) a clock key will render.
        "auto" resolves the 0.7.0 way: seconds iff refreshing under 5 s."""
        fmt = self.clock_format
        if fmt == "auto":
            fmt = "24h+seconds" if self.interval < 5 else "24h"
        return (fmt, self.clock_date)

    def key(self) -> tuple:
        """Stream key: keys with the same metric+target share ONE sample
        stream (one reading + one history per tick). This is not just an
        optimisation — cpu_percent and net counters are since-last-call
        deltas, so sampling a stream twice in quick succession returns
        garbage (~0) to the second caller."""
        if self.metric == "clock":
            # A clock Reading bakes in its rendered format, so clocks whose
            # RESOLVED format differs must not share one Reading — a 30 s key
            # would freeze another key's seconds display (0.7.0 audit).
            return ("clock", "|".join(self.resolved_clock()))
        return (self.metric, self.target)


def placeholder(spec: MonitorSpec) -> Reading:
    """What a monitor key shows before its first sample arrives."""
    return Reading(None, "—", METRICS.get(spec.metric, ""))


# Interfaces that carry no real network traffic, or that double-count traffic
# already counted on the interface underneath. Matched on the name's prefix.
_VIRTUAL_IFACES = ("lo", "docker", "br-", "veth", "virbr", "vmnet", "tun",
                   "tap", "bridge", "podman", "cni", "flannel", "kube")


def _is_real_iface(name: str) -> bool:
    return not name.startswith(_VIRTUAL_IFACES)


class _NetTotals(NamedTuple):
    bytes_recv: int
    bytes_sent: int


def _sum_real_ifaces(per: dict):
    """Total the physical interfaces only, as an object with the two counters.

    Falls back to summing everything if the filter leaves nothing, so a machine
    whose only interface has an unusual name still reports something.
    """
    names = [n for n in per if _is_real_iface(n)] or list(per)
    recv = sum(per[n].bytes_recv for n in names)
    sent = sum(per[n].bytes_sent for n in names)
    return _NetTotals(recv, sent)


def _fmt_bytes(n: float) -> str:
    # B and kB render whole, MB+ get one decimal — as they did before 0.12.0.
    # 0.12.0 gave kB a decimal too, which widened every kB-range network
    # reading by two characters and shrank the net key's value font from 20 px
    # to 16 px at arm's length; the accuracy it bought (1500 as "2 kB" vs
    # "1.5 kB") is not worth that on a small LCD.
    #
    # Roll over on the ROUNDED value, so 999999 shows "1.0 MB" rather than
    # "1000.0 kB": 999.999 is < 1000 but rounds up to 1000 at the display
    # precision. (0.12.0's comment claimed to fix this and did not; the bug
    # was in 0.11.3 too.)
    n = float(n)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        prec = 0 if unit in ("B", "kB") else 1
        if round(abs(n), prec) < 1000 or unit == "TB":
            return f"{n:.{prec}f} {unit}"
        n /= 1000.0
    return f"{n:.1f} TB"


def _fmt_rate(bps: float) -> str:
    return _fmt_bytes(bps) + "/s"


class Sampler:
    """Stateful metric poller. One instance per controller; not thread-safe by
    design — only the controller's monitor thread calls sample(), while other
    threads only read the last-reading cache (atomic dict ops under the GIL)."""

    def __init__(self):
        self._hist: dict[tuple, deque] = {}
        self._last: dict[tuple, Reading] = {}
        self._net_prev: dict[str, tuple[float, int, int]] = {}
        self._vram_backend = None      # probed lazily; ("none",) when absent
        self._gpu_backend = None       # same lifecycle as _vram_backend
        self._gputemp_backend = None   # same lifecycle as _vram_backend
        self._vram_retries = 0         # bounded: a dead source must settle
        self._gpu_retries = 0
        self._gputemp_retries = 0
        # Backends that PROBE fine but cannot be read; see _gpu_read_failed.
        self._read_failures: dict[str, int] = {}
        # psutil keys its cpu_percent since-last-call baseline PER THREAD, so
        # priming must be per thread too — a flag primed on one thread would
        # let another thread's first (garbage) reading through as real.
        self._cpu_primed_threads: set[int] = set()
        # (stream key, error text) already logged — see sample(). MonitorSpec.key
        # returns a tuple, not a str. Bounded by the number of configured monitor
        # keys times their distinct failure modes.
        self._logged_failures: set[tuple[tuple, str]] = set()
        # LRU bookkeeping for _evict_old_streams. A counter, not a clock: it
        # only ever needs an ordering, and time.monotonic is monkeypatched all
        # over the tests.
        self._stream_seen: dict[tuple, int] = {}
        self._stream_clock = 0

    # -- public ------------------------------------------------------------
    def sample(self, spec: MonitorSpec) -> Reading:
        """Take ONE sample of this spec's stream. The caller must call this at
        most once per stream per tick (see MonitorSpec.key) — every key of the
        stream then shares the returned reading."""
        fn = getattr(self, f"_sample_{spec.metric}", None)
        try:
            reading = fn(spec) if fn else Reading(None, "n/a", ok=False)
        except Exception as e:                      # a bad mount/iface must not
            # Log each distinct failure ONCE. The caller re-samples every
            # spec.interval regardless of the outcome (the unchanged-signature
            # fast path suppresses only the repaint), and interval floors at
            # 0.5 s — so one key pointed at a mistyped mount wrote ~172,800
            # identical WARNING lines a day into the journal, forever, while
            # the key face just read "n/a".
            sig = (spec.key(), str(e))
            if sig not in self._logged_failures:
                self._logged_failures.add(sig)
                log.warning("monitor %s failed (further identical failures "
                            "will not be logged): %s", spec.metric, e)
            reading = Reading(None, "n/a", METRICS.get(spec.metric, ""), ok=False)
        k = spec.key()
        if k not in self._last:
            self._evict_old_streams()
        self._last[k] = reading
        hist = self._hist.setdefault(k, deque(maxlen=HISTORY_LEN))
        hist.append(reading.sample)
        self._stream_seen[k] = self._stream_clock
        self._stream_clock += 1
        return reading

    def _evict_old_streams(self) -> None:
        """Forget the least recently sampled streams once there are too many.

        Nothing ever removed one, and the target field is a QLineEdit wired to
        textChanged: every keystroke while a monitor key is selected writes a
        new MonitorSpec, and every one the monitor thread catches on a tick
        became a permanent entry. Measured: 12 s of typing left 21 streams.
        Each is only a Reading plus a 32-slot deque, so this is a drip rather
        than a hazard — but it is unbounded over a process lifetime, and a real
        deck cannot have anything like MAX_STREAMS distinct live ones.
        """
        if len(self._last) < MAX_STREAMS:
            return
        oldest = sorted(self._stream_seen, key=lambda k: self._stream_seen[k])
        for k in oldest[:MAX_STREAMS // 2]:
            self._last.pop(k, None)
            self._hist.pop(k, None)
            self._stream_seen.pop(k, None)

    def last(self, spec: MonitorSpec) -> Reading:
        return self._last.get(spec.key()) or placeholder(spec)

    def history(self, spec: MonitorSpec) -> list[float | None]:
        return list(self._hist.get(spec.key(), ()))

    # -- metrics -----------------------------------------------------------
    def _sample_cpu(self, spec: MonitorSpec) -> Reading:
        if psutil is None:
            return _NO_PSUTIL
        pct = psutil.cpu_percent(interval=None)
        tid = threading.get_ident()
        if tid not in self._cpu_primed_threads:
            # psutil documents the first non-blocking cpu_percent() as a
            # meaningless 0.0 (no since-last-call window yet) — show a
            # warm-up frame instead of a fake 0%.
            self._cpu_primed_threads.add(tid)
            return Reading(None, "…", METRICS["cpu"])
        return Reading(pct, f"{pct:.0f}%", f"{os.cpu_count() or '?'} cores",
                       sample=pct)

    def _sample_ram(self, spec: MonitorSpec) -> Reading:
        if psutil is None:
            return _NO_PSUTIL
        vm = psutil.virtual_memory()
        return Reading(vm.percent, f"{vm.percent:.0f}%",
                       f"{_fmt_bytes(vm.used)} / {_fmt_bytes(vm.total)}",
                       sample=vm.percent)

    def _sample_disk(self, spec: MonitorSpec) -> Reading:
        if psutil is None:
            return _NO_PSUTIL
        du = psutil.disk_usage(spec.target or "/")
        return Reading(du.percent, f"{du.percent:.0f}%",
                       f"{_fmt_bytes(du.free)} free", sample=du.percent)

    def _sample_net(self, spec: MonitorSpec) -> Reading:
        if psutil is None:
            return _NO_PSUTIL
        per = psutil.net_io_counters(pernic=True)
        if spec.target:
            io = per.get(spec.target)
            if io is None:
                return Reading(None, "n/a", f"no iface {spec.target}", ok=False)
        else:
            # NOT psutil.net_io_counters(): that sums EVERY interface, so an
            # untargeted key reported localhost sockets as network throughput.
            # Measured: 209 MB over 127.0.0.1 read as "↓ 3.1 GB/s ↑ 3.1 GB/s",
            # the same bytes counted in both directions. A local database, X
            # forwarding, PulseAudio over TCP or any container bridge shows
            # constant phantom traffic, and one burst flattens the sparkline
            # for the next 32 samples because it normalises to the window max.
            io = _sum_real_ifaces(per)
        now = time.monotonic()
        prev = self._net_prev.get(spec.target)
        self._net_prev[spec.target] = (now, io.bytes_recv, io.bytes_sent)
        # A stale `prev` is worse than none. _net_prev lives on the Sampler and
        # survives page switches, folder navigation, profile switches and
        # unplugs — all of which stop this key being sampled while the counters
        # keep climbing. Returning on a 600 s gap reported the average over the
        # whole absence (measured: "↓ 5.0 MB/s" when the true rate was zero)
        # and fed that spike into the sparkline. Anything much older than the
        # sampling interval is a gap, not a measurement.
        stale = prev is not None and (now - prev[0]) > max(4.0, spec.interval * 4)
        if prev is None or now <= prev[0] or stale:
            return Reading(None, "…", METRICS["net"])
        dt = now - prev[0]
        down = max(0.0, (io.bytes_recv - prev[1]) / dt)
        up = max(0.0, (io.bytes_sent - prev[2]) / dt)
        # the graph plots the download rate
        return Reading(None, f"↓ {_fmt_rate(down)}", f"↑ {_fmt_rate(up)}",
                       sample=down)

    def _resolve_gpu_backend(self, attr: str, probe, retries_attr: str):
        """Shared probe/retry/settle lifecycle for the GPU-family backends.

        "retry" (NVML installed but not ready — driver still loading at
        login) is NOT cached: probe again next sample instead of freezing on
        n/a forever. But retries are bounded — ~20 samples (>= 10 s at the
        fastest interval) covers a loading driver; after that the source is
        treated as permanently unavailable and a FINAL probe settles on the
        best remaining answer (amdgpu if that's what the machine has, else
        none) so we stop re-running import+nvmlInit every interval."""
        b = getattr(self, attr)
        if b is not None:
            return b
        b = probe()
        if b[0] != "retry":
            setattr(self, attr, b)
            setattr(self, retries_attr, 0)
            return b
        n = getattr(self, retries_attr) + 1
        setattr(self, retries_attr, n)
        if n >= 20:
            b = probe(final=True)
            if b[0] == "retry":
                b = ("none",)
            setattr(self, attr, b)
        return b

    def _gpu_read_failed(self, attr: str, retries_attr: str) -> None:
        """A backend that probed fine but could not be READ.

        Dropping the cache so the next sample re-probes is right for a driver
        unload or a GPU hot-remove. But the retry budget above only counts
        probes that RETURN "retry", and it resets to zero on every successful
        probe — so for the failure mode where the probe keeps succeeding and
        only the read fails (NVML after a Xid error: nvmlInit and
        nvmlDeviceGetHandleByIndex both work, device queries do not), the cycle
        probe-ok / read-fails / drop-cache repeated forever. Roughly twice a
        second at the fastest interval, each iteration leaking another NVML
        init refcount, and the key read "n/a" the whole time with exactly one
        line in the log to explain it.

        So count read failures separately and settle for good once they pile
        up, which is what the "we stop re-running import+nvmlInit every
        interval" invariant meant to promise.
        """
        # Its OWN counter, deliberately not retries_attr: _resolve_gpu_backend
        # zeroes that one on every successful probe, and here the probe keeps
        # succeeding — so sharing it would reset the budget on every lap of the
        # very loop this exists to break.
        #
        # CONSECUTIVE, not cumulative: _gpu_read_ok clears this on every good
        # read. Without that clear the count only ever rose, so twenty blips
        # spread across the whole session — one a day on a hybrid laptop whose
        # dGPU runtime-suspends, or a GPU reset — permanently killed the key
        # until restart, where 0.11.3 recovered every time. Twenty in a row is
        # the genuinely-stuck NVML-after-Xid case this is meant to catch.
        n = self._read_failures.get(attr, 0) + 1
        self._read_failures[attr] = n
        if n >= 20:
            log.warning("%s: the GPU backend probes but cannot be read (%d "
                        "attempts); giving up until restart", attr, n)
            self._release_nvml(getattr(self, attr))
            setattr(self, attr, ("none",))
        else:
            # Balance this backend's nvmlInit() before dropping it: the next
            # sample re-probes (attr is None) and calls nvmlInit() again, so
            # without the release an intermittently-failing GPU — which never
            # reaches the n>=20 settle above because a good read resets the
            # counter — climbs NVML's refcount on every failure/re-probe cycle.
            self._release_nvml(getattr(self, attr))
            setattr(self, attr, None)

    def _gpu_read_ok(self, attr: str) -> None:
        """A read landed, so the consecutive-failure budget starts over."""
        self._read_failures.pop(attr, None)

    @staticmethod
    def _release_nvml(backend) -> None:
        """Balance the nvmlInit() a probe did, when we stop using its handle.

        Nothing on the vram/gpu path ever called nvmlShutdown, so every probe
        added a refcount that was never given back.
        """
        try:
            if backend and backend[0] == "nvml":
                backend[1].nvmlShutdown()
        except Exception:
            log.debug("nvmlShutdown failed", exc_info=True)

    def _sample_vram(self, spec: MonitorSpec) -> Reading:
        b = self._resolve_gpu_backend("_vram_backend", _probe_vram,
                                      "_vram_retries")
        if b[0] not in ("nvml", "amdgpu"):
            return Reading(None, "n/a", "no dedicated GPU", ok=False)
        try:
            if b[0] == "nvml":
                info = b[1].nvmlDeviceGetMemoryInfo(b[2])
                used, total = info.used, info.total
            else:
                with open(b[1]) as f:
                    used = int(f.read())
                with open(b[2]) as f:
                    total = int(f.read())
        except Exception:
            # The backend died under us (driver unload, GPU hot-remove):
            # drop the cache so the next sample re-probes instead of
            # warning every interval forever.
            self._gpu_read_failed("_vram_backend", "_vram_retries")
            raise
        self._gpu_read_ok("_vram_backend")
        pct = 100.0 * used / total if total else 0.0
        return Reading(pct, f"{pct:.0f}%",
                       f"{_fmt_bytes(used)} / {_fmt_bytes(total)}", sample=pct)

    def _sample_gpu(self, spec: MonitorSpec) -> Reading:
        b = self._resolve_gpu_backend("_gpu_backend", _probe_gpu,
                                      "_gpu_retries")
        if b[0] not in ("nvml", "amdgpu"):
            return Reading(None, "n/a", "no dedicated GPU", ok=False)
        try:
            if b[0] == "nvml":
                pct = float(b[1].nvmlDeviceGetUtilizationRates(b[2]).gpu)
            else:
                with open(b[1]) as f:
                    pct = float(f.read())
        except Exception:
            # backend died (driver unload / hot-remove): re-probe next sample
            self._gpu_read_failed("_gpu_backend", "_gpu_retries")
            raise
        self._gpu_read_ok("_gpu_backend")
        return Reading(pct, f"{pct:.0f}%", "load", sample=pct)

    def _sample_gputemp(self, spec: MonitorSpec) -> Reading:
        """GPU temperature with the sensor auto-picked per vendor — the
        one-click alternative to a manual temp target like "amdgpu:edge"."""
        b = self._resolve_gpu_backend("_gputemp_backend", _probe_gputemp,
                                      "_gputemp_retries")
        if b[0] not in ("nvml", "amdgpu"):
            return Reading(None, "n/a", "no GPU sensor", ok=False)
        try:
            if b[0] == "nvml":
                # 0 == NVML_TEMPERATURE_GPU (the constant's value is stable API)
                val = float(b[1].nvmlDeviceGetTemperature(b[2], 0))
                label = "GPU"
            else:
                temps: dict = getattr(psutil, "sensors_temperatures", lambda: {})() or {}
                picked = _pick_temp(temps, b[1])
                if picked is None:
                    raise LookupError(f"sensor {b[1]} vanished")
                label, val = picked
        except Exception:
            self._gpu_read_failed("_gputemp_backend", "_gputemp_retries")
            raise
        self._gpu_read_ok("_gputemp_backend")
        pct = max(0.0, min(100.0, val))
        return Reading(pct, f"{val:.0f}°C", label, sample=val)

    def _sample_temp(self, spec: MonitorSpec) -> Reading:
        if psutil is None:
            return _NO_PSUTIL
        temps: dict = getattr(psutil, "sensors_temperatures", lambda: {})() or {}
        picked = _pick_temp(temps, spec.target)
        if picked is None:
            return Reading(None, "n/a",
                           f"no sensor {spec.target}" if spec.target
                           else "no temp sensors", ok=False)
        label, val = picked
        # °C on the shared 0..100 gauge axis; graph records the raw value
        pct = max(0.0, min(100.0, val))
        return Reading(pct, f"{val:.0f}°C", label, sample=val)

    def _sample_clock(self, spec: MonitorSpec) -> Reading:
        # No psutil needed. "auto" shows seconds only at fast refresh — at
        # slow intervals a seconds display would just sit stale between pushes.
        now = time.localtime()
        fmt, date = spec.resolved_clock()
        text = time.strftime(_CLOCK_STRF[fmt], now)
        if fmt.startswith("12h"):
            text = text.lstrip("0") or text      # "01:05" -> "1:05"
            # AM/PM by hand: %p is EMPTY in most European locales, which
            # would leave an ambiguous 12-hour face (midnight == noon) with
            # a stray trailing space (0.8.0 audit).
            text += " AM" if now.tm_hour < 12 else " PM"
        datef = _CLOCK_DATE_STRF[date]
        sub = time.strftime(datef, now) if datef else ""
        return Reading(None, text, sub)


_NO_PSUTIL = Reading(None, "n/a", "psutil missing", ok=False)

# Chips whose first matching entry is the CPU-package temperature, in
# preference order (Intel, AMD, AMD-alt, ARM SBCs, ACPI fallback).
_TEMP_PREFERRED = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz")


def _pick_temp(temps: dict, target: str) -> tuple[str, float] | None:
    """Pick one (label, current °C) from psutil.sensors_temperatures().

    target "" = auto: prefer a CPU package sensor, else the first chip that
    reports anything. Explicit targets select "chip" or "chip:label"
    (case-insensitive, label matched by prefix — "nvme:comp" hits Composite).
    """
    if target:
        chip, _, want = target.partition(":")
        chip, want = chip.strip().lower(), want.strip().lower()
        for name, entries in temps.items():
            if name.lower() != chip:
                continue
            for e in entries:
                lbl = (getattr(e, "label", "") or "").lower()
                if not want or lbl.startswith(want):
                    return (getattr(e, "label", "") or name, float(e.current))
        return None
    for chip in _TEMP_PREFERRED:
        for name, entries in temps.items():
            if name.lower() == chip and entries:
                e = _pkg_entry(entries)
                return (getattr(e, "label", "") or name, float(e.current))
    for name, entries in temps.items():
        if entries:
            e = entries[0]
            return (getattr(e, "label", "") or name, float(e.current))
    return None


def _pkg_entry(entries):
    """The package/whole-die entry of a CPU chip, else its first entry."""
    for e in entries:
        lbl = (getattr(e, "label", "") or "").lower()
        if lbl.startswith(("package", "tctl", "tdie")):
            return e
    return entries[0]


_PCI_DEVICES = "/sys/bus/pci/devices"
_nvidia_present_cache: bool | None = None   # scanned once; PCI doesn't change


def _nvidia_gpu_present() -> bool:
    """Is an NVIDIA display device on the PCI bus? Driver-independent: sysfs
    exposes vendor/class before (and without) the nvidia module loading. This
    is what lets the probes tell "NVML failed because there is no NVIDIA GPU"
    (fall through to amdgpu now) from "failed because the driver isn't up yet"
    (retry: on a hybrid machine the amdgpu node is the iGPU, and caching it
    would pin the key to the wrong GPU for the process lifetime)."""
    global _nvidia_present_cache
    if _nvidia_present_cache is None:
        found = False
        for vf in glob.glob(os.path.join(_PCI_DEVICES, "*", "vendor")):
            try:
                with open(vf) as f:
                    if f.read().strip().lower() != "0x10de":
                        continue
                with open(os.path.join(os.path.dirname(vf), "class")) as f:
                    # 0x03xxxx == PCI display controller class
                    if f.read().strip().lower().startswith("0x03"):
                        found = True
                        break
            except OSError:
                continue
        _nvidia_present_cache = found
    return _nvidia_present_cache


def _probe_vram(final: bool = False):
    """Find a VRAM source: NVIDIA via NVML, AMD via sysfs, else none.
    Intel iGPUs share system RAM — there is nothing meaningful to report.

    NVML failing does NOT mean no GPU: pynvml is pure Python (the deb
    Recommends it, the snap bundles it), so on AMD-only machines the import
    succeeds and nvmlInit() raises library-not-found forever — the amdgpu
    sysfs probe must still run. But when NVIDIA *hardware* is on the PCI bus,
    an NVML failure means the driver isn't up yet (login) — then amdgpu must
    NOT run: on hybrid machines it is the iGPU, and the 0.8.1 audit found the
    init-failure path pinning keys to it forever. ("retry",) is re-probed by
    the caller, bounded; final=True is the caller settling after the retry
    budget: best remaining answer only, never another retry."""
    nvml_present = False
    nvml_inited = False
    try:
        import pynvml
        nvml_present = True
        pynvml.nvmlInit()
        nvml_inited = True
        return ("nvml", pynvml, pynvml.nvmlDeviceGetHandleByIndex(0))
    except Exception:
        # nvmlInit() succeeded but the handle query failed (driver still
        # loading, GPU lost): balance the init, or every probe on a sick NVML
        # leaks a refcount. Only when init actually ran.
        if nvml_inited:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
    if nvml_present and not final and _nvidia_gpu_present():
        return ("retry",)
    for total in sorted(glob.glob("/sys/class/drm/card*/device/mem_info_vram_total")):
        used = os.path.join(os.path.dirname(total), "mem_info_vram_used")
        if os.path.exists(used):
            return ("amdgpu", used, total)
    return ("retry",) if (nvml_present and not final) else ("none",)


def _probe_gpu(final: bool = False):
    """Find a GPU-load source: NVIDIA via NVML utilization rates, AMD via the
    sysfs gpu_busy_percent file. Same fallback/retry/final semantics as
    _probe_vram (see there for the hybrid-machine reasoning)."""
    nvml_present = False
    nvml_inited = False
    try:
        import pynvml
        nvml_present = True
        pynvml.nvmlInit()
        nvml_inited = True
        return ("nvml", pynvml, pynvml.nvmlDeviceGetHandleByIndex(0))
    except Exception:
        # nvmlInit() succeeded but the handle query failed (driver still
        # loading, GPU lost): balance the init, or every probe on a sick NVML
        # leaks a refcount. Only when init actually ran.
        if nvml_inited:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
    if nvml_present and not final and _nvidia_gpu_present():
        return ("retry",)
    for busy in sorted(glob.glob("/sys/class/drm/card*/device/gpu_busy_percent")):
        return ("amdgpu", busy)
    return ("retry",) if (nvml_present and not final) else ("none",)


def _probe_gputemp(final: bool = False):
    """Find a GPU temperature source: NVIDIA via NVML, AMD via the amdgpu
    chip in psutil's sensors (edge is the conventional die-edge sensor).

    A WORKING nvmlInit means an NVIDIA GPU exists — a failed sensor read then
    returns ("retry",) and never falls through to amdgpu: on hybrid laptops
    the amdgpu chip is the iGPU, and caching it would pin the key to the
    wrong GPU forever (0.8.0 audit). When nvmlInit itself fails but NVIDIA
    *hardware* is on the PCI bus (driver still loading at login — the
    init-failure hole the 0.8.1 audit found), amdgpu must equally not run.
    The caller caps retries and settles with final=True: best remaining
    answer only, never another retry."""
    nvml_importable = False
    nvml_inited = False
    try:
        import pynvml
        nvml_importable = True
        pynvml.nvmlInit()
        nvml_inited = True
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        pynvml.nvmlDeviceGetTemperature(handle, 0)     # probe the sensor too
        return ("nvml", pynvml, handle)
    except Exception:
        if nvml_inited:
            # NVIDIA GPU present but the sensor read failed: undo the init
            # refcount and let the caller retry (bounded) — NOT amdgpu.
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
            return ("retry",)
    if nvml_importable and not final and _nvidia_gpu_present():
        return ("retry",)
    # No NVIDIA route (or settling): AMD sysfs sensors are the right answer
    # on AMD machines; otherwise retry (bounded) while a driver may load.
    if psutil is not None:
        temps: dict = getattr(psutil, "sensors_temperatures", lambda: {})() or {}
        for want in ("amdgpu:edge", "amdgpu"):
            if _pick_temp(temps, want) is not None:
                return ("amdgpu", want)
    return ("retry",) if (nvml_importable and not final) else ("none",)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _mix(a, b, t: float):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def render_monitor(size: int, spec: MonitorSpec, reading: Reading,
                   history: list[float | None] | None = None,
                   bg_color: str = "#101020",
                   text_color: str = "#ffffff") -> Image.Image:
    """Draw one monitor frame as an upright RGB key image."""
    bg = _hex(bg_color)
    fg = _hex(text_color, (255, 255, 255))
    dim = _mix(bg, fg, 0.55)                       # muted label colour
    img = Image.new("RGB", (size, size), bg)
    draw = ImageDraw.Draw(img)
    style = spec.style if reading.pct is not None or spec.style == "graph" \
        else "number"                              # gauge needs a percentage

    label = METRICS.get(spec.metric, spec.metric.upper())
    accent = WARN if (reading.pct or 0) >= WARN_PCT else ACCENT

    if style == "gauge":
        _draw_gauge(draw, size, reading.pct or 0.0, accent, _mix(bg, fg, 0.18))
        # Value text fills the arc (26% of key size, up from 20%) — the 0.6.0
        # face read small at arm's length on the physical deck. The label
        # moves into the gauge's bottom opening instead of crowding the arc.
        # Width is capped to the arc's INNER opening (0.62·size: margin 0.10 +
        # stroke 0.09 per side), not the key width — at 26% a 4+ glyph value
        # like "100%" or any "…°C" reading would overdraw the arc stroke.
        _center_text(draw, reading.text, size, size * 0.36, int(size * 0.26),
                     fg, max_w=int(size * 0.62))
        _center_text(draw, label, size, size * 0.84, int(size * 0.11), dim)
    elif style == "graph":
        _center_text(draw, label, size, size * 0.06, int(size * 0.11), dim)
        _center_text(draw, reading.text, size, size * 0.18, int(size * 0.17), fg)
        _draw_graph(draw, size, history or [], spec.metric in PERCENT_METRICS,
                    accent, _mix(bg, fg, 0.12))
    else:                                          # number
        _center_text(draw, label, size, size * 0.08, int(size * 0.12), dim)
        _center_text(draw, reading.text, size, size * 0.34, int(size * 0.24), fg)
        if reading.sub:
            _center_text(draw, reading.sub, size, size * 0.72, int(size * 0.11), dim)
    return img


def _center_text(draw, text: str, size: int, y: float, fs: int, fill,
                 max_w: int | None = None):
    if not text:
        return
    fs = max(8, fs)
    limit = max_w if max_w is not None else size - 6
    while fs > 8 and draw.textlength(text, font=_font(fs)) > limit:
        fs -= 1
    font = _font(fs)
    bb = draw.textbbox((0, 0), text, font=font)
    x = int((size - (bb[2] - bb[0])) // 2 - bb[0])
    draw.text((x, int(y)), text, font=font, fill=fill)


def _draw_gauge(draw, size: int, pct: float, accent, track):
    """270° arc gauge, opening at the bottom."""
    m = int(size * 0.10)
    box = (m, m, size - m, size - m)
    width = max(4, int(size * 0.09))
    start, span = 135, 270
    draw.arc(box, start, start + span, fill=track, width=width)
    frac = max(0.0, min(1.0, pct / 100.0))
    if frac > 0:
        draw.arc(box, start, start + int(span * frac), fill=accent, width=width)


def _draw_graph(draw, size: int, history: list[float | None], is_percent: bool,
                accent, grid):
    """Sparkline over the lower part of the key. Percent metrics are scaled to
    0..100; rate metrics normalize to the window's maximum. None entries are
    gaps (failed samples / warm-up) and are skipped, never drawn as zero."""
    top, bottom = int(size * 0.42), int(size * 0.94)
    left, right = int(size * 0.06), int(size * 0.94)
    draw.rectangle((left, top, right, bottom), outline=grid, width=1)
    pts = [v for v in history if v is not None]
    if len(pts) < 2:
        return
    scale = 100.0 if is_percent else max(max(pts), 1.0)
    h, w = bottom - top, right - left
    # Position each point at its place in TIME, not its place among the
    # non-None values. Compacting the gaps out drew [10, None x29, 90] as a
    # full-width line between two samples fifteen seconds apart, implying a
    # continuity that was not measured — most visible right after warm-up or
    # after a run of failed samples.
    n = max(len(history) - 1, 1)
    runs: list[list[tuple[int, int]]] = []
    current: list[tuple[int, int]] = []
    for i, v in enumerate(history):
        if v is None:
            if current:
                runs.append(current)
            current = []
            continue
        x = left + int(w * i / n)
        y = bottom - int(h * max(0.0, min(1.0, v / scale)))
        current.append((x, y))
    if current:
        runs.append(current)
    # soft fill under each unbroken run, then the line itself. A run of a
    # single point — a good sample between two gaps, e.g. a sensor that
    # reports on alternate ticks — is drawn as a dot rather than dropped;
    # dropping it left the whole sparkline blank when no two samples were
    # ever adjacent.
    for xy in runs:
        if len(xy) == 1:
            x, y = xy[0]
            draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=accent)
            continue
        poly = xy + [(xy[-1][0], bottom), (xy[0][0], bottom)]
        draw.polygon(poly, fill=_mix(grid, accent, 0.35))
        draw.line(xy, fill=accent, width=2)
