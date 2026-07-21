# Known issues

Open defects carried forward from the pre-0.10.0 audits. Everything here was
found by reading the code and, where noted, reproducing the behaviour. Entries
marked **FIXED** carry a note saying how; the rest are open.

Line numbers are **as of v0.10.0** (commit `aacf2eb`) and have shifted in files
touched since. Treat them as a pointer, not an address.

Nothing in this file is a regression introduced by 0.10.0 or 0.10.1. These are
long-standing behaviours that predate both.

---

## Device layer and the vendored SDK

**Status:** issues 1-8 are fixed and confirmed on hardware. Issue 9's cause is
confirmed and mitigated as far as it can be without rebuilding a vendored
binary: the remaining 2.001 s is inside `transport_destroy` in the prebuilt
`libtransport.so`, and the window no longer waits on screen for it.

Nothing in the device layer is outstanding.

The items below live in or around `fifine_deck/backend/StreamDock/`, which
is the vendored MiraboxSpace SDK — code we ship but did not write. Fixing them
means patching a third party's threading, so each one needs real replug-cycle
and quit-timing testing against physical hardware before it can be trusted. That
is why they were held back from 0.10.0 rather than rushed in on release day.

### 1. A replug completing inside ~0.6 s wedges the deck permanently — **FIXED (needs a hardware replug to confirm)**

> Fixed after 0.10.2. Reconciliation no longer trusts the path string alone.
> Each cached device is stamped at creation with the identity of its node —
> `(st_dev, st_ino)` — and a device whose path is still present but whose node
> identity has CHANGED is treated as gone, because devtmpfs destroys and
> recreates `/dev/hidrawN` on a replug even though the name is reused.
>
> Serial number does not help here: this deck reports a real one
> (`81D0DA784415`), but it is the same physical device, so it is identical
> before and after. The inode is what distinguishes the two connections.
>
> A removal that actually removed something now also runs `_add_missing_devices`
> immediately, because on a fast replug the "add" uevent can arrive while the
> stale entry is still cached and be skipped by the `_device_exists` guard.
>
> If the node cannot be stat'd the check falls back to path-only, so an
> unreadable node cannot cause a false eviction. Verified against the live deck:
> identity stamps as `(7, 12769)` and reconciliation with the device present
> removes nothing. Six tests in `tests/test_sdk_shutdown.py`.
>
> **Still needs confirming with a physical fast replug** — that is the one
> condition no test here can create.

The original problem, for reference:


`DeviceManager.py:295-301`, `DeviceManager.py:174`, `controller.py:179`

On a udev `remove`, `_remove_missing_devices` retries only 3 times over 0.6 s and
reconciles by **path string**. If the device is back before those retries finish,
enumeration still finds `/dev/hidraw0`, so nothing is removed. The following
`add` then hits the `_device_exists(path)` guard, finds the stale dead device
object still in `streamdocks`, and returns without announcing anything.

`DeckController._on_removed` never fires, so `self.device` keeps pointing at a
StreamDock whose transport handle refers to a torn-down node. Every later write
is silently swallowed, the status bar still reads `● connected fw=…`, and no key
works. The 60 s idle rescan does the same path comparison and also finds nothing
to fix, so there is no recovery short of restarting the app or unplugging for
more than a second.

Reachable from a wobbly USB-C connector or a hub that re-enumerates.

### 2. `transport_destroy` can free a handle while the GIF worker is writing through it — **FIXED**

> Fixed after 0.10.1. `GifController.close()` now returns whether its worker
> actually exited, and `StreamDock.close()` defers `transport.close()` unless
> **every** thread that can be inside a native call on the handle — reader, GIF
> worker and heartbeat — has stopped. Deferring leaks one handle and its threads
> for the process lifetime, which is strictly better than a use-after-free that
> kills the process, and it now says which threads held it back.
> Covered by `tests/test_sdk_shutdown.py`.

The original problem, for reference:


`StreamDock.py:266-275`, `GifController.py:236-237`, `GifController.py:473-481`,
`LibUSBHIDAPI.py:1231-1240`, `LibUSBHIDAPI.py:628-633`

