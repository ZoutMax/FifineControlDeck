#!/usr/bin/env bash
# Install fifine Control Deck from the bundled .deb, letting apt pull in all
# dependencies (PyQt6, Pillow, …) and the optional helper tools automatically.
#
#   ./install.sh
#
set -e
cd "$(dirname "$0")"

ARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
DEB="dist/fifine-control-deck_latest_${ARCH}.deb"

if [ ! -f "$DEB" ]; then
    echo "No package for this architecture ($ARCH). Expected: $DEB"
    echo "Supported: amd64, arm64."
    exit 1
fi

echo "Installing $DEB (apt will resolve dependencies automatically)…"
# 'apt install ./file.deb' installs the .deb AND its Depends + Recommends.
sudo apt install -y "./$DEB"

# Make sure the current user can access the device without root.
if ! id -nG "$USER" | grep -qw plugdev; then
    echo
    echo "Adding $USER to the 'plugdev' group (needed for device access)…"
    sudo usermod -aG plugdev "$USER"
    echo "Log out and back in (or reboot) for the group change to take effect."
fi

echo
echo "Done. Unplug/replug the device, then launch 'fifine Control Deck' from your menu."
