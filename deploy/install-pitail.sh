#!/bin/bash
# App-only installer for the Pi-Tail image.
#
# Pi-Tail already provides USB-gadget phone control + Nexmon monitor mode
# (`mon0up`), so this installs ONLY the pi-netzero app + its service — it does
# NOT touch USB gadget / dwc2 / dnsmasq (that would fight Pi-Tail).
#
#   sudo deploy/install-pitail.sh
#
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo deploy/install-pitail.sh)"; exit 1; }

SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/pi-netzero

echo "[*] Installing pi-netzero (app only) -> $APP"
mkdir -p "$APP"
rsync -a --delete --exclude '.git' --exclude '.venv' --exclude 'captures' "$SRC"/ "$APP"/

echo "[*] System packages (python venv, iw)"
apt-get update -qq
apt-get install -y python3-venv python3-pip iw rsync >/dev/null

echo "[*] Python virtualenv + deps"
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --upgrade pip >/dev/null
"$APP/.venv/bin/pip" install -r "$APP/requirements.txt"

# Sanity-check Pi-Tail's monitor helper is present.
if command -v mon0up >/dev/null 2>&1; then
  echo "[*] Found mon0up: $(command -v mon0up)"
else
  echo "[!] WARNING: 'mon0up' not found on PATH. This installer expects the"
  echo "    Pi-Tail image. On a plain Kali image use deploy/install.sh instead."
fi

# CRITICAL: Pi-Tail associates wlan0 with the 'sepultura' hotspot at boot. On
# the Zero 2 W's Broadcom chip that station-mode association crashes the Wi-Fi
# firmware (the SDIO card drops off until reboot) and monitor mode never works.
# Disable the wlan0 station stanza so the chip stays alive for monitor mode.
IFACES=/boot/firmware/interfaces
if [ -f "$IFACES" ] && grep -q '^allow-hotplug wlan0' "$IFACES"; then
  echo "[*] Disabling wlan0 station mode (it crashes the Zero 2 W Wi-Fi firmware)"
  cp "$IFACES" "$IFACES.pre-pinetzero"
  sed -i '/^allow-hotplug wlan0/,+3 s/^/#/' "$IFACES"
fi
systemctl mask wpa_supplicant >/dev/null 2>&1 || true

echo "[*] Installing usb0 DHCP server (so a plugged-in phone/PC auto-gets an IP)"
# Pi-Tail runs no DHCP on usb0, so connected devices never get an address and
# can't reach the UI. This serves one, bound to usb0 only.
install -m 644 "$APP/deploy/dnsmasq-usb0-pitail.conf" /etc/dnsmasq-usb0.conf
install -m 644 "$APP/deploy/pi-netzero-usb-dhcp.service" /etc/systemd/system/

echo "[*] Installing + enabling the app service"
install -m 644 "$APP/deploy/pi-netzero-pitail.service" /etc/systemd/system/pi-netzero.service
systemctl daemon-reload
systemctl enable pi-netzero.service pi-netzero-usb-dhcp.service

cat <<'EOF'

[✓] pi-netzero installed for Pi-Tail.

Start it now (or reboot — it auto-starts):
    sudo systemctl start pi-netzero

From your phone (already connected to Pi-Tail over USB), open a browser to:
    http://<pi-tail-ip>:8080
The IP is the same one you SSH to Pi-Tail on — check it with:  ip a show usb0

The app brings up monitor mode via Pi-Tail's `mon0up` automatically.

Watch logs:   journalctl -u pi-netzero -f
EOF