The transport destroy is gated on `read_thread_alive` only. The GIF worker's join
is not: `GifController.close()` joins with `timeout=2.0` and ignores the result,
while that worker writes to the device outside every lock. Every transport method
is an unsynchronised check-then-use of `self._handle`.

Pull the cable while an animated key is playing: the in-flight
`set_key_image_stream` blocks in libusb waiting for its transfer timeout, the
join gives up after 2 s, and the handle is freed underneath it. That is a
use-after-free in C, so the process dies instead of disconnecting cleanly.

**This is the one to fix first.** It is the only item here that can take the
whole process down.

### 3. Every quit blocks ~2 s in the heartbeat join — **FIXED**

> Fixed after 0.10.1. The worker now waits on a `threading.Event` instead of
> `time.sleep`, so a stop request wakes it at once; `close()` sets that event,
> checks the join result, and treats a still-live heartbeat as a reason to defer
> the transport destroy (see issue 2). The unbounded `join()` when restarting the
> heartbeat is bounded too.
>
> Measured on real hardware, phase by phase, after the fix:
> `gif_controller.close()` 0.00 s, **heartbeat join 0.00 s (was a guaranteed
> 2.0 s)**, read thread join 0.09 s.
>
> End to end, A/B over three start-and-quit cycles through one harness on the
> same machine:
>
> | SDK | trials | mean |
> |---|---|---|
> | original | **30.01 s (hung, reported failure)**, 5.74 s, 2.93 s | 12.89 s |
> | fixed | 2.93 s, 3.14 s, 2.77 s | **2.95 s** |
>
> The mean is the less interesting half. The old SDK was *unpredictable* — one
> run hung past 30 s and never reported success while another finished in under
> three seconds, which is the signature of a thread stuck in a native call. The
> fixed SDK stays inside 2.77–3.14 s and always succeeds.
>
> **~2 s of the remaining shutdown is elsewhere** — see issue 9 below, which the
> same profiling turned up.

The original problem, for reference:


`StreamDock.py:599-606`, `StreamDock.py:228-236`, `controller.py:248`,
`main_window.py:876`, `LibUSBHIDAPI.py:886-890`

`_heartbeat_worker` sleeps 10 s between beats, and `close()` joins it with
`timeout=2.0` while ignoring whether the join succeeded. The thread is inside
`time.sleep(10)` essentially always, so the join always times out.

Guaranteed: Options → Quit (or Ctrl+Q) freezes the window for two seconds before
it closes, because `DeckController.stop()` runs on the Qt thread. The same 2 s
blocks the udev listener thread on every unplug.

Narrower race: if the heartbeat passes its `if not self._handle` check just as
`close()` frees the handle, `transport_heartbeat` runs on freed memory.

The fix is small and self-contained — replace the sleep with an interruptible
`threading.Event.wait()` — which makes this the best value-for-risk item here.

### 4. The throttled rescan is idle-gated, not time-gated — **FIXED**

> Fixed after 0.10.2. The rescan now runs on a monotonic wall-clock schedule
> (`last_rescan` + 60 s) regardless of why `poll()` returned, so unrelated USB
> traffic can no longer starve it. The unguarded `pyudev.Context()` /
> `Monitor.from_netlink()` are wrapped too, falling back to polling instead of
> killing the listener thread for the rest of the session.
> Covered by `tests/test_sdk_shutdown.py`.

The original problem, for reference:


`DeviceManager.py:225-229`, `DeviceManager.py:303-307`, `DeviceManager.py:208-210`,
`controller.py:141-149`

`monitor.poll(timeout=60)` returns for **any** uevent on the `usb` subsystem, and
the rescan runs only on the timeout branch. So the safety net does not run "once
a minute" as its comment and `docs/PROVENANCE.md` claim — it runs only after 60
consecutive seconds with zero USB uevents of any kind. On a machine with a
webcam, a dock or a phone attached, that window may never arrive.

This matters because the add path can legitimately give up: `_add_missing_devices`
retries only 10 times at 0.2 s. A deck whose hidraw node is not ready inside that
~3 s window is dropped, and the fallback that used to catch it a second later may
now never fire.

The throttle itself is correct; the trigger should be a monotonic
"last full rescan" timestamp rather than "poll returned nothing".

