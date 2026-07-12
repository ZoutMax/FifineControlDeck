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
python3 flatpak-pip-generator PyQt6 Pillow pyudev
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
`--talk-name=org.freedesktop.Flatpak` (for `flatpak-spawn --host`). To make the
host-tool actions work cleanly, the action engine (`fifine_deck/actions.py`)
should prefer portals / MPRIS when running inside a sandbox
(detect `FLATPAK_ID`), falling back to `flatpak-spawn --host <tool>`.

## Submitting to Flathub
1. Fork <https://github.com/flathub/flathub> (the `new-pr` branch flow).
2. Add the manifest + metainfo, open a PR.
3. A reviewer runs the build and checks the metadata; iterate until green.
4. Once merged you get your own `flathub/io.github.zoutmax.FifineControlDeck`
   repo and the app appears on flathub.org.

## Note
The **Snap** already provides a zero-setup install on Ubuntu
(`snap install fifine-control-deck`) — Flathub is the equivalent for the wider
Flatpak ecosystem, worth doing once the confinement adaptation above is in place.
