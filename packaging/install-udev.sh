#!/usr/bin/env bash
# Install the udev rule so the fifine Control Deck is usable without root.
# Run with: sudo ./install-udev.sh
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
install -m 0644 "$HERE/70-fifine-deck.rules" /etc/udev/rules.d/70-fifine-deck.rules

# Earlier versions installed this rule as 99-fifine-deck.rules, where its
# TAG+="uaccess" never fired (73-seat-late.rules dispatches uaccess at 73).
rm -f /etc/udev/rules.d/99-fifine-deck.rules
udevadm control --reload-rules
udevadm trigger
echo "Installed. Unplug and replug the fifine Control Deck now."
