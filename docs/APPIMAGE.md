# AppImage

A single self-contained file that runs on any glibc Linux without a package
manager. It exists because every other path this project ships builds a `.deb`,
which leaves Fedora, Arch, openSUSE and SteamOS users with no install route.

```bash
chmod +x fifine-control-deck-<version>-x86_64.AppImage
./fifine-control-deck-<version>-x86_64.AppImage
```

## Device access, the one manual step

The app talks to the deck over `/dev/hidraw*`, which needs a udev rule. Writing
that rule needs root, and an AppImage has no install step — so this is the one
thing it cannot do for you. Without it the app starts and shows
**"⚠ deck not usable"** with the reason, rather than pretending to be connected.

The rule travels inside the bundle. Install it once:

```bash
./fifine-control-deck-<version>-x86_64.AppImage --appimage-extract \
    usr/share/fifine-control-deck/70-fifine-deck.rules
sudo install -m644 \
    squashfs-root/usr/share/fifine-control-deck/70-fifine-deck.rules \
    /usr/lib/udev/rules.d/70-fifine-deck.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

No restart needed: the app watches for `change` uevents and picks up the new
permissions by itself.

If your distro uses the `plugdev` group rather than uaccess, also
`sudo usermod -aG plugdev "$USER"` and log back in.

## Optional helpers, not bundled

These are looked up on the host and each degrades gracefully with a log line
saying what is missing:

| tool | used for |
|---|---|
| `ydotool`, `xdotool` or `wtype` | hotkey and type-text keys |
| `playerctl` | media keys (MPRIS is tried first) |
| `wpctl` or `pactl` | volume keys |

Bundling them is deliberately avoided: they need their own daemons, permissions
and session integration, and a stale bundled copy would be worse than the host's.

## Building it

```bash
./packaging/build-appimage.sh [version]        # version defaults to debian/changelog
```

Produces `dist/fifine-control-deck-<version>-x86_64.AppImage`, about 57 MB.

The build starts from [python-appimage](https://github.com/niess/python-appimage)'s
manylinux CPython 3.12 — a relocatable interpreter that already works inside an
AppImage — pip-installs PyQt6, Pillow, psutil and pyudev into it, prunes Qt,
adds this package, and repacks with `appimagetool`. Downloads are cached in
`~/.cache/fifine-appimage`, so rebuilds are offline.

### Why the pruning matters

A stock PyQt6 wheel is ~260 MB because it ships every Qt module: WebEngine,
Quick, Quick3D, Designer, PDF, the lot. This app imports five —
`QtCore`, `QtGui`, `QtWidgets`, `QtNetwork`, `QtDBus`.

`packaging/appimage-prune.py` keeps those five and then computes the transitive
`DT_NEEDED` closure over them and over the plugins we ship, deleting every Qt
library nothing reaches. That takes site-packages from 293 MB to 127 MB and the
finished AppImage to 57 MB.

It reads `DT_NEEDED` with `readelf` rather than running `ldd`, and that is not a
style preference. `ldd` only prints a resolved path when the loader can actually
*find* the library, and the bundled Qt libs resolve through an RPATH that is not
in effect when `ldd` runs from outside the AppImage — so every line comes back
"not found", the closure computes as empty, and the script cheerfully deletes
all 109 Qt libraries. That happened during development. The script now also
refuses to prune if the closure returns fewer than five libraries, which is the
tripwire for that failure returning.

### Verifying a build

The AppImage should carry its own Python and Qt entirely:

```bash
./fifine-control-deck-*.AppImage &
grep -oE "/[^ ]*\.so[^ ]*" /proc/$(pgrep -f 'mount_fifine.*fifine_deck')/maps \
    | sort -u | grep -viE "^/tmp/\.mount_" | grep -iE "qt6|python"
```

That should print nothing. Anything it does print is a host library leaking into
the bundle, which is how AppImages break on machines other than the one that
built them.