Related, same function: `pyudev.Context()` / `Monitor.from_netlink()` sit outside
the loop's `try`, so a failure there (no netlink access in a container or a
confined session) propagates into `DeckController._listen`, which logs once and
lets the thread die. `_fallback_polling` only covers `ImportError`, so hotplug is
dead for the rest of the session with no retry.

### 5. Fixing udev permissions while the app runs never reconnects — **FIXED**

> Fixed after 0.10.2. A udev `change` uevent — exactly what `udevadm trigger`
> emits — is now delivered to a new `on_device_changed` callback instead of
> being dropped, and the controller answers it with `try_open()`, which already
> knew how to drop a non-functional handle and reopen. It early-returns when a
> working handle is already held, so reacting to every change event costs
> nothing; verified by firing real `change` events across the whole usb and
> hidraw subsystems with the deck connected, which produced no churn.
>
> Note the eviction-by-firmware approach was considered and rejected: the
> manager never opens devices itself (`auto_open=False`), so every cached entry
> has empty firmware and evicting on that basis would drop working devices.

The original problem, for reference:


`DeviceManager.py:43-55`, `DeviceManager.py:174`, `DeviceManager.py:292-293`,
`controller.py:187-195`, `main_window.py:816`, `actions.py:456-457`

A device that failed to open stays cached forever. `enumerate()` appends the
device object regardless of whether the open succeeded, `_add_missing_devices`
skips any path already in `streamdocks`, and `_handle_device_event` ignores every
action except `add`/`remove` — while `udevadm trigger`, which the README and our
own snap hint tell the user to run, emits `change`.

So: start the app before installing the udev rule, see "no device", install the
rule and reload udev, and the app never retries. The only in-app reconnect is
`try_open()`, reachable solely from the snap hint, and `snap_usb_hint()` returns
`None` outside a snap — so a .deb, PPA or source user has no reconnect path at
all. The "unplug and replug" line in the docs is what saves this today.

### 6. A permission failure looks identical to "no deck", and a half-open handle reports as connected — **FIXED**

> Fixed after 0.10.2. `_setup_device` now rejects a handle that opens but
> returns no firmware — the libusb false-connect — closing it and reporting not
> connected, matching what `try_open` already did. It also records a
> user-facing `controller.last_error`, which the status bar shows as
> "⚠ deck not usable" plus the reason, instead of the "○ no device" it used to
> show for an unplugged deck and a permission-blocked one alike.
> Covered by three tests in `tests/test_controller.py`.

The original problem, for reference:


`controller.py:190`, `controller.py:125`, `main_window.py:772-776`

The open failure is logged at WARNING to stderr only; the GUI shows the same
`○ no device` as an unplugged deck. Conversely `_setup_device` returns True even
when `dev.firmware_version` is empty — the "libusb false-connect" state that
`try_open` explicitly rejects. On that path the status bar reads `● connected fw=`
with every key dead and no retry anywhere.

### 7. GIF decoding runs on the Qt thread while holding the controller lock — **FIXED**

> After 0.10.2, `_read_gif` memoises its result, keyed on the file's path,
> mtime and size plus the target format, bounded to 6 entries. Measured on a
> 90-frame 400x400 GIF: **244.1 ms cold, 0.0 ms cached**. Editing an icon in
> place still invalidates correctly, and a failed decode is never cached.
>
> That removes the repeated cost, which is the common case — every page switch,
> profile switch, folder enter/exit and reconnect used to re-decode the same
> unchanged file in full.
>
> The first decode is now off the Qt thread too. `render_key` asks whether the
> decode is already cached; if not it queues the file for a dedicated worker
> and paints the key's static face for this pass, and the worker re-renders it
> once the decode lands. The worker holds no controller lock while decoding —
> `warm_key_gif` touches only the decode cache, never the device.
>
> Measured on the deck with a cold 90-frame 400x400 file: **render_key returns
> in 0.1 ms instead of ~244 ms**, and the key becomes animated a moment later.
>
> Guards, because this runs per key per render: a file already queued is not
> queued again, and a file that fails to decode is remembered so it is not
> retried forever — it falls through to the existing `rc < 0` handling instead.
> A device whose SDK lacks the new helpers behaves exactly as before.
>
> Twelve tests in `tests/test_sdk_shutdown.py`.

