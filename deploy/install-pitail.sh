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

echo "[*] System packages (python venv, iw, hcxtools for hash extraction)"
apt-get update -qq
apt-get install -y python3-venv python3-pip iw rsync hcxtools >/dev/null

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

# usb0 DHCP *server* (dnsmasq): installed but LEFT DISABLED on purpose. The phone
# reaches the Pi via Ethernet tethering, where the *phone* is the DHCP server +
# gateway; a second DHCP server on this link makes Android pick a conflicting
# subnet and nothing connects. The Pi instead runs a DHCP *client* (the uplink
# monitor) to accept the phone's address. Enable this only for direct PC use
# without a static IP:  sudo systemctl enable --now pi-netzero-usb-dhcp
echo "[*] Installing usb0 DHCP server config (left disabled — see note)"
install -m 644 "$APP/deploy/dnsmasq-usb0-pitail.conf" /etc/dnsmasq-usb0.conf
install -m 644 "$APP/deploy/pi-netzero-usb-dhcp.service" /etc/systemd/system/

# Pi-Tail's stock gadget is g_ether (RNDIS + CDC). Modern Android (Pixel etc.)
# can't drive the RNDIS config and shows only "Charging connected device...".
# Swap to a CDC NCM-only gadget after boot (g_ether stays the cmdline fail-safe).
echo "[*] Installing CDC NCM gadget swap (Android compatibility)"
install -m 755 "$APP/deploy/pi-tail-ncm.sh" /usr/local/sbin/pi-tail-ncm.sh
install -m 644 "$APP/deploy/pi-tail-ncm.service" /etc/systemd/system/

# Uplink monitor: pushes a ✅/⚠️ to ntfy when the Pi gains/loses internet (e.g.
# when you turn on Ethernet tethering on the phone), with the IP + gateway it got.
install -m 755 "$APP/deploy/pitail-uplink-monitor.sh" /usr/local/sbin/pitail-uplink-monitor.sh
install -m 644 "$APP/deploy/pitail-uplink-monitor.service" /etc/systemd/system/

# Force the USB-OTG port to peripheral. The Pi is always a USB *device* (the CDC
# NCM gadget above); internet comes back from the phone over Ethernet tethering,
# not USB host mode — a micro-USB↔USB-C cable can't ground the Pi's ID pin to make
# it a host anyway. Peripheral also avoids dr_mode=otg's VBUS sensing tearing down
# the gadget when a second 5V source (power bank) is attached.
BOOTCFG=/boot/firmware/config.txt
[ -f "$BOOTCFG" ] || BOOTCFG=/boot/config.txt
if grep -qE '^dtoverlay=dwc2(,dr_mode=otg)?$' "$BOOTCFG"; then
  echo "[*] Forcing dwc2 peripheral mode in $BOOTCFG (backup: $BOOTCFG.bak-pinetzero)"
  cp -a "$BOOTCFG" "$BOOTCFG.bak-pinetzero"
  sed -i -E 's/^dtoverlay=dwc2(,dr_mode=otg)?$/dtoverlay=dwc2,dr_mode=peripheral/' "$BOOTCFG"
fi

echo "[*] Installing + enabling the app service"
install -m 644 "$APP/deploy/pi-netzero-pitail.service" /etc/systemd/system/pi-netzero.service
systemctl daemon-reload
systemctl disable pi-netzero-usb-dhcp.service 2>/dev/null || true   # off: conflicts with Ethernet tethering
systemctl enable pi-netzero.service pi-tail-ncm.service pitail-uplink-monitor.service

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
