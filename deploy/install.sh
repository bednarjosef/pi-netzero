#!/bin/bash
# One-shot installer. Run on the Pi (Kali for the Pi Zero 2 W, which ships the
# Nexmon firmware needed for onboard monitor mode):
#
#   sudo deploy/install.sh
#
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo deploy/install.sh)"; exit 1; }

SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/pi-netzero

echo "[*] Installing pi-netzero -> $APP"
mkdir -p "$APP"
rsync -a --delete --exclude '.git' --exclude '.venv' --exclude 'captures' "$SRC"/ "$APP"/
chmod +x "$APP/deploy/usb_gadget.sh"

echo "[*] System packages (python venv, dnsmasq, iw, rsync)"
apt-get update -qq
apt-get install -y python3-venv python3-pip dnsmasq iw rsync >/dev/null

echo "[*] Python virtualenv + deps"
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --upgrade pip >/dev/null
"$APP/.venv/bin/pip" install -r "$APP/requirements.txt"

# --- USB gadget: enable dwc2 in peripheral mode + load libcomposite ----------
BOOT=/boot/firmware
[ -d "$BOOT" ] || BOOT=/boot
CONFIG="$BOOT/config.txt"
CMDLINE="$BOOT/cmdline.txt"

echo "[*] Enabling dwc2 peripheral mode ($CONFIG / $CMDLINE)"
grep -q "dtoverlay=dwc2" "$CONFIG" || echo "dtoverlay=dwc2,dr_mode=peripheral" >> "$CONFIG"
if ! grep -q "modules-load=dwc2" "$CMDLINE"; then
  # append to the single kernel cmdline line
  sed -i 's/\brootwait\b/rootwait modules-load=dwc2/' "$CMDLINE" \
    || sed -i 's/$/ modules-load=dwc2/' "$CMDLINE"
fi

# Our gadget script runs its own dnsmasq bound to usb0; disable the system one
# so the two don't fight over the interface.
systemctl disable --now dnsmasq 2>/dev/null || true

echo "[*] Installing + enabling systemd services"
install -m 644 "$APP/deploy/pi-netzero-usb.service" /etc/systemd/system/
install -m 644 "$APP/deploy/pi-netzero.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable pi-netzero-usb.service pi-netzero.service

cat <<'EOF'

[✓] pi-netzero installed.

Finish up:
  1. Reboot so dwc2 loads:                 sudo reboot
  2. Power the Pi from a power bank via the PWR port; run a data cable from the
     Pi's USB port (inner, labelled "USB") to your Android phone.
  3. The phone auto-detects a wired/RNDIS link. Open a browser to:
                                           http://10.55.0.1
  4. Sanity-check onboard monitor mode (Kali + Nexmon):
                                           sudo iw dev wlan0 set type monitor

Watch logs:   journalctl -u pi-netzero -f
EOF
