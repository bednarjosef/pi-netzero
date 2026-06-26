#!/bin/bash
# Swap the Pi-Tail USB-Ethernet gadget from the legacy g_ether driver to a
# CDC NCM-only gadget (built via configfs).
#
# Why: Pi-Tail loads g_ether from cmdline.txt, which exposes a dual-config
# gadget -- configuration #1 = RNDIS, configuration #2 = CDC. A Linux host
# quietly negotiates the CDC config so it "just works", but modern Android
# (Pixel etc.) grabs the RNDIS config first, has no RNDIS host driver, and so
# only ever shows "Charging connected device..." with no network interface.
# CDC NCM is supported natively by Android, Linux, macOS and Windows 10/11.
#
# Safety: g_ether stays in cmdline.txt as the boot fail-safe. This runs *after*
# boot to swap it out, and if the NCM gadget fails to bind a UDC it puts
# g_ether back -- so the (Linux) control link is never lost. Pair it with
# pi-tail-ncm.service to apply the swap automatically on every boot.
#
#   pi-tail-ncm.sh up    # g_ether -> CDC NCM
#   pi-tail-ncm.sh down  # CDC NCM -> g_ether (revert)
set -u

G=/sys/kernel/config/usb_gadget/pi_ncm
IP=192.168.42.254          # the Pi's own stable address for direct/laptop access

# Reuse the exact MACs Pi-Tail already uses so the host keeps the same link
# identity (interface name, DHCP lease) across the swap. Read them from the
# live g_ether interface / kernel cmdline, with sane fallbacks.
DEV_MAC=$(cat /sys/class/net/usb0/address 2>/dev/null || true)
HOST_MAC=$(sed -n 's/.*g_ether\.host_addr=\([0-9A-Fa-f:]\{17\}\).*/\1/p' /proc/cmdline)
[ -n "$DEV_MAC" ]  || DEV_MAC=3a:ea:66:54:bc:a2
[ -n "$HOST_MAC" ] || HOST_MAC=3a:ea:66:54:bc:a1

log(){ logger -t pi-tail-ncm "$*"; echo "[pi-tail-ncm] $*"; }

assign_ip(){
  local ifc="$1"
  ip addr add ${IP}/24 dev "$ifc" 2>/dev/null || true
  ip link set "$ifc" up 2>/dev/null || true
  # 192.168.42.254 is only the Pi's own stable address for direct/laptop access.
  # NO default route here: internet comes from the phone's Ethernet tethering,
  # whose DHCP lease (grabbed by pitail-uplink-monitor) installs the real default
  # route + gateway. We keep .254 alongside the lease so the Pi is always
  # reachable directly too. (No 'ip addr flush' so a live tether lease survives.)
}

build_ncm(){
  modprobe libcomposite 2>/dev/null || true
  [ -d "$G" ] && return 0
  mkdir -p "$G" || return 1
  cd "$G" || return 1
  echo 0x1d6b > idVendor       # Linux Foundation
  echo 0x0104 > idProduct      # Multifunction Composite Gadget
  echo 0x0100 > bcdDevice
  echo 0x0200 > bcdUSB
  mkdir -p strings/0x409
  echo "Pi-Tail"     > strings/0x409/manufacturer
  echo "Pi-Tail NCM" > strings/0x409/product
  echo "pitail0001"  > strings/0x409/serialnumber
  mkdir -p configs/c.1/strings/0x409
  echo "CDC NCM" > configs/c.1/strings/0x409/configuration
  echo 250       > configs/c.1/MaxPower
  mkdir -p functions/ncm.usb0
  echo "$HOST_MAC" > functions/ncm.usb0/host_addr
  echo "$DEV_MAC"  > functions/ncm.usb0/dev_addr
  ln -s functions/ncm.usb0 configs/c.1/
  local udc; udc=$(ls /sys/class/udc 2>/dev/null | head -1)
  [ -n "$udc" ] || return 1
  echo "$udc" > UDC || return 1
  return 0
}

teardown_ncm(){
  [ -d "$G" ] || return 0
  cd "$G" 2>/dev/null || return 0
  echo "" > UDC 2>/dev/null || true
  rm -f configs/c.1/ncm.usb0
  rmdir configs/c.1/strings/0x409 2>/dev/null || true
  rmdir configs/c.1 2>/dev/null || true
  rmdir functions/ncm.usb0 2>/dev/null || true
  rmdir strings/0x409 2>/dev/null || true
  cd /
  rmdir "$G" 2>/dev/null || true
}

restore_gether(){
  teardown_ncm
  modprobe g_ether host_addr="$HOST_MAC" dev_addr="$DEV_MAC" 2>/dev/null || true
  sleep 2
  assign_ip usb0
}

case "${1:-up}" in
  up)
    sleep 3                          # let the launching shell return first
    # USB role check: with no UDC the dwc2 port is in HOST mode (an OTG cable is
    # attached), so the Pi is the host and the phone supplies internet via USB
    # tethering. ifupdown's usb0 stanza (static 192.168.42.254, gateway
    # 192.168.42.129 -- "Android defaults") wires up that uplink, so we must NOT
    # build a gadget or poke g_ether here. Bow out and leave usb0 to ifupdown.
    if [ -z "$(ls /sys/class/udc 2>/dev/null)" ]; then
      log "no UDC -> USB host mode (OTG cable); phone provides the uplink via ifupdown. Skipping gadget swap."
      exit 0
    fi
    log "swapping g_ether -> CDC NCM (host=$HOST_MAC dev=$DEV_MAC)"
    rmmod g_ether 2>/dev/null || true
    sleep 1
    if build_ncm; then
      sleep 2
      ifc=$(cat "$G/functions/ncm.usb0/ifname" 2>/dev/null)
      [ -n "$ifc" ] || ifc=usb0
      assign_ip "$ifc"
      log "CDC NCM gadget up on $ifc ($IP)"
    else
      log "NCM build FAILED -> restoring g_ether"
      restore_gether
    fi
    ;;
  down)
    log "reverting CDC NCM -> g_ether"
    restore_gether
    ;;
  *)
    echo "usage: $0 up|down"; exit 1 ;;
esac