The original problem, for reference:


`controller.py:325`, `controller.py:309`, `GifController.py:262-306`

`set_key_gif` decodes and JPEG-encodes **every frame** on the calling thread with
no caching, redone in full on each render, and `render_key`/`render_page` are
called directly from the GUI thread in nine places.

Measured: 90 frames of a 400×400 GIF is 0.25 s of PIL work, scaling linearly with
size and length and multiplying with several animated keys on a page. Every page
switch, profile switch, folder enter/exit and reconnect freezes the window for
that long. Because `_lock` is held throughout, the SDK reader thread also blocks,
so key presses landing during a page switch are dispatched late by the same
amount.

### 8. Lower severity, same area — **FIXED**

> After 0.10.2:
> * **Double close — fixed.** `StreamDock.close()` is now serialised by a lock
>   and idempotent, so the concurrent `controller.stop()` / hotplug-remove race
>   destroys the transport exactly once. Tested single-threaded and with six
>   threads racing through a barrier.
> * **Stale frame — fixed.** `controller.stop()` now stops the animation
>   WORKER, not just the loop flag, before `clearAllIcon()`. The worker collects
>   its batch under its own lock and writes after releasing it, so a frame could
>   otherwise land after the clear and stay lit on the deck once the app was
>   gone.
> * **Unchecked write results — fixed.** `set_key_image_stream` returns `None`
>   when the transport has no handle (it returns early and writes nothing) and a
>   negative int on a C error; every caller discarded both. `render_key` now
>   reports it through `_note_write_result`, which logs the first failure of a
>   run and then counts rather than logging one line per key per render, and
>   says so when writes start landing again. Verified on the healthy deck that a
>   full 15-key render produces no false positives.

The original text:


- **Double close / double free.** Unplugging during quit lets `controller.stop()`
  and `_remove_device_by_path` call `close()` on the same object concurrently;
  neither `StreamDock.close` nor `LibUSBHIDAPI.close` takes a lock, so both can
  read a non-`None` handle and both call `transport_destroy`. Also, `stop()`
  always passes `notify=True`, so a deck removed without the udev event landing
  gets a disconnect packet written to a gone device — which the SDK's own comment
  warns "may terminate the process".
- **A stale frame can survive quit.** `stop()` calls `stop_gif_loop()` then
  `clearAllIcon()`, but the GIF worker collects its batch under the lock and
  writes after releasing it, so one frame can be painted after the clear and
  stay lit on the physical deck.
- **Write results are never checked.** `set_key_image_stream` returns a
  `TransportResult` that `set_key_image_pil` passes back and `render_key`
  discards. Nothing distinguishes "written" from "swallowed by a dead handle",
  which is what makes issues 1 and 6 invisible to the user.

### 9. ~2 s of every shutdown is spent inside `controller.stop()` — **CAUSE CONFIRMED, PARTLY MITIGATED**

> Traced after 0.10.2. Step-by-step profiling with the device free puts the
> entire cost in one place:
>
> | step | time |
> |---|---|
> | `_cancel_holds`, `_monitor_stop.set`, queue, `set_key_callback(None)` | 0.000 s |
> | `stop_gif_loop`, `clearAllIcon`, `refresh` | 0.000 s |
> | read-thread join | 0.093 s |
> | `disconnected()` packet | 0.000 s |
> | **`transport.close()`** | **2.001 s** |
>
> `transport.close()` is a single call into `transport_destroy` in the prebuilt
> `libtransport.so`. That is a **vendored binary blob**, so the 2.0 s cannot be
> removed without rebuilding someone else's library. The suspiciously round
> figure suggests an internal timeout rather than real work.
>
> Mitigated where we can: `MainWindow._quit` now hides the window and the tray
> icon and paints that before running the teardown, so quitting looks immediate
> instead of leaving a frozen window on screen for two seconds. The teardown
> still runs synchronously to completion — it has to, since it clears the key
> LCDs and releases the device.
>
> The original text below is superseded; the guess about `clearAllIcon()` was
> **wrong** — that call measures 0.000 s.

