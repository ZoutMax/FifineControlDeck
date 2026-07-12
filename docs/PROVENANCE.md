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

## Implications
Because these are prebuilt binaries with no in-repo build, the project ships via
community channels (Snap, Launchpad PPA, GitHub Releases, Flatpak). Building the
transport library from source would be a prerequisite for inclusion in the
Debian/Ubuntu **main** archives.
