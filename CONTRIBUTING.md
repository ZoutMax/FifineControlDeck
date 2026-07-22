# Contributing

Thanks for helping improve **fifine Control Deck**!

## Development setup
- Python 3.10+ with system **PyQt6**, **Pillow** and **psutil**
  (`python3-pyqt6 python3-pil python3-psutil`) and **pyudev**. Without psutil
  the system-monitor sampler tests skip silently — install it so the full
  suite actually runs.
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
- **AppImage:** `./packaging/build-appimage.sh [version]` — see
  [`docs/APPIMAGE.md`](docs/APPIMAGE.md)
- **snap:** `snapcraft pack`
- **PPA source:** `debuild -S` — see [`docs/PPA.md`](docs/PPA.md)

## Releasing
`./release.sh <version> "what changed"` bumps the snap + deb version, tags, and
pushes to GitHub. The tag triggers the GitHub Release workflow (builds +
attaches the `.deb`s and the AppImage). Then promote the snap and `dput` the PPA.

Write the `## [<version>]` CHANGELOG section **first** — `release.sh` refuses to
run without it, and so does `tests/test_packaging.py`.

Run every gate before tagging, not just `pytest`. CI runs four, and two v0.10.0
attempts were published-blocked by changes that passed `pytest` alone:

    ruff check fifine_deck --exclude fifine_deck/backend --select E9,F63,F7,F82
    mypy fifine_deck                 # mypy.ini pins python_version = 3.10
    python -m pytest
    QT_QPA_PLATFORM=offscreen python .github/smoke_test.py

CI installs only `PyQt6 Pillow psutil pyudev ruff pytest pytest-timeout mypy`,
on Python 3.10-3.13. A test importing anything beyond that plus the stdlib
passes locally and fails in CI.

## Known issues
Open defects carried forward from the pre-0.10.0 audits — mostly in the vendored
SDK's threading — are written up in
[`docs/KNOWN-ISSUES.md`](docs/KNOWN-ISSUES.md) with file:line pointers,
reproduction notes and a verification-status caveat.

## Device reports
Only the **Stream Dock 293V3** (`3142:0060`) is hardware-verified. Other models
and knob/dial hardware are untested — if you have one, please open an issue with
`python3 probe_device.py` output. Device reports are very welcome!

## Conventions
- Match the surrounding style; keep **ruff** clean.
- Diagnostics go through **`logging`**, not `print()`.
- No AI-tool attribution in commits/PRs.