The original, incorrect speculation:


`controller.py` (`stop()`), and what it calls: `stop_gif_loop()`, `clearAllIcon()`

Found by profiling the fix for issue 3, so unlike the rest of this file it is a
**measurement, not a reading**. With the SDK's thread joins now effectively free,
the phase breakdown of a real shutdown on hardware is:

| phase | time |
|---|---|
| `gif_controller.close()` | 0.00 s |
| heartbeat join | 0.00 s |
| read thread join | 0.09 s |
| **rest of `controller.stop()`** | **2.05 s** |

That 2 s is the dominant remaining cost, and it runs on the Qt thread from
`MainWindow._quit`, so it is a visible freeze on every quit. The likely candidate
is `clearAllIcon()` writing all 15 key images over USB one at a time, plus the
monitor sampler shutdown, but that has **not** been confirmed — the profile
above only narrows it to "inside `stop()`". Profile it further before changing
anything.

It accounts for most, but not all, of the ~2.95 s a full quit now takes; the IPC
round trip and Qt teardown carry the rest and have not been attributed.

---

## Application layer

### Typing does nothing, silently, when no keystroke tool is installed

`actions.py:296-298`

`_type_text` returns silently when no keystroke tool is present, while the
parallel `_send_hotkey` path logs `no keystroke tool (install xdotool / ydotool /
wtype)`. A "Type text" or "Type password" key on a box without any of those does
nothing at all, with no log line and no GUI feedback.

### A locked keyring types an empty password with no indication

`actions.py:382-386`, `secret_store.py:60-62`

`secret_store.get` returns `None` both for "keyring missing" and "keyring
locked", and the warning only fires for a raised error, not a `None` return. With
the login keyring locked, pressing the key types an empty string and the user is
never told the secret was unavailable.

### Config export writes a plaintext password with no warning

`widgets.py:542`, `main_window.py:123-142`

When no keyring is available, the password is stored in `params["password"]` and
`Action.to_dict` copies params verbatim, so **Options → Export config** writes the
secret in the clear. The export is `0600`, which protects other local users, but
the whole point of an export is to move it to another machine or a backup, where
that mode does not follow it. No warning is shown at export time.

### `--enable-autostart` can report success without changing anything

`app.py:354-363`, `main_window.py:103`

The flags delegate to a running instance and print success as soon as the bytes
are written, without confirming. The receiving side calls
`autostart_act.setChecked(...)`, which emits `toggled` — and therefore writes or
removes the `.desktop` — only if the state actually changes; that state is a
snapshot taken once at window construction and never refreshed. If the file was
removed behind the GUI's back, `--enable-autostart` prints
`Signalled the running instance to update autostart.`, exits 0, and autostart
stays off.

---

## Packaging

### The vendored transport `.so` is unstripped

`lintian` reports `unstripped-binary-or-object` on
`fifine_deck/backend/StreamDock/Transport/TransportDLL/libtransport.so` for every
build. It is a prebuilt third-party binary, so stripping it is a decision about
someone else's artifact rather than a straightforward fix. Not an upload blocker.

### Launchpad references outliving the mirror — **RESOLVED**

The Launchpad git mirror was removed from `release.sh` after v0.10.0: SSH still
authenticates but the repository read fails, and it had drifted 16 commits
behind. It fed no publishing channel — the PPA is fed by `dput` and the snap
builds from GitHub — so nothing was lost with it.

The docs were left alone at the time pending a decision on whether the mirror
was dead or merely moved. Settled now: the **PPA is demonstrably live** (0.11.1
uploaded and accepted for noble, 0.11.1ppa1 for resolute), so every PPA
reference in `README.md` and `docs/PPA.md` is correct and stays. The one stale
item was the `code mirror` link in `README.md`, which pointed at a repository
roughly eighteen commits behind — removed.

---

## Verification status

Items 1-8 were each read out of the source and reasoned through, and several
were checked against the live process's open file descriptors and threads. They
have **not** been reproduced against physical hardware, because doing so means
deliberately provoking disconnects and use-after-free on a real device. Confirm
each on hardware before writing a fix, and treat the severity ordering here as a
starting point rather than a measurement.
