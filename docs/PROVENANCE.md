# Vendored binary provenance

The USB transport library shipped under `fifine_deck/backend/StreamDock/` is a
**prebuilt** binary from the upstream MIT-licensed SDK — it is *not* built from
source in this repository. Its origin and checksums are recorded here for
auditability.

## Source
- Upstream: **MiraboxSpace/StreamDock-Device-SDK**
  — https://github.com/MiraboxSpace/StreamDock-Device-SDK
- License: **MIT** (see `fifine_deck/backend/StreamDock/LICENSE.vendor`)

## Files & checksums (SHA-256)
| File | Arch | SHA-256 |
|------|------|---------|
| `Transport/TransportDLL/libtransport.so` | amd64 (x86_64) | `ed072a26c2379259d2c7f53abb1070b830529741be9902d40833976353e14a3e` |
| `Transport/TransportDLL/libtransport_arm64.so` | arm64 (aarch64) | `1f35101b4efdfd304d4cec5cfd76188e29c3d04ba2af1e9a3495d974a9f2f45e` |

Verify locally:
```bash
sha256sum fifine_deck/backend/StreamDock/Transport/TransportDLL/libtransport*.so
```

## Local patches to the vendored SDK

The vendored tree is upstream MIT code with one deliberate change, kept
small and commented in place so it survives a future re-vendor:

- `StreamDock/DeviceManager.py`, `_listen_linux`: the hotplug poll timeout
  went from 1 s to 60 s. Upstream re-enumerates the whole USB HID bus on
  every idle second as a safety net behind pyudev; each scan costs ~105 ms,
  which measured as ~7% of a CPU core burned continuously by an idle app.
  pyudev events still arrive immediately (poll returns as soon as one does),
  so only the redundant rescan is throttled.

## Implications
Because these are prebuilt binaries with no in-repo build, the project ships via
community channels (Launchpad PPA and GitHub Releases today; Snap and Flatpak
packaging exists but their store submissions are parked). Building the
transport library from source would be a prerequisite for inclusion in the
Debian/Ubuntu **main** archives.
