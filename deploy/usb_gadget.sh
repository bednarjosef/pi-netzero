#!/bin/bash
# USB-Ethernet (RNDIS) gadget for the Raspberry Pi Zero 2 W.
#
# Presents the Pi to an Android phone as a USB network device over a single
# cable, so the phone reaches pi-netzero at http://10.55.0.1 while the onboard
# Wi-Fi radio stays dedicated to monitor mode.
#
# Usage: usb_gadget.sh up | down
set -e

G=/sys/kernel/config/usb_gadget/pi_netzero
IFACE_IP=10.55.0.1
DNSMASQ_CONF=/opt/pi-netzero/deploy/dnsmasq-usb0.conf
DNSMASQ_PID=/run/pi-netzero-dnsmasq.pid

# Locally-administered MACs (even first octet = unicast/locally-administered).
HOST_MAC=42:63:66:31:01:02   # phone side of the link
DEV_MAC=42:63:66:31:01:01    # Pi side of the link

up() {
  if [ -d "$G" ]; then
    echo "pi-netzero gadget already configured."
    exit 0
  fi

  modprobe libcomposite
  mkdir -p "$G"; cd "$G"

  echo 0x1d6b > idVendor      # Linux Foundation
  echo 0x0104 > idProduct     # Multifunction Composite Gadget
  echo 0x0100 > bcdDevice
  echo 0x0200 > bcdUSB

  mkdir -p strings/0x409
  echo "pi-netzero"     > strings/0x409/manufacturer
  echo "pi-netzero USB" > strings/0x409/product
  echo "0001"           > strings/0x409/serialnumber

  mkdir -p configs/c.1/strings/0x409
  echo "RNDIS" > configs/c.1/strings/0x409/configuration
  echo 250     > configs/c.1/MaxPower

  # RNDIS function — the protocol Android understands.
  mkdir -p functions/rndis.usb0
  echo "$HOST_MAC" > functions/rndis.usb0/host_addr
  echo "$DEV_MAC"  > functions/rndis.usb0/dev_addr

  # Microsoft OS descriptors so Windows also auto-binds RNDIS (no-op for Android).
  echo 1       > os_desc/use
  echo 0xcd    > os_desc/b_vendor_code
  echo MSFT100 > os_desc/qw_sign
  echo RNDIS   > functions/rndis.usb0/os_desc/interface.rndis/compatible_id
  echo 5162001 > functions/rndis.usb0/os_desc/interface.rndis/sub_compatible_id

  ln -s functions/rndis.usb0 configs/c.1/
  ln -s configs/c.1 os_desc

  # Bind to the USB device controller -> gadget goes live.
  ls /sys/class/udc > UDC

  # Bring up the link and hand the phone a DHCP lease.
  ip addr add ${IFACE_IP}/24 dev usb0
  ip link set usb0 up
  dnsmasq --conf-file="$DNSMASQ_CONF" --pid-file="$DNSMASQ_PID"
  echo "pi-netzero gadget up at http://${IFACE_IP}"
}

down() {
  [ -f "$DNSMASQ_PID" ] && kill "$(cat "$DNSMASQ_PID")" 2>/dev/null || true
  rm -f "$DNSMASQ_PID"
  [ -d "$G" ] || exit 0
  cd "$G"
  echo "" > UDC 2>/dev/null || true
  rm -f os_desc/c.1
  rm -f configs/c.1/rndis.usb0
  rmdir configs/c.1/strings/0x409 2>/dev/null || true
  rmdir configs/c.1 2>/dev/null || true
  rmdir functions/rndis.usb0/os_desc/interface.rndis 2>/dev/null || true
  rmdir functions/rndis.usb0 2>/dev/null || true
  rmdir strings/0x409 2>/dev/null || true
  cd /
  rmdir "$G" 2>/dev/null || true
  echo "pi-netzero gadget down."
}

case "${1:-}" in
  up)   up ;;
  down) down ;;
  *)    echo "usage: $0 up|down"; exit 1 ;;
esac
