#!/usr/bin/env bash
# Install fifine Control Deck from a .deb, letting apt pull in all
# dependencies (PyQt6, Pillow, …) and the optional helper tools automatically.
#
#   ./install.sh
#
# Works from a git clone (builds the .deb if there isn't one) and from an
# unpacked release (uses the .deb sitting next to this script).
set -e
cd "$(dirname "$0")"

ARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"

# Highest-versioned .deb for this arch, from a build tree or next to this
# script. build-deb.sh names them fifine-control-deck_<version>_<arch>.deb.
find_deb() {
    # Sort on the basename: sorting full paths would rank every dist/ file
    # before every ./ file ('d' < 'f'), regardless of version.
    ls -1 "dist/fifine-control-deck_"*"_${ARCH}.deb" \
          "fifine-control-deck_"*"_${ARCH}.deb" 2>/dev/null |
        awk -F/ '{ print $NF "\t" $0 }' | sort -V -k1,1 | tail -n1 | cut -f2
}

DEB="$(find_deb)"

# Take the version from debian/changelog: build-deb.sh defaults to 0.1.0, and
# apt would treat that as a downgrade of any real install. An unpacked release
# tarball has no debian/changelog, so VERSION is empty there and whatever .deb
# ships beside this script is used as-is.
VERSION="$(sed -n '1s/.*(\([^)]*\)).*/\1/p' debian/changelog 2>/dev/null | sed 's/ppa[0-9]*$//')"

# Rebuild when dist/ holds no .deb at all, AND when the newest one there is for
# some OTHER version than this tree's. Building only in the "none at all" case
# meant `git pull && ./install.sh` in a clone that had ever been built silently
# reinstalled the stale .deb: apt reported success and the user kept the old
# version, believing they had upgraded.
if [ -x packaging/build-deb.sh ] && [ -n "$VERSION" ] && \
   [ "${DEB##*/}" != "fifine-control-deck_${VERSION}_${ARCH}.deb" ]; then
    echo "Building $VERSION for $ARCH…"
    ./packaging/build-deb.sh "$VERSION" "$ARCH"
    DEB="$(find_deb)"
elif [ -z "$DEB" ] && [ -x packaging/build-deb.sh ]; then
    echo "No .deb here for $ARCH — building ${VERSION:-0.1.0}…"
    ./packaging/build-deb.sh "${VERSION:-0.1.0}" "$ARCH"
    DEB="$(find_deb)"
fi

if [ -z "$DEB" ]; then
    cat >&2 <<EOF
No .deb for this architecture ($ARCH), and none could be built here.

Install from the PPA instead:
    sudo add-apt-repository ppa:zoutmax/fifine
    sudo apt install fifine-control-deck

or download a .deb from:
    https://github.com/ZoutMax/FifineControlDeck/releases
EOF
    exit 1
fi

echo "Installing $DEB (apt will resolve dependencies automatically)…"
# 'apt install ./file.deb' installs the .deb AND its Depends + Recommends.
sudo apt install -y "./$DEB"

# The udev rule grants the user at the active seat an ACL (TAG+="uaccess"), so
# a normal desktop login needs nothing further. plugdev is the fallback for
# sessions logind doesn't own — e.g. over SSH, or on an unusual seat setup.
if ! id -nG "$USER" | grep -qw plugdev; then
    echo
    echo "Adding $USER to the 'plugdev' group (fallback for non-seat sessions)…"
    sudo usermod -aG plugdev "$USER" || true
    echo "Log out and back in (or reboot) for the group change to take effect."
fi

echo
echo "Done. Unplug/replug the device, then launch 'fifine Control Deck' from your menu."
