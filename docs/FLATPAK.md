# Flatpak / Flathub packaging

Scaffold for publishing on [Flathub](https://flathub.org) so users can
`flatpak install` on any distro with no PPA. Files live in `flatpak/`:

- `io.github.zoutmax.FifineControlDeck.yaml` — the manifest
- `io.github.zoutmax.FifineControlDeck.metainfo.xml` — AppStream metadata (required)
- `io.github.zoutmax.FifineControlDeck.desktop` — launcher
- `fifine-control-deck.launcher` — sets `PYTHONPATH` and runs `python3 -m fifine_deck`

**App ID:** `io.github.zoutmax.FifineControlDeck` (valid because you control
`zoutmax.github.io`). **Runtime:** `org.freedesktop.Platform 24.08` — the PyQt6
pip wheel bundles its own Qt6, so no KDE runtime needed.

## This is a scaffold — three things remain before it ships

### 1. Pin the Python deps for offline builds
Flathub builds have **no network**, so the `python3-deps` module must list every
wheel with a URL + sha256. Generate it:

```bash
pip install requirements-parser  # once
curl -O https://raw.githubusercontent.com/flatpak/flatpak-builder-tools/master/pip/flatpak-pip-generator
python3 flatpak-pip-generator PyQt6 Pillow psutil pyudev
# -> produces python3-modules.json; reference it from the manifest and drop the
#    inline `pip3 install` build-command.
```

### 2. Test-build locally
```bash
flatpak install flathub org.freedesktop.Platform//24.08 org.freedesktop.Sdk//24.08
flatpak-builder --user --install --force-clean build-dir \
  flatpak/io.github.zoutmax.FifineControlDeck.yaml
flatpak run io.github.zoutmax.FifineControlDeck
```
Validate before submitting:
```bash
flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest flatpak/io.github.zoutmax.FifineControlDeck.yaml
appstreamcli validate flatpak/io.github.zoutmax.FifineControlDeck.metainfo.xml
```

### 3. Confinement caveats (the real work)
Under Flatpak sandboxing, some actions need adapting — they rely on **host**
tools that aren't in the sandbox:

| Action | Host tool | Under Flatpak |
|--------|-----------|---------------|
| Device I/O | `libtransport.so` (bundled) | ✅ works with `--device=all` |
| Launch app / open URL | `xdg-open` | use the **OpenURI portal** or `flatpak-spawn --host` |
| Hotkey / type text | `ydotool` | needs `flatpak-spawn --host` (host must have ydotool) or the GlobalShortcuts portal |
| Media play/pause | `playerctl` | use **MPRIS** over D-Bus (`--talk-name=org.mpris.MediaPlayer2.*`) |
| Volume | `wpctl`/`pactl` | limited via the PulseAudio socket |

The manifest already grants `--device=all`, `--socket=pulseaudio`, and
`--talk-name=org.freedesktop.Flatpak` (for `flatpak-spawn --host`).

**Implemented:** `fifine_deck/actions.py` detects the sandbox (`/.flatpak-info`
or `FLATPAK_ID`) and automatically routes host helper tools **and** launched
apps/scripts through `flatpak-spawn --host` (it also probes tool availability on
the host, not the sandbox). `open_url` uses the runtime's portal-aware
`xdg-open`. Non-sandboxed builds are unaffected (the wrapper is a no-op).

**Remaining:** verify this end-to-end in a real `flatpak-builder` build, and
note the **host** must actually have `ydotool` / `playerctl` / `wpctl` installed
for those actions to work (they run on the host, outside the sandbox).

## Submitting to Flathub
1. Fork <https://github.com/flathub/flathub> (the `new-pr` branch flow).
2. Add the manifest + metainfo, open a PR.
3. A reviewer runs the build and checks the metadata; iterate until green.
4. Once merged you get your own `flathub/io.github.zoutmax.FifineControlDeck`
   repo and the app appears on flathub.org.

## Status (2026-07)
The Flathub submission is **parked**. The review raised two blockers: the app
needs `--talk-name=org.freedesktop.Flatpak` (host commands via `flatpak-spawn`)
which reviewers consider contrary to the point of sandboxing, and the project
was judged too young ("insufficient development history"). See the closed
[PR #9390](https://github.com/flathub/flathub/pull/9390).

Revisit once the project has a longer track record (~October 2026). Until
then the PPA and the `.deb` are the supported install paths; the Snap is
parked for the same maturity reason (see [`SNAP.md`](SNAP.md)).

**Verified by a real sandbox build (2026-07-20).** `flatpak-builder` build
of the 0.9.0 manifest, installed and exercised:

- effective permissions carry **no** `org.freedesktop.Flatpak` and **no**
  `org.freedesktop.secrets` (only the KDE runtime's own defaults);
- the Secret portal returns a master secret inside the sandbox with zero
  permissions requested, and the encrypted store round-trips (0600 file, no
  plaintext on disk) through both `portal_secret` and the `secret_store`
  chain;
- the bundled `cryptography` wheel imports (49.0.0, Python 3.13);
- the GUI constructs with all 15 keys, and without host access the app
  reports its degraded state instead of failing.

**Both technical reviewer points were addressed in 0.9.0:**

- `--talk-name=org.freedesktop.secrets` is gone: the "Type password" action
  now stores secrets through the **Secret portal**
  (`org.freedesktop.portal.Secret`, see `fifine_deck/portal_secret.py`),
  which needs no permission. The Flatpak bundles `cryptography` for the
  encrypted store; deb/PPA installs keep using SecretService, unchanged.
- `--talk-name=org.freedesktop.Flatpak` is gone from the default finish-args:
  the manifest is **portals-first**. Host-side actions (launch app, shell
  command, hotkeys, media/volume tools) detect the missing grant and print
  the exact enable line instead of failing silently.
- The launcher stays upstream (already the case: `flatpak/` in the app repo).

## Enabling host actions (user opt-in)

The whole point of a macro deck is controlling the host, so users who want
host-side actions grant it once, consciously:

```bash
flatpak override --user --talk-name=org.freedesktop.Flatpak \
    io.github.zoutmax.FifineControlDeck
```

(or turn on "Talk: org.freedesktop.Flatpak" in Flatseal), then restart the
app. Without the grant the app still runs, drives the deck, and every deck-
side action (pages, profiles, folders, brightness, monitor keys) works.
