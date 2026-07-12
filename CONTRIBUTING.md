# Contributing

Thanks for helping improve **fifine Control Deck**!

## Development setup
- Python 3.10+ with system **PyQt6** + **Pillow** (`python3-pyqt6 python3-pil`)
  and **pyudev**.
- Install the udev rule so the device works without `sudo`, then replug (you
  must be in the `plugdev` group):
  ```bash
  sudo ./packaging/install-udev.sh
  ```
- Run it: `./run.sh` (or `python3 -m fifine_deck`; `--headless` for no GUI).
- Turn up diagnostics with `FIFINE_LOG=DEBUG`.

## Tests, lint & types
```bash
python3 -m pytest                                   # unit + controller tests (offscreen, config-isolated)
ruff check fifine_deck --exclude fifine_deck/backend
mypy fifine_deck                                    # advisory
QT_QPA_PLATFORM=offscreen python3 .github/smoke_test.py
```
Tests never touch your real `~/.config` config (a fixture redirects it to a tmp
dir — please keep it that way).

## Packaging
- **.deb:** `./packaging/build-deb.sh <version> <amd64|arm64>`
- **snap:** `snapcraft pack`
- **PPA source:** `debuild -S` — see [`docs/PPA.md`](docs/PPA.md)
- **Flatpak:** see [`docs/FLATPAK.md`](docs/FLATPAK.md)

## Releasing
`./release.sh <version> "what changed"` bumps the snap + deb version, tags, and
pushes both remotes. The tag triggers the GitHub Release workflow (builds +
attaches the `.deb`s). Then promote the snap and `dput` the PPA.

## Device reports
Only the **Stream Dock 293V3** (`3142:0060`) is hardware-verified. Other models
and knob/dial hardware are untested — if you have one, please open an issue with
`python3 probe_device.py` output. Device reports are very welcome!

## Conventions
- Match the surrounding style; keep **ruff** clean.
- Diagnostics go through **`logging`**, not `print()`.
- No AI-tool attribution in commits/PRs.
